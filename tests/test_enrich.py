"""Tests for enrichment: person tags, inferred locations, backfill.

The recurring theme: an inference must stay distinguishable from an
observation, forever. A wrong coordinate or a misattributed face is a fact the
archive will repeat for decades, so the tests care as much about provenance as
about coverage.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta

import pytest
from conftest import requires_exiftool
from factories import make_dated_image, make_sidecar

from enrich.backfill import backfill_locations
from enrich.locations import confidence_for, infer_locations, nearest_located
from enrich.persons import apply_tags, read_manifest_tags, split_names, tag_from_source
from index.db import open_index
from index.models import GPS_FROM_EXIF, GPS_INFERRED
from settings import FamilyConfig, Subject

TAKEN = datetime(2015, 6, 15, 12, 0)

BERKELEY = (37.8716, -122.2727)
TAHOE = (39.0968, -120.0324)


@pytest.fixture
def index(tmp_path):
    with open_index(tmp_path / "index.db") as db:
        yield db


@pytest.fixture
def family():
    return FamilyConfig(
        subjects=(
            Subject(name="Child One", tags=("Chip",)),
            Subject(name="Child Two"),
        )
    )


def add_asset(index, sha, *, taken_at=TAKEN, gps=None, gps_source=None, name=None):
    return index.add_asset(
        sha256=sha,
        vault_path=f"2015/06/{sha[:8]}.jpg",
        original_filename=name or f"{sha[:8]}.jpg",
        media_type="photo",
        filesize=1000,
        taken_at=taken_at,
        taken_at_source="exif",
        gps_latitude=gps[0] if gps else None,
        gps_longitude=gps[1] if gps else None,
        gps_source=gps_source or (GPS_FROM_EXIF if gps else None),
    )


class TestSplitNames:
    def test_splits_on_semicolons(self):
        assert split_names("Child One; Child Two") == ["Child One", "Child Two"]

    def test_trims_and_drops_blanks(self):
        assert split_names("  A ;; B  ") == ["A", "B"]

    def test_empty(self):
        assert split_names("") == []


class TestPersonTagging:
    def test_matches_by_checksum_not_filename(self, index, family):
        """The vault renames files to resolve collisions; checksums don't move."""
        asset_id = add_asset(index, "a" * 64, name="renamed-abc123.jpg")
        apply_tags(iter([("a" * 64, ["Child One"])]), index, family, "manifest")
        assert index.persons_for_asset(asset_id) == ["Child One"]

    def test_resolves_a_nickname_to_the_canonical_name(self, index, family):
        """Otherwise a child's photos split across two names and their quota
        in the selection engine is silently short."""
        asset_id = add_asset(index, "a" * 64)
        apply_tags(iter([("a" * 64, ["Chip"])]), index, family, "manifest")
        assert index.persons_for_asset(asset_id) == ["Child One"]

    def test_matching_is_case_and_space_insensitive(self, index, family):
        asset_id = add_asset(index, "a" * 64)
        apply_tags(iter([("a" * 64, ["  child   ONE "])]), index, family, "manifest")
        assert index.persons_for_asset(asset_id) == ["Child One"]

    def test_unknown_names_are_applied_and_reported(self, index, family):
        """Real information, kept — but surfaced so an unconfigured nickname
        doesn't quietly go unnoticed."""
        asset_id = add_asset(index, "a" * 64)
        result = apply_tags(iter([("a" * 64, ["Grandma"])]), index, family, "manifest")
        assert index.persons_for_asset(asset_id) == ["Grandma"]
        assert result.unknown_people == {"Grandma": 1}

    def test_unmatched_checksums_are_counted(self, index, family):
        result = apply_tags(iter([("f" * 64, ["Child One"])]), index, family, "manifest")
        assert result.matched == 0
        assert result.unmatched == 1
        assert result.coverage == 0.0

    def test_tagging_twice_is_idempotent(self, index, family):
        asset_id = add_asset(index, "a" * 64)
        for _ in range(2):
            apply_tags(iter([("a" * 64, ["Child One"])]), index, family, "manifest")
        assert index.persons_for_asset(asset_id) == ["Child One"]

    def test_dry_run_writes_nothing(self, index, family):
        asset_id = add_asset(index, "a" * 64)
        result = apply_tags(
            iter([("a" * 64, ["Child One"])]), index, family, "manifest", dry_run=True
        )
        assert result.tags_applied == 1
        assert index.persons_for_asset(asset_id) == []

    def test_multiple_people_on_one_photo(self, index, family):
        asset_id = add_asset(index, "a" * 64)
        apply_tags(iter([("a" * 64, ["Child One", "Child Two"])]), index, family, "manifest")
        assert index.persons_for_asset(asset_id) == ["Child One", "Child Two"]


class TestReadManifest:
    def test_reads_checksums_and_people(self, tmp_path):
        manifest = tmp_path / "manifest.csv"
        with manifest.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=["path", "date", "albums", "persons", "size_bytes", "sha256"]
            )
            writer.writeheader()
            writer.writerow(
                {
                    "path": "2015/06/IMG_1.jpg",
                    "date": "2015-06-15",
                    "albums": "",
                    "persons": "Child One; Child Two",
                    "size_bytes": "100",
                    "sha256": "a" * 64,
                }
            )
            writer.writerow(
                {
                    "path": "2015/06/IMG_2.jpg",
                    "date": "",
                    "albums": "",
                    "persons": "",
                    "size_bytes": "100",
                    "sha256": "b" * 64,
                }
            )
        pairs = list(read_manifest_tags(manifest))
        assert pairs == [("a" * 64, ["Child One", "Child Two"])]


@requires_exiftool
class TestTagFromSource:
    def test_prefers_the_manifest(self, tmp_path, index, family):
        drive = tmp_path / "drive"
        drive.mkdir()
        image = make_dated_image(drive / "IMG_1.jpg", TAKEN)
        from mediafiles import sha256_file

        digest = sha256_file(image)
        asset_id = add_asset(index, digest)

        with (drive / "manifest.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["path", "persons", "sha256"])
            writer.writeheader()
            writer.writerow({"path": "IMG_1.jpg", "persons": "Child Two", "sha256": digest})

        result = tag_from_source(drive, index, family)
        assert result.matched == 1
        assert index.persons_for_asset(asset_id) == ["Child Two"]

    def test_falls_back_to_sidecars(self, tmp_path, index, family):
        drive = tmp_path / "drive"
        drive.mkdir()
        image = make_dated_image(drive / "IMG_1.jpg", TAKEN)
        make_sidecar(image, TAKEN, persons=["Child One"])
        from mediafiles import sha256_file

        asset_id = add_asset(index, sha256_file(image))

        tag_from_source(drive, index, family)
        assert index.persons_for_asset(asset_id) == ["Child One"]

    def test_missing_directory_raises(self, tmp_path, index, family):
        with pytest.raises(FileNotFoundError):
            tag_from_source(tmp_path / "nope", index, family)


class TestConfidence:
    def test_full_at_zero_gap(self):
        assert confidence_for(timedelta(0)) == 1.0

    def test_decays_with_distance(self):
        assert confidence_for(timedelta(minutes=30)) > confidence_for(timedelta(hours=3))

    def test_zero_at_and_beyond_the_window(self):
        assert confidence_for(timedelta(hours=6)) == 0.0
        assert confidence_for(timedelta(hours=99)) == 0.0


class TestNearestLocated:
    def _anchors(self, index):
        anchors = [a for a in index.assets_with_location(source="exif") if a.taken_at]
        anchors.sort(key=lambda a: a.taken_at)
        return anchors, [a.taken_at for a in anchors]

    def test_finds_the_closest_in_time(self, index):
        add_asset(index, "a" * 64, taken_at=TAKEN - timedelta(hours=2), gps=BERKELEY)
        add_asset(index, "b" * 64, taken_at=TAKEN + timedelta(minutes=10), gps=TAHOE)
        anchors, times = self._anchors(index)

        found = nearest_located(TAKEN, anchors, times, timedelta(hours=6))
        assert found.gps_latitude == TAHOE[0]

    def test_respects_the_window(self, index):
        add_asset(index, "a" * 64, taken_at=TAKEN - timedelta(hours=10), gps=BERKELEY)
        anchors, times = self._anchors(index)
        assert nearest_located(TAKEN, anchors, times, timedelta(hours=6)) is None

    def test_will_not_cross_midnight(self, index):
        """Six hours across a day boundary usually means travel or sleep."""
        add_asset(index, "a" * 64, taken_at=datetime(2015, 6, 14, 23, 30), gps=BERKELEY)
        anchors, times = self._anchors(index)
        found = nearest_located(datetime(2015, 6, 15, 0, 30), anchors, times, timedelta(hours=6))
        assert found is None

    def test_empty_anchor_set(self, index):
        assert nearest_located(TAKEN, [], [], timedelta(hours=6)) is None


class TestInferLocations:
    def test_fills_a_nearby_gap(self, index):
        add_asset(index, "a" * 64, taken_at=TAKEN, gps=BERKELEY)
        target = add_asset(index, "b" * 64, taken_at=TAKEN + timedelta(minutes=20))

        result = infer_locations(index)

        assert result.inferred == 1
        filled = index.asset_by_sha256("b" * 64)
        assert filled.gps_latitude == BERKELEY[0]
        assert filled.gps_source == GPS_INFERRED
        assert filled.location_is_inferred
        assert filled.id == target

    def test_leaves_distant_photos_alone(self, index):
        """An empty field is honest; a wrong coordinate is repeated forever."""
        add_asset(index, "a" * 64, taken_at=TAKEN, gps=BERKELEY)
        add_asset(index, "b" * 64, taken_at=TAKEN + timedelta(hours=9))

        result = infer_locations(index)

        assert result.inferred == 0
        assert result.skipped_no_neighbour == 1
        assert not index.asset_by_sha256("b" * 64).has_location

    def test_never_overwrites_a_recorded_location(self, index):
        add_asset(index, "a" * 64, taken_at=TAKEN, gps=BERKELEY)
        add_asset(index, "b" * 64, taken_at=TAKEN + timedelta(minutes=5), gps=TAHOE)

        infer_locations(index)

        kept = index.asset_by_sha256("b" * 64)
        assert kept.gps_latitude == TAHOE[0]
        assert kept.gps_source == GPS_FROM_EXIF

    def test_does_not_infer_from_an_inference(self, index):
        """Otherwise one guess propagates across the archive, each step looking
        as solid as the last."""
        add_asset(index, "a" * 64, taken_at=TAKEN, gps=BERKELEY)
        add_asset(index, "b" * 64, taken_at=TAKEN + timedelta(minutes=30))
        add_asset(index, "c" * 64, taken_at=TAKEN + timedelta(hours=7))

        infer_locations(index)

        # b is within reach of a; c is only within reach of b, which is inferred.
        assert index.asset_by_sha256("b" * 64).has_location
        assert not index.asset_by_sha256("c" * 64).has_location

    def test_confidence_bands_are_reported(self, index):
        add_asset(index, "a" * 64, taken_at=TAKEN, gps=BERKELEY)
        add_asset(index, "b" * 64, taken_at=TAKEN + timedelta(minutes=5))
        add_asset(index, "c" * 64, taken_at=TAKEN + timedelta(hours=4))

        result = infer_locations(index)
        assert result.by_confidence.get("high") == 1
        assert result.by_confidence.get("low") == 1

    def test_dry_run_writes_nothing(self, index):
        add_asset(index, "a" * 64, taken_at=TAKEN, gps=BERKELEY)
        add_asset(index, "b" * 64, taken_at=TAKEN + timedelta(minutes=20))

        result = infer_locations(index, dry_run=True)

        assert result.inferred == 1
        assert not index.asset_by_sha256("b" * 64).has_location

    def test_undated_photos_are_never_targets(self, index):
        add_asset(index, "a" * 64, taken_at=TAKEN, gps=BERKELEY)
        add_asset(index, "b" * 64, taken_at=None)

        infer_locations(index)

        assert not index.asset_by_sha256("b" * 64).has_location


@requires_exiftool
class TestBackfill:
    def test_recovers_locations_from_vault_originals(self, tmp_path, index):
        """The rebuildable-from-the-vault claim, exercised rather than asserted."""
        import subprocess

        from mediafiles import sha256_file

        vault = tmp_path / "vault"
        image = make_dated_image(vault / "2015" / "06" / "IMG_1.jpg", TAKEN)
        subprocess.run(
            [
                "exiftool", "-overwrite_original",
                "-GPSLatitude=37.8716", "-GPSLatitudeRef=N",
                "-GPSLongitude=-122.2727", "-GPSLongitudeRef=W",
                str(image),
            ],
            capture_output=True,
            check=True,
        )

        index.add_asset(
            sha256=sha256_file(image),
            vault_path="2015/06/IMG_1.jpg",
            original_filename="IMG_1.jpg",
            media_type="photo",
            filesize=image.stat().st_size,
            taken_at=TAKEN,
            taken_at_source="exif",
        )

        result = backfill_locations(vault, index)

        assert result.located == 1
        recovered = index.asset_by_vault_path("2015/06/IMG_1.jpg")
        assert recovered.gps_source == GPS_FROM_EXIF
        assert round(recovered.gps_latitude, 4) == 37.8716

    def test_reports_files_missing_from_the_vault(self, tmp_path, index):
        vault = tmp_path / "vault"
        vault.mkdir()
        add_asset(index, "a" * 64)
        result = backfill_locations(vault, index)
        assert result.missing_files == ["2015/06/aaaaaaaa.jpg"]

    def test_does_not_modify_vault_originals(self, tmp_path, index):
        from mediafiles import sha256_file

        vault = tmp_path / "vault"
        image = make_dated_image(vault / "2015" / "06" / "IMG_1.jpg", TAKEN)
        before = sha256_file(image)
        index.add_asset(
            sha256=before,
            vault_path="2015/06/IMG_1.jpg",
            original_filename="IMG_1.jpg",
            media_type="photo",
            filesize=image.stat().st_size,
            taken_at=TAKEN,
            taken_at_source="exif",
        )

        backfill_locations(vault, index)

        assert sha256_file(image) == before
