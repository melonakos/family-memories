"""Tests for the osxphotos adapter.

osxphotos isn't installed here (it's macOS-only), so these exercise the
conversion layer against stand-in objects. What's being checked is that a
renamed or absent property degrades toward copying *less*, never more.
"""

from __future__ import annotations

import sys
from datetime import datetime

import pytest

from contribute.library import (
    FakeLibrary,
    LibraryError,
    _looks_like_screenshot,
    open_library,
    photo_to_item,
)


class FakePhotoInfo:
    """Stands in for an osxphotos PhotoInfo."""

    def __init__(self, **kwargs):
        self.uuid = kwargs.pop("uuid", "abc-123")
        self.original_filename = kwargs.pop("original_filename", "IMG_0001.HEIC")
        self.date = kwargs.pop("date", datetime(2012, 5, 1, 9, 30))
        self.persons = kwargs.pop("persons", [])
        self.albums = kwargs.pop("albums", [])
        self.screenshot = kwargs.pop("screenshot", False)
        self.hidden = kwargs.pop("hidden", False)
        self.intrash = kwargs.pop("intrash", False)
        self.shared = kwargs.pop("shared", False)
        self.isphoto = kwargs.pop("isphoto", True)
        self.ismovie = kwargs.pop("ismovie", False)
        self.live_photo = kwargs.pop("live_photo", False)
        self.ismissing = kwargs.pop("ismissing", False)
        self.original_filesize = kwargs.pop("original_filesize", 1024)
        self.path = kwargs.pop("path", "/Users/x/Pictures/IMG_0001.HEIC")
        for key, value in kwargs.items():
            setattr(self, key, value)


class Minimal:
    """A PhotoInfo missing everything but a uuid — the renamed-property case."""

    uuid = "only-a-uuid"


class TestConversion:
    def test_copies_the_basics(self):
        item = photo_to_item(FakePhotoInfo(persons=["A", "B"], albums=["Trip"]))
        assert item.uuid == "abc-123"
        assert item.original_filename == "IMG_0001.HEIC"
        assert item.date == datetime(2012, 5, 1, 9, 30)
        assert item.persons == ("A", "B")
        assert item.albums == ("Trip",)

    def test_drops_blank_person_tags(self):
        item = photo_to_item(FakePhotoInfo(persons=["A", "", None, "  "]))
        assert item.persons == ("A",)

    def test_falls_back_to_filename_when_original_is_absent(self):
        photo = FakePhotoInfo(original_filename="")
        photo.filename = "IMG_9.HEIC"
        assert photo_to_item(photo).original_filename == "IMG_9.HEIC"

    def test_media_flags(self):
        item = photo_to_item(FakePhotoInfo(ismovie=True, isphoto=False, live_photo=True))
        assert item.is_movie and item.is_live_photo and not item.is_photo

    def test_missing_original_is_flagged_for_download(self):
        assert photo_to_item(FakePhotoInfo(ismissing=True)).is_missing

    def test_no_path_becomes_none(self):
        assert photo_to_item(FakePhotoInfo(path=None)).path is None


class TestDateHandling:
    def test_epoch_placeholder_is_treated_as_unknown(self):
        """Photos stores an undated import as 1970-01-01. Passing that through
        would sail it under any modern cutoff and copy it silently."""
        assert photo_to_item(FakePhotoInfo(date=datetime(1970, 1, 1))).date is None

    def test_pre_1970_is_also_rejected(self):
        assert photo_to_item(FakePhotoInfo(date=datetime(1969, 6, 1))).date is None

    def test_a_real_date_survives(self):
        assert photo_to_item(FakePhotoInfo(date=datetime(1998, 7, 4))).date == datetime(1998, 7, 4)

    def test_a_non_datetime_becomes_none(self):
        assert photo_to_item(FakePhotoInfo(date="sometime in 2012")).date is None


class TestScreenshotDetection:
    @pytest.mark.parametrize(
        "name",
        [
            "Screenshot 2016-03-02 at 10.14.51.png",
            "screenshot.png",
            "Screen Shot 2016-03-02 at 10.14.51.png",
            "Simulator Screen Shot - iPhone.png",
        ],
    )
    def test_filename_heuristic_catches_known_shapes(self, name):
        assert _looks_like_screenshot(name)

    @pytest.mark.parametrize("name", ["IMG_0001.HEIC", "screen-porch.jpg", "DSC_1234.NEF"])
    def test_ordinary_photos_are_not_screenshots(self, name):
        assert not _looks_like_screenshot(name)

    def test_the_property_alone_is_enough(self):
        assert photo_to_item(FakePhotoInfo(screenshot=True)).is_screenshot

    def test_the_filename_alone_is_enough(self):
        """Belt and braces: a false positive only excludes one more post-cutoff
        screenshot, which is the conservative direction."""
        item = photo_to_item(FakePhotoInfo(screenshot=False, original_filename="Screenshot 1.png"))
        assert item.is_screenshot


class TestDefensiveDefaults:
    """A property osxphotos renames must never silently widen the copy."""

    def test_absent_shared_flag_assumes_not_ours_to_give(self):
        assert photo_to_item(Minimal()).is_shared is True

    def test_absent_date_becomes_unknown_not_a_guess(self):
        assert photo_to_item(Minimal()).date is None

    def test_absent_flags_default_to_false(self):
        item = photo_to_item(Minimal())
        assert not item.is_screenshot
        assert not item.is_hidden
        assert not item.in_trash

    def test_absent_filesize_is_zero_not_an_error(self):
        assert photo_to_item(Minimal()).filesize == 0

    def test_none_filesize_is_handled(self):
        assert photo_to_item(FakePhotoInfo(original_filesize=None)).filesize == 0


class TestFakeLibrary:
    def test_yields_its_items(self):
        library = FakeLibrary([photo_to_item(FakePhotoInfo())])
        assert len(list(library.items())) == 1

    def test_is_re_iterable(self):
        """build_inventory and export each walk the library; a one-shot
        generator would silently yield nothing the second time."""
        library = FakeLibrary([photo_to_item(FakePhotoInfo())])
        assert len(list(library.items())) == len(list(library.items())) == 1

    def test_has_a_description(self):
        assert FakeLibrary([], description="demo").description == "demo"


@pytest.mark.skipif(sys.platform == "darwin", reason="checks the non-macOS message")
class TestPlatformGuard:
    def test_explains_itself_off_macos(self):
        with pytest.raises(LibraryError, match="requires macOS"):
            open_library()
