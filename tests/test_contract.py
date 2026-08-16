"""Tests for the copy contract.

These are the tests that matter most in the repo. Every case here corresponds
to a promise made to a contributor about what leaves their photo library, and
a regression in any of them is a broken agreement rather than a bug.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from conftest import CUTOFF, at

from contribute.contract import add_months, decide
from contribute.models import Disposition, PhotoItem
from settings import ContributeConfig


def item(**kwargs) -> PhotoItem:
    defaults = {"uuid": "u1", "original_filename": "IMG_0001.HEIC", "date": at(2010)}
    return PhotoItem(**{**defaults, **kwargs})


class TestBeforeCutoff:
    def test_untagged_photo_is_copied(self, contribute_config, family):
        decision = decide(item(date=at(2009)), contribute_config, family)
        assert decision.disposition is Disposition.INCLUDE_PRE_CUTOFF

    def test_screenshot_is_copied(self, contribute_config, family):
        """The screenshot override is explicitly post-cutoff only."""
        decision = decide(
            item(date=at(2012), is_screenshot=True, original_filename="Screenshot.png"),
            contribute_config,
            family,
        )
        assert decision.disposition is Disposition.INCLUDE_PRE_CUTOFF

    def test_video_is_copied(self, contribute_config, family):
        decision = decide(
            item(date=at(2011), is_movie=True, is_photo=False), contribute_config, family
        )
        assert decision.is_include


class TestCutoffBoundary:
    """The cutoff is inclusive of the post-cutoff rules: on the day itself,
    tagging is already required. Off-by-one here silently copies or drops a
    day's worth of someone's library."""

    def test_day_before_cutoff_is_copied_untagged(self, contribute_config, family):
        decision = decide(item(date=datetime(2014, 12, 31, 23, 59)), contribute_config, family)
        assert decision.disposition is Disposition.INCLUDE_PRE_CUTOFF

    def test_cutoff_day_requires_a_tag(self, contribute_config, family):
        decision = decide(item(date=datetime(2015, 1, 1, 0, 0)), contribute_config, family)
        assert decision.disposition is Disposition.EXCLUDE_UNTAGGED

    def test_cutoff_day_with_tag_is_copied(self, contribute_config, family):
        decision = decide(
            item(date=datetime(2015, 1, 1, 0, 0), persons=("Subject Two",)),
            contribute_config,
            family,
        )
        assert decision.disposition is Disposition.INCLUDE_TAGGED


class TestAfterCutoff:
    def test_tagged_with_subject_is_copied(self, contribute_config, family):
        decision = decide(item(date=at(2018), persons=("Subject One",)), contribute_config, family)
        assert decision.disposition is Disposition.INCLUDE_TAGGED
        assert decision.matched_subjects == ("Subject One",)

    def test_untagged_is_excluded(self, contribute_config, family):
        decision = decide(item(date=at(2018)), contribute_config, family)
        assert decision.disposition is Disposition.EXCLUDE_UNTAGGED

    def test_tagged_only_with_a_non_subject_is_excluded(self, contribute_config, family):
        decision = decide(
            item(date=at(2018), persons=("Someone Else",)), contribute_config, family
        )
        assert decision.disposition is Disposition.EXCLUDE_UNTAGGED

    def test_subject_alongside_others_still_qualifies(self, contribute_config, family):
        decision = decide(
            item(date=at(2018), persons=("Someone Else", "Subject Two")),
            contribute_config,
            family,
        )
        assert decision.disposition is Disposition.INCLUDE_TAGGED
        assert decision.matched_subjects == ("Subject Two",)

    def test_multiple_subjects_reported_in_roster_order(self, contribute_config, family):
        decision = decide(
            item(date=at(2018), persons=("Subject Three", "Subject One")),
            contribute_config,
            family,
        )
        assert decision.matched_subjects == ("Subject One", "Subject Three")


class TestScreenshotOverride:
    """Rule 3 of the contract: post-cutoff screenshots are never copied, even
    when a subject's face is in them. The override runs ahead of the tag rule."""

    def test_tagged_screenshot_after_cutoff_is_still_excluded(self, contribute_config, family):
        decision = decide(
            item(date=at(2018), is_screenshot=True, persons=("Subject One", "Subject Two")),
            contribute_config,
            family,
        )
        assert decision.disposition is Disposition.EXCLUDE_SCREENSHOT

    def test_override_can_be_disabled_by_config(self, family):
        config = ContributeConfig(cutoff_date=CUTOFF, exclude_screenshots_after_cutoff=False)
        decision = decide(
            item(date=at(2018), is_screenshot=True, persons=("Subject One",)), config, family
        )
        assert decision.disposition is Disposition.INCLUDE_TAGGED

    def test_untagged_screenshot_excluded_as_screenshot(self, contribute_config, family):
        """The reason matters: it's what the inventory reports back."""
        decision = decide(item(date=at(2018), is_screenshot=True), contribute_config, family)
        assert decision.disposition is Disposition.EXCLUDE_SCREENSHOT


class TestAbsoluteExclusions:
    """Nothing pulls these back in — not a date before the cutoff, not a tag."""

    @pytest.mark.parametrize(
        ("field", "expected"),
        [
            ("is_hidden", Disposition.EXCLUDE_ALBUM),
            ("in_trash", Disposition.EXCLUDE_ALBUM),
            ("is_shared", Disposition.EXCLUDE_NOT_OWNED),
        ],
    )
    def test_excluded_despite_being_pre_cutoff_and_tagged(
        self, contribute_config, family, field, expected
    ):
        decision = decide(
            item(date=at(2009), persons=("Subject One",), **{field: True}),
            contribute_config,
            family,
        )
        assert decision.disposition is expected

    def test_excluded_album_by_name(self, contribute_config, family):
        decision = decide(
            item(date=at(2009), albums=("Vacation", "Recently Deleted")), contribute_config, family
        )
        assert decision.disposition is Disposition.EXCLUDE_ALBUM

    def test_album_matching_ignores_case_and_spacing(self, contribute_config, family):
        decision = decide(
            item(date=at(2009), albums=("  recently   deleted ",)), contribute_config, family
        )
        assert decision.disposition is Disposition.EXCLUDE_ALBUM

    def test_shared_exclusion_can_be_disabled(self, family):
        config = ContributeConfig(cutoff_date=CUTOFF, exclude_not_owned=False)
        decision = decide(item(date=at(2009), is_shared=True), config, family)
        assert decision.disposition is Disposition.INCLUDE_PRE_CUTOFF


class TestUnknownDate:
    """Ground rule 4. An item with no trustworthy date can't be judged against
    a date cutoff, so it goes to a human rather than being guessed either way."""

    def test_no_date_goes_to_review(self, contribute_config, family):
        decision = decide(item(date=None), contribute_config, family)
        assert decision.disposition is Disposition.REVIEW_UNKNOWN_DATE
        assert not decision.is_include

    def test_no_date_with_subject_tag_still_goes_to_review(self, contribute_config, family):
        decision = decide(item(date=None, persons=("Subject One",)), contribute_config, family)
        assert decision.disposition is Disposition.REVIEW_UNKNOWN_DATE

    def test_absolute_exclusions_still_win_over_review(self, contribute_config, family):
        """A hidden item with no date is excluded, not queued for a decision —
        the contributor already answered this one."""
        decision = decide(item(date=None, is_hidden=True), contribute_config, family)
        assert decision.disposition is Disposition.EXCLUDE_ALBUM


class TestTagNormalization:
    """Face tags drift over a decade of hand-typing. Matching the raw string
    silently drops photos, which is the worst possible failure mode here."""

    @pytest.mark.parametrize(
        "tag",
        ["Subject One", "subject one", "SUBJECT ONE", "  Subject   One  ", "sUbJeCt oNe"],
    )
    def test_case_and_whitespace_variants_match(self, contribute_config, family, tag):
        decision = decide(item(date=at(2018), persons=(tag,)), contribute_config, family)
        assert decision.disposition is Disposition.INCLUDE_TAGGED

    def test_configured_alias_matches(self, contribute_config, family):
        decision = decide(item(date=at(2018), persons=("Trey",)), contribute_config, family)
        assert decision.matched_subjects == ("Subject Three",)

    def test_alias_reports_the_canonical_name(self, contribute_config, family):
        decision = decide(item(date=at(2018), persons=("sub one",)), contribute_config, family)
        assert decision.matched_subjects == ("Subject One",)

    def test_unrelated_name_does_not_match(self, contribute_config, family):
        decision = decide(
            item(date=at(2018), persons=("Subject Onederful",)), contribute_config, family
        )
        assert decision.disposition is Disposition.EXCLUDE_UNTAGGED


class TestUntaggedWindow:
    def test_untagged_just_after_cutoff_is_flagged(self, contribute_config, family):
        decision = decide(item(date=datetime(2015, 6, 1)), contribute_config, family)
        assert decision.disposition is Disposition.EXCLUDE_UNTAGGED
        assert decision.in_untagged_window

    def test_untagged_long_after_cutoff_is_not_flagged(self, contribute_config, family):
        decision = decide(item(date=at(2020)), contribute_config, family)
        assert not decision.in_untagged_window

    def test_window_boundary_is_exclusive(self, contribute_config, family):
        """18 months from 2015-01-01 is 2016-07-01, which is outside the window."""
        outside = decide(item(date=datetime(2016, 7, 1)), contribute_config, family)
        inside = decide(item(date=datetime(2016, 6, 30)), contribute_config, family)
        assert not outside.in_untagged_window
        assert inside.in_untagged_window


class TestAddMonths:
    def test_simple(self):
        assert add_months(date(2015, 1, 1), 18) == date(2016, 7, 1)

    def test_zero_is_identity(self):
        assert add_months(date(2015, 3, 15), 0) == date(2015, 3, 15)

    def test_year_rollover(self):
        assert add_months(date(2015, 12, 1), 1) == date(2016, 1, 1)

    def test_clamps_to_end_of_short_month(self):
        """Jan 31 + 1 month has no valid answer; clamping beats ValueError."""
        assert add_months(date(2015, 1, 31), 1) == date(2015, 2, 28)
        assert add_months(date(2016, 1, 31), 1) == date(2016, 2, 29)

    def test_exact_multiple_of_twelve(self):
        assert add_months(date(2015, 5, 10), 24) == date(2017, 5, 10)
