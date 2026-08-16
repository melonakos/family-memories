"""The ingest pipeline: scan, identify, classify, file, record.

Two properties this module exists to hold.

**Three-way accounting.** Every file the scan finds ends as exactly one of:
imported (an asset in the vault), linked (deliberately not imported, with a
reason and a master), or queued (a human decides). Nothing is skipped, and the
counts always sum to the number of files seen. That is asserted in tests,
because "we processed everything" is the kind of claim that quietly stops being
true.

**Idempotence.** The inbox is never modified or deleted from. Running twice is
safe and nearly free: the second pass recognises everything by checksum and
imports nothing. An interrupted run — and a multi-hour run over a terabyte will
be interrupted — is resumed by running it again.

Identification (hashing, metadata) is separated from classification, but
classification happens *as files are applied*, against a view of the archive
that includes what this run has already imported. Classifying everything up
front against a frozen snapshot would let two copies of the same photo arriving
in the same batch both import, each unaware of the other.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from index.db import Index, utcnow
from index.models import (
    EXACT_DUPLICATE,
    LOW_RES_TWIN,
    REVIEW_AMBIGUOUS_MATCH,
    REVIEW_HIGHER_RES_ARRIVED,
    REVIEW_UNKNOWN_DATE,
    Asset,
)
from mediafiles import iter_media, media_type, sha256_file
from vault.filing import VaultError, file_asset

from .dedupe import Verdict, classify, compute_phash
from .metadata import FileMetadata, metadata_for, read_exiftool, require_exiftool

ProgressCallback = Callable[[str, int, int], None]


def _bump(bucket: dict[str, int], key: str) -> None:
    bucket[key] = bucket.get(key, 0) + 1


@dataclass
class IngestResult:
    """What one run did. The counts are the accounting invariant."""

    scanned: int = 0
    imported: int = 0
    linked: int = 0
    deferred: int = 0
    failed: int = 0

    imported_bytes: int = 0
    linked_bytes: int = 0

    undated: int = 0
    renamed: int = 0

    review_reasons: dict[str, int] = field(default_factory=dict)
    """Every review-queue entry this run created, by kind.

    Deliberately not the same as ``deferred``: an undated file is imported
    *and* flagged, so it appears here while counting toward ``imported``.
    Folding the two together would break the accounting invariant.
    """

    errors: list[str] = field(default_factory=list)
    imported_paths: list[str] = field(default_factory=list)
    linked_reasons: dict[str, int] = field(default_factory=dict)

    @property
    def accounted(self) -> int:
        return self.imported + self.linked + self.deferred + self.failed

    @property
    def balanced(self) -> bool:
        """Every scanned file landed somewhere. Must never be false."""
        return self.accounted == self.scanned



@dataclass(frozen=True)
class IdentifiedFile:
    """A file and everything knowable about it without consulting the archive."""

    path: Path
    sha256: str
    filesize: int
    phash: str | None
    metadata: FileMetadata


def scan_inbox(inbox: Path) -> list[Path]:
    """Every media file in the inbox, deterministically ordered."""
    root = Path(inbox).expanduser()
    if not root.is_dir():
        raise FileNotFoundError(f"Inbox is not a directory: {root}")
    return list(iter_media(root))


def best_first(files: list[IdentifiedFile], inbox: Path) -> list[IdentifiedFile]:
    """Order files so the best copy of a picture is always seen first.

    Without this, ingest results depend on filesystem traversal order, which
    differs by platform — Windows sorts paths case-insensitively, so a
    ``holiday/`` subfolder precedes ``IMG_0001.jpg`` there but follows it on
    macOS. For byte-identical duplicates that only decides which name is kept.
    For a downscaled twin it decides something real: process the small copy
    first and the full-resolution master is no longer "new", so it lands in the
    review queue instead of the vault.

    Sorting by resolution then file size makes the master win regardless, and
    the normalized relative path breaks ties identically on every platform.
    """

    def key(item: IdentifiedFile) -> tuple[int, int, str]:
        width = item.metadata.width or 0
        height = item.metadata.height or 0
        try:
            relative = item.path.relative_to(inbox).as_posix()
        except ValueError:
            relative = item.path.as_posix()
        return (-(width * height), -item.filesize, relative)

    return sorted(files, key=key)


def identify(paths: list[Path], progress: ProgressCallback | None = None) -> list[IdentifiedFile]:
    """Hash and read metadata for every file. Consults nothing, writes nothing."""
    exif_by_path = read_exiftool(paths)

    identified: list[IdentifiedFile] = []
    for position, path in enumerate(paths, start=1):
        if progress:
            progress(str(path), position, len(paths))
        identified.append(
            IdentifiedFile(
                path=path,
                sha256=sha256_file(path),
                filesize=path.stat().st_size,
                phash=compute_phash(path),
                metadata=metadata_for(path, exif_by_path.get(str(path.resolve()))),
            )
        )
    return identified


class _ArchiveView:
    """The archive as this run sees it: what was already there, plus what it
    has imported so far.

    Without the second half, two copies of one photo arriving in the same batch
    would both be classified against an archive containing neither.
    """

    def __init__(self, index: Index, dry_run: bool) -> None:
        self._index = index
        self._dry_run = dry_run
        self._hashed: list[Asset] = index.hashed_assets()
        self._by_sha: dict[str, Asset] = {}
        self._pending_id = -1

    @property
    def hashed_assets(self) -> list[Asset]:
        return self._hashed

    def exact_match(self, sha256: str) -> Asset | None:
        return self._by_sha.get(sha256) or self._index.asset_by_sha256(sha256)

    def record(
        self,
        item: IdentifiedFile,
        vault_path: str,
        asset_id: int | None,
    ) -> Asset:
        """Add a just-imported asset to the view.

        During a dry run no row exists, so a stand-in with a negative id keeps
        the classification identical to what the real run would produce.
        """
        if asset_id is None:
            self._pending_id -= 1
            asset_id = self._pending_id

        asset = Asset(
            id=asset_id,
            sha256=item.sha256,
            vault_path=vault_path,
            original_filename=item.path.name,
            media_type=media_type(item.path),
            filesize=item.filesize,
            taken_at_source=item.metadata.taken_at_source,
            imported_at=utcnow(),
            phash=item.phash,
            taken_at=item.metadata.taken_at,
            width=item.metadata.width,
            height=item.metadata.height,
        )
        self._by_sha[item.sha256] = asset
        if item.phash:
            self._hashed.append(asset)
        return asset


def ingest(
    inbox: Path,
    vault_root: Path,
    index: Index,
    threshold: int = 8,
    source_label: str | None = None,
    dry_run: bool = False,
    progress: ProgressCallback | None = None,
) -> IngestResult:
    """Run the pipeline over an inbox. Never modifies or removes anything in it."""
    require_exiftool()

    paths = scan_inbox(inbox)
    result = IngestResult(scanned=len(paths))
    if not paths:
        return result

    files = best_first(identify(paths, progress), Path(inbox).expanduser())
    view = _ArchiveView(index, dry_run)
    source_id = (
        None if dry_run or not source_label else index.add_source(source_label, kind="other")
    )

    for item in files:
        try:
            _apply(item, vault_root, index, view, result, source_id, dry_run, threshold)
        except (VaultError, OSError) as exc:
            result.failed += 1
            result.errors.append(f"{item.path}: {exc}")

    return result


def _apply(
    item: IdentifiedFile,
    vault_root: Path,
    index: Index,
    view: _ArchiveView,
    result: IngestResult,
    source_id: int | None,
    dry_run: bool,
    threshold: int,
) -> None:
    decision = classify(
        sha256=item.sha256,
        phash=item.phash,
        width=item.metadata.width,
        height=item.metadata.height,
        filesize=item.filesize,
        exact_match=view.exact_match(item.sha256),
        hashed_assets=view.hashed_assets,
        threshold=threshold,
    )
    original_path = str(item.path)

    if decision.verdict is Verdict.NEW:
        _import(item, vault_root, index, view, result, source_id, dry_run, original_path)
        return

    if decision.verdict in (Verdict.EXACT_DUPLICATE, Verdict.LOW_RES_TWIN):
        reason = EXACT_DUPLICATE if decision.verdict is Verdict.EXACT_DUPLICATE else LOW_RES_TWIN
        result.linked += 1
        result.linked_bytes += item.filesize
        _bump(result.linked_reasons, reason)

        master = decision.master
        # A negative id is a stand-in created during this run; there is no row
        # to point a foreign key at.
        if dry_run or master is None or master.id < 0:
            return
        # Idempotence: a rerun over the same inbox must not stack link rows.
        if not index.has_linked_path(original_path):
            index.add_linked_file(
                sha256=item.sha256,
                original_path=original_path,
                master_asset_id=master.id,
                reason=reason,
                filesize=item.filesize,
            )
        return

    kind = (
        REVIEW_HIGHER_RES_ARRIVED
        if decision.verdict is Verdict.HIGHER_RES_ARRIVED
        else REVIEW_AMBIGUOUS_MATCH
    )
    result.deferred += 1
    _bump(result.review_reasons, kind)
    if dry_run:
        return
    if not index.has_open_review_for_path(original_path):
        index.queue_review(
            kind,
            original_path=original_path,
            detail={
                "detail": decision.detail,
                "distance": decision.distance,
                "master": decision.master.vault_path if decision.master else None,
            },
        )


def _import(
    item: IdentifiedFile,
    vault_root: Path,
    index: Index,
    view: _ArchiveView,
    result: IngestResult,
    source_id: int | None,
    dry_run: bool,
    original_path: str,
) -> None:
    filed = file_asset(
        item.path, vault_root, item.metadata.taken_at, item.sha256, dry_run=dry_run
    )
    result.imported += 1
    result.imported_bytes += item.filesize
    result.imported_paths.append(filed.relative_path)
    if filed.renamed:
        result.renamed += 1

    asset_id: int | None = None
    if not dry_run:
        asset_id = index.add_asset(
            sha256=item.sha256,
            vault_path=filed.relative_path,
            original_filename=item.path.name,
            media_type=media_type(item.path),
            filesize=item.filesize,
            taken_at=item.metadata.taken_at,
            taken_at_source=item.metadata.taken_at_source,
            phash=item.phash,
            width=item.metadata.width,
            height=item.metadata.height,
            source_id=source_id,
        )
    view.record(item, filed.relative_path, asset_id)

    # An undated file is archived, not rejected — but it is flagged, and it sits
    # in the vault's `undated` bucket rather than under an invented date.
    if item.metadata.has_date:
        return
    result.undated += 1
    _bump(result.review_reasons, REVIEW_UNKNOWN_DATE)
    if dry_run:
        return
    index.queue_review(
        REVIEW_UNKNOWN_DATE,
        asset_id=asset_id,
        original_path=original_path,
        detail={"filename": item.path.name, "vault_path": filed.relative_path},
    )
