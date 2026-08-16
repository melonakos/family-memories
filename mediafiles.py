"""Shared primitives for identifying and reading media files on disk.

A leaf module, like ``settings``: no package allegiance, no heavy imports, so
both the contribution kit and the ingest pipeline can use it without either
depending on the other. Hashing and file identification are the same problem in
both places and there should be exactly one implementation.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator
from pathlib import Path

CHUNK_SIZE = 1024 * 1024

PHOTO_EXTENSIONS = frozenset(
    {
        ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp",
        ".heic", ".heif", ".dng", ".raw", ".cr2", ".cr3", ".nef", ".arw",
        ".orf", ".rw2",
    }
)
VIDEO_EXTENSIONS = frozenset(
    {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".mts", ".m2ts", ".3gp"}
)
MEDIA_EXTENSIONS = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS

# Formats a perceptual hash can be computed for. Raw files and HEIC need
# decoders Pillow may not have, so near-duplicate detection skips them rather
# than guessing — they still get exact-hash dedupe.
HASHABLE_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp"}
)

SIDECAR_EXTENSIONS = frozenset({".xmp", ".json", ".aae"})

PHOTO = "photo"
VIDEO = "video"


def media_type(path: Path) -> str:
    """``"photo"`` or ``"video"``, by extension."""
    return VIDEO if path.suffix.casefold() in VIDEO_EXTENSIONS else PHOTO


def is_media(path: Path) -> bool:
    return path.suffix.casefold() in MEDIA_EXTENSIONS


def can_perceptual_hash(path: Path) -> bool:
    return path.suffix.casefold() in HASHABLE_EXTENSIONS


def sha256_file(path: Path) -> str:
    """Streaming SHA-256. Files here are routinely gigabytes."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def iter_media(root: Path, skip_names: Iterable[str] = ()) -> Iterator[Path]:
    """Every media file under ``root``, sorted, ignoring sidecars and OS cruft.

    Sorted so that runs are deterministic and reports are comparable between
    machines.
    """
    skip = {str(name) for name in skip_names}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.name in skip:
            continue
        if is_media(path):
            yield path


def sidecar_candidates(media: Path) -> list[Path]:
    """Sidecar paths that may accompany a media file.

    osxphotos writes ``IMG_1234.jpg.json`` by default and ``IMG_1234.json``
    when the original extension is dropped. Both spellings are checked.
    """
    candidates: list[Path] = []
    for extension in (".json", ".xmp", ".aae"):
        candidates.append(media.with_suffix(media.suffix + extension))
        candidates.append(media.with_suffix(extension))
    return candidates


def read_sidecar_metadata(media: Path) -> dict[str, str]:
    """Pull date, albums, and persons out of a JSON sidecar, if one exists.

    Best-effort by design: a missing or unparseable sidecar yields empty fields
    rather than an error. The file itself and its checksum are what matter, and
    the same metadata is generally embedded in the media too.
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
