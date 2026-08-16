"""Tests for the dry-run inventory.

The export-size figure drives a hardware purchase and the cloud-download figure
drives the schedule, so both are checked against exact byte counts rather than
eyeballed.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from conftest import at

from contribute.demo import build_demo_library
from contribute.inventory import (
    build_inventory,
    format_duration,
    format_size,
    plural,
    render_report,
    report_to_dict,
)
from contribute.library import FakeLibrary
from contribute.models import Disposition, PhotoItem

MB = 1024 * 1024


def item(uuid: str, **kwargs) -> PhotoItem:
    defaults = {"original_filename": f"{uuid}.HEIC", "date": at(2010), "filesize": MB}
    return PhotoItem(uuid=uuid, **{**defaults, **kwargs})


class TestFormatting:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [(0, "0 B"), (512, "512 B"), (1024, "1.0 KB"), (1024**2, "1.0 MB"), (1024**3, "1.0 GB")],
    )
    def test_sizes(self, value, expected):
        assert format_size(value) == expected

    def test_terabytes_do_not_overflow_the_units(self):
        assert format_size(5 * 1024**4).endswith("TB")

    @pytest.mark.parametrize(
        ("hours", "contains"),
        [(0.5, "minutes"), (5.0, "hours"), (50.0, "days")],
    )
    def test_durations(self, hours, contains):
        assert contains in format_duration(hours)

    def test_sub_minute_downloads_read_sensibly(self):
        """"0 minutes" reads like a bug in a report someone is trusting."""
        assert format_duration(0.001) == "under a minute"

    @pytest.mark.parametrize(
        ("count", "expected"),
        [(0, "0 items"), (1, "1 item"), (2, "2 items"), (1500, "1,500 items")],
    )
    def test_pluralization(self, count, expected):
        assert plural(count, "item") == expected


class TestCounts:
    def test_totals_split_by_rule(self, contribute_config, family):
        library = FakeLibrary(
            [
                item("a", date=at(2010)),
                item("b", date=at(2012)),
                item("c", date=at(2018), persons=("Subject One",)),
                item("d", date=at(2018)),
            ]
        )
        report, decisions = build_inventory(library, contribute_config, family)

        assert report.scanned == 4
        assert report.included_count == 3
        assert report.counts[Disposition.INCLUDE_PRE_CUTOFF] == 2
        assert report.counts[Disposition.INCLUDE_TAGGED] == 1
        assert report.counts[Disposition.EXCLUDE_UNTAGGED] == 1
        assert len(decisions) == 4

    def test_included_bytes_counts_only_included_items(self, contribute_config, family):
        library = FakeLibrary(
            [
                item("a", date=at(2010), filesize=10 * MB),
                item("b", date=at(2018), filesize=999 * MB),  # untagged, excluded
            ]
        )
        report, _ = build_inventory(library, contribute_config, family)
        assert report.included_bytes == 10 * MB

    def test_media_types_are_counted(self, contribute_config, family):
        library = FakeLibrary(
            [
                item("a", date=at(2010)),
                item("b", date=at(2010), is_movie=True, is_photo=False),
                item("c", date=at(2010), is_live_photo=True),
            ]
        )
        report, _ = build_inventory(library, contribute_config, family)
        assert (report.included_photos, report.included_movies) == (2, 1)
        assert report.included_live_photos == 1

    def test_date_range_spans_included_items_only(self, contribute_config, family):
        library = FakeLibrary(
            [
                item("a", date=datetime(2008, 3, 1)),
                item("b", date=datetime(2013, 9, 1)),
                item("c", date=datetime(2020, 1, 1)),  # untagged, excluded
            ]
        )
        report, _ = build_inventory(library, contribute_config, family)
        assert report.earliest_included == datetime(2008, 3, 1)
        assert report.latest_included == datetime(2013, 9, 1)

    def test_subject_counts_track_tagged_items(self, contribute_config, family):
        library = FakeLibrary(
            [
                item("a", date=at(2018), persons=("Subject One",)),
                item("b", date=at(2018), persons=("Subject One", "Subject Two")),
            ]
        )
        report, _ = build_inventory(library, contribute_config, family)
        assert report.subject_counts["Subject One"] == 2
        assert report.subject_counts["Subject Two"] == 1


class TestCloudOnly:
    def test_counts_only_items_that_would_be_copied(self, contribute_config, family):
        """An excluded item never needs downloading, so it must not inflate the
        wait estimate that the handoff gets scheduled around."""
        library = FakeLibrary(
            [
                item("a", date=at(2010), is_missing=True, filesize=100 * MB),
                item("b", date=at(2018), is_missing=True, filesize=900 * MB),  # excluded
            ]
        )
        report, _ = build_inventory(library, contribute_config, family)
        assert report.cloud_only_count == 1
        assert report.cloud_only_bytes == 100 * MB

    def test_estimate_is_zero_when_nothing_is_cloud_only(self, contribute_config, family):
        report, _ = build_inventory(
            FakeLibrary([item("a", date=at(2010))]), contribute_config, family
        )
        assert report.estimated_download_hours == 0.0

    def test_estimate_scales_with_size(self, contribute_config, family):
        small, _ = build_inventory(
            FakeLibrary([item("a", date=at(2010), is_missing=True, filesize=MB)]),
            contribute_config,
            family,
        )
        large, _ = build_inventory(
            FakeLibrary([item("a", date=at(2010), is_missing=True, filesize=1000 * MB)]),
            contribute_config,
            family,
        )
        assert large.estimated_download_hours > small.estimated_download_hours > 0


class TestReviewAndGaps:
    def test_unknown_dates_are_listed_by_filename(self, contribute_config, family):
        library = FakeLibrary([item("a", original_filename="scan_01.jpg", date=None)])
        report, _ = build_inventory(library, contribute_config, family)
        assert report.unknown_date_files == ["scan_01.jpg"]
        assert report.included_count == 0

    def test_unknown_date_items_are_neither_included_nor_excluded(self, contribute_config, family):
        report, _ = build_inventory(
            FakeLibrary([item("a", date=None)]), contribute_config, family
        )
        assert report.included_count == 0
        assert report.excluded_count == 0
        assert report.counts[Disposition.REVIEW_UNKNOWN_DATE] == 1

    def test_untagged_window_is_tracked_separately(self, contribute_config, family):
        library = FakeLibrary(
            [
                item("a", date=datetime(2015, 6, 1), filesize=2 * MB),
                item("b", date=datetime(2020, 6, 1), filesize=7 * MB),
            ]
        )
        report, _ = build_inventory(library, contribute_config, family)
        assert report.untagged_window_count == 1
        assert report.untagged_window_bytes == 2 * MB
        assert report.untagged_window_end == datetime(2016, 7, 1).date()


class TestRendering:
    def test_report_states_that_nothing_changed(self, contribute_config, family):
        library = FakeLibrary([item("a", date=at(2010))])
        report, _ = build_inventory(library, contribute_config, family)
        text = render_report(report, family)
        assert "dry run" in text
        assert "Nothing has been copied" in text

    def test_report_lists_every_subject_even_at_zero(self, contribute_config, family):
        report, _ = build_inventory(FakeLibrary([]), contribute_config, family)
        text = render_report(report, family)
        for subject in family.subjects:
            assert subject.name in text

    def test_empty_library_renders(self, contribute_config, family):
        report, _ = build_inventory(FakeLibrary([]), contribute_config, family)
        assert render_report(report, family)

    def test_report_dict_is_json_serializable(self, contribute_config, family):
        import json

        library = FakeLibrary([item("a", date=at(2010)), item("b", date=None)])
        report, _ = build_inventory(library, contribute_config, family)
        assert json.loads(json.dumps(report_to_dict(report)))["scanned"] == 2


class TestDemoLibrary:
    """The demo exists to show a contributor how the rules behave, so it has to
    actually exercise all of them."""

    def test_covers_every_disposition(self, contribute_config, family):
        cutoff = datetime.combine(contribute_config.cutoff_date, datetime.min.time())
        library = build_demo_library(family, cutoff)
        report, _ = build_inventory(library, contribute_config, family)
        for disposition in Disposition:
            assert report.counts[disposition] > 0, f"demo never produces {disposition.name}"

    def test_is_deterministic(self, contribute_config, family):
        cutoff = datetime.combine(contribute_config.cutoff_date, datetime.min.time())
        first, _ = build_inventory(build_demo_library(family, cutoff), contribute_config, family)
        second, _ = build_inventory(build_demo_library(family, cutoff), contribute_config, family)
        assert report_to_dict(first) == report_to_dict(second)

    def test_tagged_post_cutoff_screenshot_is_excluded(self, contribute_config, family):
        """The demo's headline case, verified rather than assumed."""
        cutoff = datetime.combine(contribute_config.cutoff_date, datetime.min.time())
        library = build_demo_library(family, cutoff)
        _, decisions = build_inventory(library, contribute_config, family)
        screenshot = next(d for d in decisions if d.item.uuid == "post-005")
        assert screenshot.item.persons  # it really is tagged
        assert screenshot.disposition is Disposition.EXCLUDE_SCREENSHOT
