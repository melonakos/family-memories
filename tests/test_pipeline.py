"""End-to-end tests for the ingest pipeline.

Two invariants dominate here, and both are the kind that quietly stop holding:

* **Accounting** — imported + linked + deferred + failed == scanned. Always.
* **Idempotence** — running twice changes nothing the second time.

The inbox is built from real generated JPEGs with real EXIF, so dedupe and date
extraction are genuinely exercised rather than mocked.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from conftest import requires_exiftool
from factories import copy_file, downscale, make_dated_image, make_fake_video, make_image

from index.db import open_index
from index.models import EXACT_DUPLICATE, LOW_RES_TWIN, REVIEW_UNKNOWN_DATE
from ingest.pipeline import ingest
from mediafiles import sha256_file

pytestmark = requires_exiftool

TAKEN = datetime(2015, 6, 15, 12, 0)
LATER = datetime(2018, 3, 2, 9, 30)


@pytest.fixture
def workspace(tmp_path):
    inbox = tmp_path / "inbox"
    vault = tmp_path / "vault"
    inbox.mkdir()
    vault.mkdir()
    return inbox, vault


@pytest.fixture
def index(tmp_path):
    with open_index(tmp_path / "index.db") as db:
        yield db


def build_inbox(inbox):
    """One of every case the pipeline must handle."""
    original = make_dated_image(inbox / "IMG_0001.jpg", TAKEN, size=(1600, 1200), seed=1)
    other = make_dated_image(inbox / "IMG_0002.jpg", LATER, size=(1600, 1200), seed=50)
    copy_file(original, inbox / "sub" / "IMG_0001_copy.jpg")   # exact duplicate
    downscale(original, inbox / "sub" / "IMG_0001_small.jpg")  # low-res twin
    make_image(inbox / "undated.jpg", seed=77)                 # no EXIF date
    make_fake_video(inbox / "clip.mov")                        # video, no metadata
    return original, other


class TestAccounting:
    def test_every_file_is_accounted_for(self, workspace, index):
        inbox, vault = workspace
        build_inbox(inbox)
        result = ingest(inbox, vault, index)

        assert result.scanned == 6
        assert result.balanced, (
            f"{result.accounted} accounted vs {result.scanned} scanned — a file "
            f"went missing from the pipeline's own bookkeeping"
        )

    def test_dispositions_are_as_expected(self, workspace, index):
        inbox, vault = workspace
        build_inbox(inbox)
        result = ingest(inbox, vault, index)

        # 2 dated originals + 1 undated image + 1 video = 4 imported.
        assert result.imported == 4
        assert result.linked == 2
        assert result.linked_reasons == {EXACT_DUPLICATE: 1, LOW_RES_TWIN: 1}
        assert result.failed == 0

    def test_empty_inbox_is_fine(self, workspace, index):
        inbox, vault = workspace
        result = ingest(inbox, vault, index)
        assert result.scanned == 0
        assert result.balanced

    def test_missing_inbox_raises(self, tmp_path, index):
        with pytest.raises(FileNotFoundError):
            ingest(tmp_path / "nope", tmp_path / "vault", index)


class TestFilingAndIndexing:
    def test_files_by_year_and_month(self, workspace, index):
        inbox, vault = workspace
        build_inbox(inbox)
        ingest(inbox, vault, index)
        assert (vault / "2015" / "06" / "IMG_0001.jpg").is_file()
        assert (vault / "2018" / "03" / "IMG_0002.jpg").is_file()

    def test_index_matches_the_vault(self, workspace, index):
        inbox, vault = workspace
        build_inbox(inbox)
        ingest(inbox, vault, index)

        for asset in index.assets():
            target = vault / asset.vault_path
            assert target.is_file()
            assert sha256_file(target) == asset.sha256
            assert target.stat().st_size == asset.filesize

    def test_records_where_dates_came_from(self, workspace, index):
        inbox, vault = workspace
        build_inbox(inbox)
        ingest(inbox, vault, index)
        sources = {a.original_filename: a.taken_at_source for a in index.assets()}
        assert sources["IMG_0001.jpg"] == "exif"
        assert sources["undated.jpg"] == "unknown"

    def test_source_label_is_recorded(self, workspace, index):
        inbox, vault = workspace
        build_inbox(inbox)
        ingest(inbox, vault, index, source_label="camera dump")
        assert [s.label for s in index.sources()] == ["camera dump"]

    def test_inbox_is_never_modified(self, workspace, index):
        """The pipeline reads its input and nothing else."""
        inbox, vault = workspace
        build_inbox(inbox)
        before = {p: sha256_file(p) for p in sorted(inbox.rglob("*")) if p.is_file()}

        ingest(inbox, vault, index)

        after = {p: sha256_file(p) for p in sorted(inbox.rglob("*")) if p.is_file()}
        assert before == after


class TestUndatedFiles:
    def test_archived_under_undated_not_a_guess(self, workspace, index):
        inbox, vault = workspace
        build_inbox(inbox)
        ingest(inbox, vault, index)
        assert (vault / "undated" / "undated.jpg").is_file()
        assert (vault / "undated" / "clip.mov").is_file()

    def test_flagged_for_review(self, workspace, index):
        inbox, vault = workspace
        build_inbox(inbox)
        result = ingest(inbox, vault, index)

        assert result.undated == 2
        queued = index.reviews(kind=REVIEW_UNKNOWN_DATE)
        assert len(queued) == 2
        assert all(item.asset_id for item in queued)

    def test_undated_files_still_count_as_imported(self, workspace, index):
        """They are archived, not rejected. Accounting must reflect that."""
        inbox, vault = workspace
        build_inbox(inbox)
        result = ingest(inbox, vault, index)
        assert result.review_reasons[REVIEW_UNKNOWN_DATE] == 2
        assert result.balanced


class TestDryRun:
    def test_writes_nothing_at_all(self, workspace, index):
        inbox, vault = workspace
        build_inbox(inbox)
        result = ingest(inbox, vault, index, dry_run=True)

        assert result.imported == 4
        assert list(vault.rglob("*")) == []
        assert list(index.assets()) == []
        assert index.reviews() == []

    def test_predicts_what_the_real_run_does(self, workspace, index, tmp_path):
        """If these disagreed, --dry-run would be worse than useless."""
        inbox, vault = workspace
        build_inbox(inbox)

        predicted = ingest(inbox, vault, index, dry_run=True)
        actual = ingest(inbox, vault, index)

        assert predicted.imported == actual.imported
        assert predicted.linked == actual.linked
        assert predicted.deferred == actual.deferred
        assert predicted.linked_reasons == actual.linked_reasons
        assert sorted(predicted.imported_paths) == sorted(actual.imported_paths)


class TestIdempotence:
    def test_second_run_imports_nothing(self, workspace, index):
        inbox, vault = workspace
        build_inbox(inbox)

        first = ingest(inbox, vault, index)
        second = ingest(inbox, vault, index)

        assert first.imported == 4
        assert second.imported == 0
        assert second.linked == second.scanned
        assert second.balanced

    def test_vault_is_unchanged_by_a_second_run(self, workspace, index):
        inbox, vault = workspace
        build_inbox(inbox)

        ingest(inbox, vault, index)
        snapshot = {p.relative_to(vault).as_posix(): sha256_file(p)
                    for p in sorted(vault.rglob("*")) if p.is_file()}

        ingest(inbox, vault, index)
        after = {p.relative_to(vault).as_posix(): sha256_file(p)
                 for p in sorted(vault.rglob("*")) if p.is_file()}

        assert snapshot == after

    def test_link_rows_are_bounded(self, workspace, index):
        """Link rows settle at one per inbox file and stop growing.

        The second run legitimately adds rows: the four files it imported the
        first time now match their own vault copies by checksum, so their inbox
        paths become recorded links. What must never happen is unbounded growth
        across repeated runs.
        """
        inbox, vault = workspace
        build_inbox(inbox)

        ingest(inbox, vault, index)
        assert len(index.linked_files()) == 2  # the duplicate and the twin

        ingest(inbox, vault, index)
        settled = len(index.linked_files())
        assert settled == 6  # one per file in the inbox

        ingest(inbox, vault, index)
        ingest(inbox, vault, index)
        assert len(index.linked_files()) == settled

    def test_review_items_do_not_duplicate(self, workspace, index):
        inbox, vault = workspace
        build_inbox(inbox)
        ingest(inbox, vault, index)
        first = len(index.reviews())
        ingest(inbox, vault, index)
        assert len(index.reviews()) == first


class TestWithinBatchDuplicates:
    def test_two_identical_files_in_one_batch(self, workspace, index):
        """Neither is in the archive when the run starts, so a naive pipeline
        imports both and then trips the unique-checksum constraint."""
        inbox, vault = workspace
        original = make_dated_image(inbox / "a.jpg", TAKEN, seed=5)
        copy_file(original, inbox / "b.jpg")

        result = ingest(inbox, vault, index)

        assert result.imported == 1
        assert result.linked == 1
        assert result.failed == 0
        assert result.balanced
        assert len(list(index.assets())) == 1

    def test_twin_arriving_with_its_master(self, workspace, index):
        inbox, vault = workspace
        original = make_dated_image(inbox / "a.jpg", TAKEN, size=(1600, 1200), seed=6)
        downscale(original, inbox / "a_small.jpg")

        result = ingest(inbox, vault, index)

        assert result.imported == 1
        assert result.linked_reasons.get(LOW_RES_TWIN) == 1
        assert result.balanced


class TestOrdering:
    """Which file wins must not depend on how the filesystem enumerates."""

    def test_master_is_imported_even_when_the_twin_sorts_first(self, workspace, index):
        """The bug this ordering exists to prevent: process the downscaled copy
        first and the full-resolution master stops being 'new', landing in the
        review queue instead of the vault."""
        inbox, vault = workspace
        original = make_dated_image(inbox / "zzz_original.jpg", TAKEN, size=(1600, 1200), seed=9)
        downscale(original, inbox / "aaa_small.jpg")

        result = ingest(inbox, vault, index)

        assert result.imported == 1
        assert result.deferred == 0
        assert result.linked_reasons.get(LOW_RES_TWIN) == 1
        imported = next(index.assets())
        assert imported.original_filename == "zzz_original.jpg"
        assert (imported.width, imported.height) == (1600, 1200)

    def test_subdirectories_do_not_change_which_copy_is_kept(self, workspace, index):
        """Windows sorts paths case-insensitively, so a subfolder can precede a
        top-level file there and follow it elsewhere."""
        inbox, vault = workspace
        original = make_dated_image(inbox / "IMG_0001.jpg", TAKEN, size=(1600, 1200), seed=12)
        downscale(original, inbox / "holiday" / "IMG_0001_small.jpg")

        ingest(inbox, vault, index)

        imported = next(index.assets())
        assert imported.original_filename == "IMG_0001.jpg"

    def test_ordering_is_deterministic_for_identical_files(self, workspace, index):
        inbox, _ = workspace
        first = make_dated_image(inbox / "b.jpg", TAKEN, seed=4)
        copy_file(first, inbox / "a.jpg")

        from ingest.pipeline import best_first, identify, scan_inbox

        ordered = [f.path.name for f in best_first(identify(scan_inbox(inbox)), inbox)]
        assert ordered == ["a.jpg", "b.jpg"]


class TestNameCollisions:
    def test_same_name_different_photos_both_survive(self, workspace, index):
        inbox, vault = workspace
        make_dated_image(inbox / "one" / "IMG_0001.jpg", TAKEN, seed=11)
        make_dated_image(inbox / "two" / "IMG_0001.jpg", TAKEN, seed=22)

        result = ingest(inbox, vault, index)

        assert result.imported == 2
        assert result.renamed == 1
        filed = sorted(p.name for p in (vault / "2015" / "06").iterdir())
        assert len(filed) == 2
        assert len({sha256_file(vault / a.vault_path) for a in index.assets()}) == 2
