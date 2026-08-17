"""Stage 4 — verify and hand off.

The manifest is generated **after** the contributor's private review, from
whatever remains on the drive. That ordering is a privacy guarantee, not an
implementation detail:

    A review pass is only honest if declining to share something is invisible.

So nothing anywhere records what was originally selected. There is no
export-time manifest to diff against. Orphaned sidecars — metadata files whose
photo was deleted during review — are removed silently, and their count is
never reported, because "37 items were removed" is itemization with the
filenames stripped off.

If you are maintaining this file and feel an urge to add a helpful log line
about cleaned-up orphans, that urge is the bug.
"""

from __future__ import annotations

import csv
import shutil
from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from mediafiles import (
    MEDIA_EXTENSIONS,
    SIDECAR_EXTENSIONS,
    SYSTEM_DIRECTORIES,
    read_sidecar_metadata,
    sha256_file,
    sidecar_candidates,
)
from mediafiles import iter_media as _iter_media

__all__ = [
    "MANIFEST_COLUMNS",
    "MANIFEST_NAME",
    "MEDIA_EXTENSIONS",
    "SIDECAR_EXTENSIONS",
    "ManifestError",
    "ManifestRow",
    "VerifyResult",
    "build_manifest",
    "iter_media",
    "read_manifest",
    "read_sidecar_metadata",
    "purge_deleted_items",
    "remove_orphaned_sidecars",
    "sha256_file",
    "sidecar_candidates",
    "verify_manifest",
    "write_manifest",
]

MANIFEST_NAME = "manifest.csv"

MANIFEST_COLUMNS = ["path", "date", "albums", "persons", "size_bytes", "sha256"]


class ManifestError(Exception):
    """Raised when a manifest cannot be built or verified."""


@dataclass(frozen=True)
class ManifestRow:
    path: str
    date: str
    albums: str
    persons: str
    size_bytes: int
    sha256: str


def iter_media(root: Path) -> Iterator[Path]:
    """Every media file on the drive, excluding the manifest itself."""
    return _iter_media(root, skip_names={MANIFEST_NAME})


def remove_orphaned_sidecars(root: Path) -> None:
    """Delete sidecars whose media file no longer exists.

    Returns nothing, on purpose. See this module's docstring: the number of
    orphans is the number of items the contributor removed during review, and
    that figure does not get surfaced, logged, or returned to a caller.
    """
    media_stems = set()
    for media in iter_media(root):
        media_stems.add(media)
        media_stems.add(media.with_suffix(""))

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.casefold() not in SIDECAR_EXTENSIONS:
            continue
        # IMG_1234.jpg.json -> IMG_1234.jpg ; IMG_1234.json -> IMG_1234
        if path.with_suffix("") in media_stems:
            continue
        # A sidecar we can't delete is not worth failing the handoff over.
        with suppress(OSError):
            path.unlink()


def purge_deleted_items(root: Path) -> None:
    """Permanently remove the drive's trash and system directories.

    Deleting a file from an external drive in the macOS Finder moves it to
    ``.Trashes`` on that drive rather than removing it. Without this, every
    photo the contributor deleted during their review would still be on the
    drive when it changed hands — and would be catalogued and imported at the
    other end. Emptying the trash is what they meant by deleting it.

    Silent and unconditional, like ``remove_orphaned_sidecars``: it runs whether
    or not anything was deleted, and reports nothing. The number of items in the
    trash *is* the number of items withheld, so surfacing it — even as a bare
    count — would itemize exactly what this kit promises never to record.
    """
    for name in sorted(SYSTEM_DIRECTORIES):
        for candidate in root.iterdir() if root.is_dir() else []:
            if candidate.is_dir() and candidate.name.casefold() == name:
                shutil.rmtree(candidate, ignore_errors=True)


def build_manifest(root: Path, clean_orphans: bool = True) -> list[ManifestRow]:
    """Walk the reviewed drive and produce a manifest of what remains."""
    resolved = root.expanduser()
    if not resolved.is_dir():
        raise ManifestError(f"Not a directory: {resolved}")

    if clean_orphans:
        purge_deleted_items(resolved)
        remove_orphaned_sidecars(resolved)

    rows: list[ManifestRow] = []
    for media in iter_media(resolved):
        metadata = read_sidecar_metadata(media)
        rows.append(
            ManifestRow(
                path=media.relative_to(resolved).as_posix(),
                date=metadata.get("date", ""),
                albums=metadata.get("albums", ""),
                persons=metadata.get("persons", ""),
                size_bytes=media.stat().st_size,
                sha256=sha256_file(media),
            )
        )
    return rows


def write_manifest(rows: list[ManifestRow], root: Path) -> Path:
    path = root.expanduser() / MANIFEST_NAME
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "path": row.path,
                    "date": row.date,
                    "albums": row.albums,
                    "persons": row.persons,
                    "size_bytes": row.size_bytes,
                    "sha256": row.sha256,
                }
            )
    return path


def read_manifest(root: Path) -> list[ManifestRow]:
    path = root.expanduser() / MANIFEST_NAME
    if not path.is_file():
        raise ManifestError(
            f"No {MANIFEST_NAME} in {root}. Run the manifest step before verifying."
        )
    with path.open(newline="", encoding="utf-8") as handle:
        return [
            ManifestRow(
                path=row["path"],
                date=row.get("date", ""),
                albums=row.get("albums", ""),
                persons=row.get("persons", ""),
                size_bytes=int(row.get("size_bytes") or 0),
                sha256=row["sha256"],
            )
            for row in csv.DictReader(handle)
        ]


@dataclass
class VerifyResult:
    checked: int = 0
    ok: int = 0
    corrupted: list[str] = None  # type: ignore[assignment]
    missing: list[str] = None  # type: ignore[assignment]
    unlisted: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.corrupted = self.corrupted or []
        self.missing = self.missing or []
        self.unlisted = self.unlisted or []

    @property
    def passed(self) -> bool:
        return not self.corrupted and not self.missing and not self.unlisted


def verify_manifest(root: Path) -> VerifyResult:
    """Re-checksum every file against the manifest.

    This is the check that runs on the second copy, and again after the drive
    travels. Bit rot and truncated copies are silent otherwise.
    """
    resolved = root.expanduser()
    rows = read_manifest(resolved)
    result = VerifyResult()

    listed = {row.path for row in rows}
    for row in rows:
        target = resolved / row.path
        result.checked += 1
        if not target.is_file():
            result.missing.append(row.path)
            continue
        if sha256_file(target) == row.sha256:
            result.ok += 1
        else:
            result.corrupted.append(row.path)

    for media in iter_media(resolved):
        relative = media.relative_to(resolved).as_posix()
        if relative not in listed:
            result.unlisted.append(relative)

    return result
