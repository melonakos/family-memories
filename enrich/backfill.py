"""Re-reading metadata from files already in the vault.

The index claims to be reconstructible from the vault plus sidecars. This is
where that claim gets exercised rather than asserted: assets archived before a
field existed can have it filled in by reading the originals again.

It is also the ordinary repair path. If a schema migration adds a column, or a
metadata bug is found and fixed, the fix is to re-read — not to re-ingest, and
certainly not to touch the vault. Originals are never modified here; only the
index is written to.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from index.db import Index
from index.models import GPS_FROM_EXIF
from ingest.metadata import metadata_for, read_exiftool

BATCH_SIZE = 200


@dataclass
class BackfillResult:
    examined: int = 0
    located: int = 0
    still_unlocated: int = 0
    missing_files: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.missing_files = self.missing_files or []


def backfill_locations(
    vault_root: Path,
    index: Index,
    dry_run: bool = False,
) -> BackfillResult:
    """Read GPS from vault originals for assets that have none recorded.

    Only fills gaps. An asset that already has coordinates is left alone,
    whether they were recorded or inferred — re-deriving a value the archive
    already holds is how a careful correction gets undone.
    """
    root = Path(vault_root).expanduser()
    result = BackfillResult()

    candidates = [a for a in index.assets_missing_location()]
    # Also consider undated assets, which assets_missing_location() excludes
    # because they can never be a location-inference target.
    located_ids = {a.id for a in index.assets_with_location()}
    candidates += [
        a for a in index.assets() if a.taken_at is None and a.id not in located_ids
    ]

    for start in range(0, len(candidates), BATCH_SIZE):
        batch = candidates[start : start + BATCH_SIZE]
        paths: list[Path] = []
        by_path: dict[str, int] = {}

        for asset in batch:
            target = root / asset.vault_path
            if not target.is_file():
                result.missing_files.append(asset.vault_path)
                continue
            paths.append(target)
            by_path[str(target.resolve())] = asset.id

        if not paths:
            continue

        exif = read_exiftool(paths)
        for path in paths:
            asset_id = by_path[str(path.resolve())]
            result.examined += 1
            meta = metadata_for(path, exif.get(str(path.resolve())))
            if not meta.has_location:
                result.still_unlocated += 1
                continue
            result.located += 1
            if not dry_run:
                index.set_location(
                    asset_id,
                    meta.gps_latitude,  # type: ignore[arg-type]
                    meta.gps_longitude,  # type: ignore[arg-type]
                    source=GPS_FROM_EXIF,
                )

    return result
