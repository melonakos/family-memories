"""Filing originals into the vault.

The vault is append-only. Every function here either creates a new file or
raises; none of them opens an existing vault file for writing, and none should
ever be added that does. That is the whole guarantee of Tier 1 — if it can be
violated by a code path, it isn't a guarantee.

Filing is copy, verify, then report. The verify step re-reads what was written
and compares checksums, because a copy that silently truncates is exactly the
failure an archive is supposed to survive.
"""

from __future__ import annotations

import shutil
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mediafiles import sha256_file

# Length of the disambiguating suffix added when two different files want the
# same name in the same month. Eight hex characters of SHA-256 is ample for
# per-directory uniqueness and stays readable in a filename.
SUFFIX_LENGTH = 8

UNDATED_DIRECTORY = "undated"


class VaultError(Exception):
    """Raised when a file cannot be filed safely."""


@dataclass(frozen=True)
class FiledAsset:
    vault_path: Path
    relative_path: str
    sha256: str
    filesize: int
    renamed: bool
    """True when a name collision forced a disambiguating suffix."""


def month_directory(taken_at: datetime | None) -> str:
    """``YYYY/MM``, or an ``undated`` bucket.

    Undated files still get archived — they are just parked somewhere obvious
    and flagged for review, rather than filed under a guessed date. Moving one
    later means a new copy, since the vault never renames.
    """
    if taken_at is None:
        return UNDATED_DIRECTORY
    return f"{taken_at.year:04d}/{taken_at.month:02d}"


def target_path(vault_root: Path, source: Path, taken_at: datetime | None, sha256: str) -> Path:
    """Where a file belongs, disambiguating only if the name is already taken.

    The suffix derives from the content hash, so the same file always lands at
    the same path — reruns are stable and the layout is reproducible.
    """
    directory = vault_root / month_directory(taken_at)
    candidate = directory / source.name
    if not candidate.exists():
        return candidate

    if candidate.is_file() and sha256_file(candidate) == sha256:
        # Byte-identical file already filed here. Callers dedupe before filing,
        # so reaching this means a rerun after an interrupted write.
        return candidate

    stem, suffix = source.stem, source.suffix
    return directory / f"{stem}-{sha256[:SUFFIX_LENGTH]}{suffix}"


def file_asset(
    source: Path,
    vault_root: Path,
    taken_at: datetime | None,
    sha256: str | None = None,
    dry_run: bool = False,
) -> FiledAsset:
    """Copy a file into the vault and verify it arrived intact.

    Never overwrites. Never modifies the source. Raises VaultError rather than
    resolving any ambiguity on its own.
    """
    src = Path(source)
    if not src.is_file():
        raise VaultError(f"Not a file: {src}")

    digest = sha256 or sha256_file(src)
    size = src.stat().st_size
    destination = target_path(Path(vault_root), src, taken_at, digest)
    relative = destination.relative_to(vault_root).as_posix()

    if destination.exists():
        # Only tolerable when it is already the same bytes; anything else means
        # the caller is about to lose data.
        if sha256_file(destination) != digest:
            raise VaultError(
                f"Refusing to overwrite {destination}: it holds different content. "
                f"The vault is append-only."
            )
        return FiledAsset(destination, relative, digest, size, renamed=destination.name != src.name)

    if dry_run:
        return FiledAsset(destination, relative, digest, size, renamed=destination.name != src.name)

    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        # copy2 preserves timestamps, which are evidence even when they are not
        # trusted as the authoritative date.
        shutil.copy2(src, destination)
    except OSError as exc:
        raise VaultError(f"Could not copy {src} to {destination}: {exc}") from exc

    written = sha256_file(destination)
    if written != digest:
        # A bad copy must not be left behind looking like an archived original.
        with suppress(OSError):
            destination.unlink()
        raise VaultError(
            f"Copy of {src.name} does not match its source checksum and was removed. "
            f"Expected {digest[:12]}, got {written[:12]}. Check the destination media."
        )

    return FiledAsset(destination, relative, digest, size, renamed=destination.name != src.name)
