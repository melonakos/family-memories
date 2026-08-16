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
import hashlib
import json
from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

MANIFEST_NAME = "manifest.csv"
CHUNK_SIZE = 1024 * 1024

MEDIA_EXTENSIONS = frozenset(
    {
        ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp",
        ".heic", ".heif", ".dng", ".raw", ".cr2", ".cr3", ".nef", ".arw",
        ".orf", ".rw2", ".mov", ".mp4", ".m4v", ".avi", ".mkv", ".mts",
        ".m2ts", ".3gp",
    }
)
SIDECAR_EXTENSIONS = frozenset({".xmp", ".json", ".aae"})

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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def iter_media(root: Path) -> Iterator[Path]:
    """Every media file under ``root``, sorted, ignoring sidecars and OS cruft."""
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.name == MANIFEST_NAME:
            continue
        if path.suffix.casefold() in MEDIA_EXTENSIONS:
            yield path


def sidecar_candidates(media: Path) -> list[Path]:
    """Sidecar paths osxphotos may have written for a media file.

    It writes ``IMG_1234.jpg.json`` by default, or ``IMG_1234.json`` when the
    original extension is dropped. Both spellings are checked.
    """
    candidates: list[Path] = []
    for extension in (".json", ".xmp", ".aae"):
        candidates.append(media.with_suffix(media.suffix + extension))
        candidates.append(media.with_suffix(extension))
    return candidates


def read_sidecar_metadata(media: Path) -> dict[str, str]:
    """Pull date, albums, and persons out of a JSON sidecar, if one survives.

    Best-effort by design. A missing or unparseable sidecar yields empty
    fields rather than an error: the checksum and the file itself are what
    matter for handoff, and metadata is also embedded in the file and the XMP.
    """
    for candidate in sidecar_candidates(media):
        if candidate.suffix.casefold() != ".json" or not candidate.is_file():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            "date": str(data.get("date") or ""),
            "albums": "; ".join(str(a) for a in data.get("albums") or []),
            "persons": "; ".join(str(p) for p in data.get("persons") or []),
        }
    return {}


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


def build_manifest(root: Path, clean_orphans: bool = True) -> list[ManifestRow]:
    """Walk the reviewed drive and produce a manifest of what remains."""
    resolved = root.expanduser()
    if not resolved.is_dir():
        raise ManifestError(f"Not a directory: {resolved}")

    if clean_orphans:
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
