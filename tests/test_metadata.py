"""Tests for date and dimension extraction.

The rule under test throughout: a date comes from the file's own metadata or a
sidecar, or it does not exist. There is no third source, and in particular the
filesystem's timestamps are never consulted.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from conftest import requires_exiftool
from factories import make_dated_image, make_image, make_sidecar

from ingest.metadata import (
    SOURCE_EXIF,
    SOURCE_SIDECAR,
    SOURCE_UNKNOWN,
    metadata_for,
    parse_exif_datetime,
    parse_sidecar_datetime,
    read_exiftool,
    require_exiftool,
)


class TestParseExifDatetime:
    def test_standard_exif_format(self):
        assert parse_exif_datetime("2015:06:15 12:30:45") == datetime(2015, 6, 15, 12, 30, 45)

    def test_dashed_variant(self):
        assert parse_exif_datetime("2015-06-15 12:30:45") == datetime(2015, 6, 15, 12, 30, 45)

    def test_subseconds_and_offset_are_tolerated(self):
        assert parse_exif_datetime("2015:06:15 12:30:45.123+01:00") == datetime(
            2015, 6, 15, 12, 30, 45
        )

    def test_exiftool_zero_placeholder_is_unknown(self):
        """Its way of saying "unset". Filing under year zero would be worse."""
        assert parse_exif_datetime("0000:00:00 00:00:00") is None

    def test_zero_month_or_day_is_unknown(self):
        assert parse_exif_datetime("2015:00:00 00:00:00") is None

    @pytest.mark.parametrize("value", ["", None, "not a date", "2015", 0])
    def test_junk_is_unknown(self, value):
        assert parse_exif_datetime(value) is None

    def test_impossible_date_is_unknown(self):
        assert parse_exif_datetime("2015:02:30 12:00:00") is None


class TestParseSidecarDatetime:
    def test_iso_format(self):
        assert parse_sidecar_datetime("2015-06-15T12:30:45") == datetime(2015, 6, 15, 12, 30, 45)

    def test_timezone_is_dropped_to_stay_comparable(self):
        """The archive stores wall-clock capture time. Mixing aware and naive
        datetimes makes every later comparison a landmine."""
        parsed = parse_sidecar_datetime("2015-06-15T12:30:45+02:00")
        assert parsed == datetime(2015, 6, 15, 12, 30, 45)
        assert parsed.tzinfo is None

    def test_falls_back_to_exif_shaped_values(self):
        assert parse_sidecar_datetime("2015:06:15 12:30:45") == datetime(2015, 6, 15, 12, 30, 45)

    def test_empty_is_unknown(self):
        assert parse_sidecar_datetime("") is None


class TestMetadataFor:
    def test_prefers_exif_over_nothing(self, tmp_path):
        image = make_image(tmp_path / "a.jpg")
        meta = metadata_for(image, {"DateTimeOriginal": "2015:06:15 12:00:00"})
        assert meta.taken_at == datetime(2015, 6, 15, 12, 0)
        assert meta.taken_at_source == SOURCE_EXIF

    def test_sidecar_wins_over_exif(self, tmp_path):
        """The sidecar reflects the library the file came from, including any
        correction a human made there."""
        image = make_image(tmp_path / "a.jpg")
        make_sidecar(image, datetime(2001, 1, 1, 8, 0))
        meta = metadata_for(image, {"DateTimeOriginal": "2015:06:15 12:00:00"})
        assert meta.taken_at == datetime(2001, 1, 1, 8, 0)
        assert meta.taken_at_source == SOURCE_SIDECAR

    def test_falls_through_the_tag_priority(self, tmp_path):
        image = make_image(tmp_path / "a.jpg")
        meta = metadata_for(image, {"CreateDate": "2016:02:03 04:05:06"})
        assert meta.taken_at == datetime(2016, 2, 3, 4, 5, 6)

    def test_video_create_date_is_read(self, tmp_path):
        clip = tmp_path / "clip.mov"
        clip.write_bytes(b"x")
        meta = metadata_for(clip, {"MediaCreateDate": "2018:08:08 08:08:08"})
        assert meta.taken_at == datetime(2018, 8, 8, 8, 8, 8)

    def test_no_date_anywhere_is_unknown(self, tmp_path):
        image = make_image(tmp_path / "a.jpg")
        meta = metadata_for(image, {"ImageWidth": 640, "ImageHeight": 480})
        assert meta.taken_at is None
        assert meta.taken_at_source == SOURCE_UNKNOWN
        assert not meta.has_date

    def test_filesystem_time_is_never_used(self, tmp_path):
        """The whole point. The file has an mtime; it must not become a date."""
        image = make_image(tmp_path / "a.jpg")
        assert image.stat().st_mtime > 0
        assert metadata_for(image, {}).taken_at is None

    def test_dimensions_are_carried(self, tmp_path):
        image = make_image(tmp_path / "a.jpg")
        meta = metadata_for(image, {"ImageWidth": 1600, "ImageHeight": 1200})
        assert (meta.width, meta.height) == (1600, 1200)

    def test_zero_dimensions_are_treated_as_unknown(self, tmp_path):
        image = make_image(tmp_path / "a.jpg")
        meta = metadata_for(image, {"ImageWidth": 0, "ImageHeight": 0})
        assert meta.width is None and meta.height is None

    def test_missing_exif_dict_is_survivable(self, tmp_path):
        image = make_image(tmp_path / "a.jpg")
        assert metadata_for(image, None).taken_at is None


@requires_exiftool
class TestRealExiftool:
    def test_available(self):
        assert require_exiftool()

    def test_reads_a_date_written_by_exiftool(self, tmp_path):
        taken = datetime(2015, 6, 15, 12, 0)
        image = make_dated_image(tmp_path / "a.jpg", taken)
        exif = read_exiftool([image])
        meta = metadata_for(image, exif.get(str(image.resolve())))
        assert meta.taken_at == taken
        assert meta.taken_at_source == SOURCE_EXIF

    def test_reads_dimensions(self, tmp_path):
        image = make_dated_image(tmp_path / "a.jpg", datetime(2015, 1, 1), size=(800, 600))
        exif = read_exiftool([image])
        meta = metadata_for(image, exif.get(str(image.resolve())))
        assert (meta.width, meta.height) == (800, 600)

    def test_image_without_exif_reads_as_undated(self, tmp_path):
        image = make_image(tmp_path / "plain.jpg")
        exif = read_exiftool([image])
        assert metadata_for(image, exif.get(str(image.resolve()))).taken_at is None

    def test_batches_many_files_in_one_call(self, tmp_path):
        """Batching is what keeps a library-scale run from being dominated by
        process startup."""
        images = [
            make_dated_image(tmp_path / f"img_{i}.jpg", datetime(2015, 6, i + 1), seed=i)
            for i in range(12)
        ]
        results = read_exiftool(images)
        assert len(results) == 12
        for i, image in enumerate(images):
            meta = metadata_for(image, results.get(str(image.resolve())))
            assert meta.taken_at == datetime(2015, 6, i + 1)

    def test_empty_input_does_not_invoke_exiftool(self):
        assert read_exiftool([]) == {}

    def test_unicode_filenames_survive(self, tmp_path):
        taken = datetime(2015, 6, 15, 12, 0)
        image = make_dated_image(tmp_path / "café_Ñuñez.jpg", taken)
        exif = read_exiftool([image])
        assert metadata_for(image, exif.get(str(image.resolve()))).taken_at == taken
