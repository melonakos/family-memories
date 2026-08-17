"""Attaching person tags to archived assets.

Person tags are the most valuable metadata in the archive. The selection engine
enforces per-child quotas from them, which is what guarantees a blended family's
wall is balanced rather than dominated by whoever was photographed most. Getting
them attached correctly matters more than anything else in this module.

Matching is by **SHA-256**, never by filename. A contribution manifest carries a
checksum for every file, and the vault knows the checksum of every asset, so a
tag lands on exactly the right photograph even after it has been renamed to
resolve a collision. Filename matching would be approximately right and
occasionally, silently, wrong.

Two sources, both re-readable after the fact:

* a ``manifest.csv`` from a contribution drive, and
* the JSON sidecars osxphotos writes next to exported originals.

Neither is consumed at ingest time, so this can be re-run whenever a better
source of names turns up — and re-running is harmless.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from index.db import Index
from mediafiles import iter_media, read_sidecar_metadata, sha256_file
from settings import FamilyConfig, normalize_tag

MANIFEST_NAME = "manifest.csv"

SOURCE_MANIFEST = "manifest"
SOURCE_SIDECAR = "sidecar"


@dataclass
class PersonResult:
    """What a tagging pass did."""

    files_read: int = 0
    matched: int = 0
    unmatched: int = 0
    tags_applied: int = 0
    people: dict[str, int] = field(default_factory=dict)
    unknown_people: dict[str, int] = field(default_factory=dict)
    """Names found that aren't in the configured roster.

    Reported rather than dropped: a nickname nobody remembered to configure is
    exactly the kind of thing that silently loses a child's photographs from
    the selection engine's quota.
    """

    @property
    def coverage(self) -> float:
        total = self.matched + self.unmatched
        return self.matched / total if total else 0.0


def split_names(value: str) -> list[str]:
    """Split a manifest or sidecar person field into individual names."""
    if not value:
        return []
    return [part.strip() for part in value.split(";") if part.strip()]


def read_manifest_tags(manifest: Path) -> Iterator[tuple[str, list[str]]]:
    """Yield ``(sha256, names)`` from a contribution manifest."""
    with manifest.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            digest = (row.get("sha256") or "").strip()
            names = split_names(row.get("persons") or "")
            if digest and names:
                yield digest, names


def read_sidecar_tags(root: Path) -> Iterator[tuple[str, list[str]]]:
    """Yield ``(sha256, names)`` for media under ``root`` that has a sidecar.

    Hashes every file, so this is the slow path. Prefer a manifest when one
    exists — it already carries the checksums.
    """
    for media in iter_media(root, skip_names={MANIFEST_NAME}):
        names = split_names(read_sidecar_metadata(media).get("persons", ""))
        if names:
            yield sha256_file(media), names


def apply_tags(
    pairs: Iterator[tuple[str, list[str]]],
    index: Index,
    family: FamilyConfig,
    source: str,
    dry_run: bool = False,
) -> PersonResult:
    """Attach person tags to assets, matching on checksum.

    Names are resolved against the configured roster so that a nickname and a
    full name become the same person. Anything unrecognised is still applied —
    it is real information — but is also counted separately so an unconfigured
    spelling is visible rather than quietly diluting a child's tally.
    """
    result = PersonResult()
    roster = family.tag_index

    for digest, names in pairs:
        result.files_read += 1
        asset = index.asset_by_sha256(digest)
        if asset is None:
            result.unmatched += 1
            continue
        result.matched += 1

        for raw in names:
            subject = roster.get(normalize_tag(raw))
            canonical = subject.name if subject else raw
            if subject is None:
                result.unknown_people[raw] = result.unknown_people.get(raw, 0) + 1
            result.people[canonical] = result.people.get(canonical, 0) + 1
            result.tags_applied += 1
            if not dry_run:
                index.tag_asset(asset.id, canonical, source=source)

    return result


def tag_from_source(
    source_dir: Path,
    index: Index,
    family: FamilyConfig,
    dry_run: bool = False,
) -> PersonResult:
    """Tag assets from a drive or folder, preferring its manifest.

    The manifest is authoritative and cheap: it already holds checksums, so no
    file has to be re-hashed. Sidecars are the fallback for folders that were
    exported without one.
    """
    root = Path(source_dir).expanduser()
    if not root.is_dir():
        raise FileNotFoundError(f"Not a directory: {root}")

    manifest = root / MANIFEST_NAME
    if manifest.is_file():
        return apply_tags(read_manifest_tags(manifest), index, family, SOURCE_MANIFEST, dry_run)
    return apply_tags(read_sidecar_tags(root), index, family, SOURCE_SIDECAR, dry_run)
