"""Tests for the index database."""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from index.db import BASE_SCHEMA_VERSION, SCHEMA_VERSION, IndexDatabaseError, open_index
from index.models import (
    DATE_FROM_EXIF,
    EXACT_DUPLICATE,
    REVIEW_OPEN,
    REVIEW_RESOLVED,
    REVIEW_UNKNOWN_DATE,
)


@pytest.fixture
def index(tmp_path):
    with open_index(tmp_path / "index.db") as db:
        yield db


def add_asset(index, sha="a" * 64, path="2015/06/IMG_0001.JPG", **kwargs):
    defaults = {
        "sha256": sha,
        "vault_path": path,
        "original_filename": "IMG_0001.JPG",
        "media_type": "photo",
        "filesize": 1024,
        "taken_at": datetime(2015, 6, 15, 12, 0),
        "taken_at_source": DATE_FROM_EXIF,
    }
    return index.add_asset(**{**defaults, **kwargs})


class TestSchema:
    def test_creates_at_current_version(self, index):
        assert index.version == SCHEMA_VERSION

    def test_initialize_is_idempotent(self, tmp_path):
        path = tmp_path / "index.db"
        with open_index(path) as first:
            add_asset(first)
        with open_index(path) as second:
            assert second.version == SCHEMA_VERSION
            assert len(list(second.assets())) == 1

    def test_refuses_a_newer_schema(self, tmp_path):
        """Better to stop than to write a format this build doesn't share."""
        path = tmp_path / "index.db"
        with open_index(path) as db:
            db._db.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION + 5, "2030-01-01"),
            )
            db._db.commit()
        with pytest.raises(IndexDatabaseError, match="newer version"):
            open_index(path)

    def test_missing_index_can_be_required(self, tmp_path):
        with pytest.raises(IndexDatabaseError, match="index init"):
            open_index(tmp_path / "nope.db", create=False)

    def test_creates_parent_directories(self, tmp_path):
        with open_index(tmp_path / "deep" / "nested" / "index.db") as db:
            assert db.path.is_file()


class TestMigrations:
    """A fresh database runs the base schema then every migration, so the
    upgrade path is exercised on every install rather than only on the one
    machine that happens to be old."""

    def test_fresh_database_lands_on_the_current_version(self, index):
        assert index.version == SCHEMA_VERSION
        assert SCHEMA_VERSION > BASE_SCHEMA_VERSION, "migrations should be exercised"

    def test_migration_columns_exist(self, index):
        columns = {r["name"] for r in index._db.execute("PRAGMA table_info(assets)")}
        assert {"gps_latitude", "gps_longitude", "gps_source"} <= columns

    def test_upgrades_a_version_1_database(self, tmp_path):
        """The real upgrade: an index created before the migration existed."""
        from index.db import SCHEMA_PATH

        path = tmp_path / "old.db"
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (1, '2026-01-01')"
        )
        connection.execute(
            "INSERT INTO assets (sha256, vault_path, original_filename, taken_at_source,"
            " media_type, filesize, imported_at) "
            "VALUES ('a', 'p', 'f.jpg', 'exif', 'photo', 1, '2026-01-01')"
        )
        connection.commit()
        connection.close()

        with open_index(path) as upgraded:
            assert upgraded.version == SCHEMA_VERSION
            # The pre-existing row survives and reads back through the new model.
            asset = upgraded.asset_by_sha256("a")
            assert asset.vault_path == "p"
            assert asset.gps_latitude is None

    def test_migrating_twice_is_a_no_op(self, tmp_path):
        path = tmp_path / "index.db"
        with open_index(path) as first:
            first.add_asset(
                sha256="a" * 64,
                vault_path="p",
                original_filename="f.jpg",
                media_type="photo",
                filesize=1,
                taken_at=None,
                taken_at_source="unknown",
            )
        with open_index(path) as second:
            assert second.version == SCHEMA_VERSION
            assert len(list(second.assets())) == 1

    def test_enrichments_are_idempotent_per_value(self, index):
        asset_id = add_asset(index)
        for _ in range(3):
            index.add_enrichment(asset_id, "caption", "a day at the beach", "inferred", 0.8)
        assert len(index.enrichments_for(asset_id)) == 1

    def test_enrichment_records_source_and_confidence(self, index):
        asset_id = add_asset(index)
        index.add_enrichment(asset_id, "music", "Title — Artist", "acoustid", 0.62)
        entry = index.enrichments_for(asset_id, kind="music")[0]
        assert entry.source == "acoustid"
        assert entry.confidence == 0.62


class TestLocations:
    def test_records_coordinates_and_provenance(self, index):
        asset_id = add_asset(index)
        index.set_location(asset_id, 37.8716, -122.2727, source="exif")
        asset = index.asset_by_sha256("a" * 64)
        assert asset.has_location
        assert asset.gps_source == "exif"
        assert not asset.location_is_inferred

    def test_an_inference_never_overwrites_an_observation(self, index):
        """Whatever order they arrive in, the camera outranks the guess."""
        asset_id = add_asset(index)
        index.set_location(asset_id, 37.8716, -122.2727, source="exif")
        index.set_location(asset_id, 0.0, 0.0, source="inferred")
        asset = index.asset_by_sha256("a" * 64)
        assert asset.gps_latitude == 37.8716
        assert asset.gps_source == "exif"

    def test_an_observation_replaces_an_inference(self, index):
        asset_id = add_asset(index)
        index.set_location(asset_id, 1.0, 2.0, source="inferred")
        index.set_location(asset_id, 37.8716, -122.2727, source="exif")
        assert index.asset_by_sha256("a" * 64).gps_source == "exif"

    def test_unlocated_dated_assets_are_findable(self, index):
        add_asset(index, sha="a" * 64, path="p1")
        located = add_asset(index, sha="b" * 64, path="p2")
        index.set_location(located, 1.0, 2.0, source="exif")
        assert [a.sha256 for a in index.assets_missing_location()] == ["a" * 64]

    def test_unknown_asset_raises(self, index):
        with pytest.raises(IndexDatabaseError, match="No asset"):
            index.set_location(999, 1.0, 2.0, source="exif")


class TestAssets:
    def test_round_trip(self, index):
        asset_id = add_asset(index)
        asset = index.asset_by_sha256("a" * 64)
        assert asset is not None
        assert asset.id == asset_id
        assert asset.vault_path == "2015/06/IMG_0001.JPG"
        assert asset.taken_at == datetime(2015, 6, 15, 12, 0)

    def test_checksum_is_unique(self, index):
        """Two assets with one checksum would break dedupe's core assumption."""
        add_asset(index)
        with pytest.raises(IndexDatabaseError):
            add_asset(index, path="2016/01/OTHER.JPG")

    def test_vault_path_is_unique(self, index):
        add_asset(index)
        with pytest.raises(IndexDatabaseError):
            add_asset(index, sha="b" * 64)

    def test_undated_asset_is_allowed(self, index):
        add_asset(index, taken_at=None, taken_at_source="unknown")
        assert index.asset_by_sha256("a" * 64).taken_at is None

    def test_hashed_assets_excludes_unhashed(self, index):
        add_asset(index, sha="a" * 64, path="p1", phash="ff00ff00ff00ff00")
        add_asset(index, sha="b" * 64, path="p2", phash=None)
        assert [a.sha256 for a in index.hashed_assets()] == ["a" * 64]

    def test_pixels_helper(self, index):
        add_asset(index, width=640, height=480)
        assert index.asset_by_sha256("a" * 64).pixels == 640 * 480


class TestLinkedFiles:
    def test_records_why_a_file_was_not_imported(self, index):
        master = add_asset(index)
        index.add_linked_file(
            sha256="b" * 64,
            original_path="/inbox/copy.jpg",
            master_asset_id=master,
            reason=EXACT_DUPLICATE,
            filesize=512,
        )
        linked = index.linked_files()
        assert len(linked) == 1
        assert linked[0].reason == EXACT_DUPLICATE
        assert linked[0].master_asset_id == master

    def test_known_path_is_detectable(self, index):
        """What makes re-running ingest not stack duplicate link rows."""
        master = add_asset(index)
        index.add_linked_file(
            sha256="b" * 64,
            original_path="/inbox/copy.jpg",
            master_asset_id=master,
            reason=EXACT_DUPLICATE,
            filesize=512,
        )
        assert index.has_linked_path("/inbox/copy.jpg")
        assert not index.has_linked_path("/inbox/other.jpg")

    def test_master_must_exist(self, index):
        with pytest.raises(IndexDatabaseError):
            index.add_linked_file(
                sha256="b" * 64,
                original_path="/inbox/x.jpg",
                master_asset_id=999,
                reason=EXACT_DUPLICATE,
                filesize=1,
            )


class TestReviewQueue:
    def test_queue_and_list(self, index):
        index.queue_review(REVIEW_UNKNOWN_DATE, original_path="/inbox/scan.jpg")
        items = index.reviews()
        assert len(items) == 1
        assert items[0].kind == REVIEW_UNKNOWN_DATE
        assert items[0].status == REVIEW_OPEN

    def test_detail_round_trips_as_json(self, index):
        index.queue_review(
            REVIEW_UNKNOWN_DATE, original_path="/x.jpg", detail={"filename": "x.jpg", "n": 3}
        )
        assert index.reviews()[0].detail == {"filename": "x.jpg", "n": 3}

    def test_resolving_removes_it_from_the_open_list(self, index):
        review_id = index.queue_review(REVIEW_UNKNOWN_DATE, original_path="/x.jpg")
        index.resolve_review(review_id, "dated by hand")
        assert index.reviews(status=REVIEW_OPEN) == []
        resolved = index.reviews(status=REVIEW_RESOLVED)
        assert resolved[0].resolution == "dated by hand"
        assert resolved[0].resolved_at

    def test_open_review_for_path_is_detectable(self, index):
        index.queue_review(REVIEW_UNKNOWN_DATE, original_path="/x.jpg")
        assert index.has_open_review_for_path("/x.jpg")
        assert not index.has_open_review_for_path("/y.jpg")

    def test_filter_by_kind(self, index):
        index.queue_review("unknown_date", original_path="/a.jpg")
        index.queue_review("ambiguous_match", original_path="/b.jpg")
        assert len(index.reviews(kind="unknown_date")) == 1


class TestPersons:
    def test_tagging_is_idempotent(self, index):
        asset_id = add_asset(index)
        index.tag_asset(asset_id, "Child One")
        index.tag_asset(asset_id, "Child One")
        assert index.persons_for_asset(asset_id) == ["Child One"]

    def test_multiple_people_sorted(self, index):
        asset_id = add_asset(index)
        index.tag_asset(asset_id, "Child Two")
        index.tag_asset(asset_id, "Child One")
        assert index.persons_for_asset(asset_id) == ["Child One", "Child Two"]


class TestSources:
    def test_same_label_reuses_the_row(self, index):
        first = index.add_source("contribution drive", kind="contribution")
        assert index.add_source("contribution drive") == first
        assert len(index.sources()) == 1


class TestStats:
    def test_empty_index(self, index):
        stats = index.stats()
        assert stats.assets == 0
        assert stats.total_bytes == 0
        assert stats.open_reviews == 0

    def test_counts_and_ranges(self, index):
        add_asset(index, sha="a" * 64, path="p1", filesize=100, taken_at=datetime(2010, 1, 1))
        add_asset(
            index,
            sha="b" * 64,
            path="p2",
            filesize=200,
            taken_at=datetime(2020, 5, 5),
            media_type="video",
        )
        add_asset(index, sha="c" * 64, path="p3", filesize=50, taken_at=None)

        stats = index.stats()
        assert stats.assets == 3
        assert stats.total_bytes == 350
        assert (stats.photos, stats.videos) == (2, 1)
        assert stats.undated == 1
        assert stats.earliest == datetime(2010, 1, 1)
        assert stats.latest == datetime(2020, 5, 5)

    def test_groups_reviews_and_links(self, index):
        master = add_asset(index)
        index.add_linked_file(
            sha256="b" * 64,
            original_path="/x.jpg",
            master_asset_id=master,
            reason=EXACT_DUPLICATE,
            filesize=1,
        )
        index.queue_review(REVIEW_UNKNOWN_DATE, original_path="/y.jpg")
        stats = index.stats()
        assert stats.linked_by_reason == {EXACT_DUPLICATE: 1}
        assert stats.reviews_by_kind == {REVIEW_UNKNOWN_DATE: 1}
        assert stats.open_reviews == 1
