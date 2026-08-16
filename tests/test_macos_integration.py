"""macOS integration tests — the ones that need a real osxphotos.

Everything else in this suite runs anywhere, because the copy contract is pure.
These are the exceptions: they check the two things that cannot be verified
without macOS and an actual Photos library.

1. That the installed osxphotos still spells its export flags the way
   ``contribute/export.py`` expects.
2. That a real ``PhotoInfo`` still carries every property the contract reads.

Both are silent-failure risks. The adapter reads properties defensively so a
rename degrades toward copying less rather than crashing — which is the right
behaviour during an export, and exactly the wrong behaviour for noticing. These
tests are what make the degradation loud.

Point them at Photos libraries with:

    OSXPHOTOS_TEST_LIBRARIES=/path/to/dir/containing/photoslibrary/bundles

The osxphotos project ships 50-odd test libraries covering face tags, shared
items, cloud-only originals, live photos, and every Photos schema back to
macOS 10.12. They are ordinary directories, not the TCC-protected system
library, so they open without Full Disk Access and work on a headless runner.
See .github/workflows/tests.yml.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from contribute.export import check_flags, export_flags, osxphotos_help, suggest_flag
from contribute.library import PHOTOINFO_ATTRIBUTES, missing_attributes, open_library

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS")


def _libraries() -> list[Path]:
    root = os.environ.get("OSXPHOTOS_TEST_LIBRARIES")
    if not root:
        return []
    return sorted(Path(root).expanduser().glob("*.photoslibrary"))


LIBRARIES = _libraries()
needs_libraries = pytest.mark.skipif(
    not LIBRARIES, reason="set OSXPHOTOS_TEST_LIBRARIES to a directory of .photoslibrary bundles"
)
needs_cli = pytest.mark.skipif(
    shutil.which("osxphotos") is None, reason="osxphotos CLI not on PATH"
)


@needs_cli
class TestExportFlags:
    """Resolves the assumption export.py is built on."""

    def test_required_flags_exist_in_the_installed_version(self):
        help_text = osxphotos_help()
        missing, _ = check_flags(help_text, export_flags(Path("u.txt"), Path("r.csv")))
        if missing:
            detail = "\n".join(
                f"  {f.flag} ({f.purpose})"
                f"\n    closest: {', '.join(suggest_flag(f.flag, help_text)) or 'none'}"
                for f in missing
            )
            pytest.fail(f"osxphotos no longer supports flags the export needs:\n{detail}")

    @pytest.mark.xfail(strict=False, reason="optional flags degrade gracefully")
    def test_optional_flags_exist(self):
        _, missing = check_flags(osxphotos_help(), export_flags(Path("u.txt"), Path("r.csv")))
        assert not missing, f"optional flags unavailable: {[f.flag for f in missing]}"


@needs_libraries
@pytest.mark.parametrize("library_path", LIBRARIES, ids=lambda p: p.name)
class TestAgainstRealLibraries:
    def test_library_opens(self, library_path):
        assert open_library(library_path).description

    def test_every_depended_on_property_still_exists(self, library_path):
        """The check that makes a renamed osxphotos property loud.

        Reads the raw PhotoInfo rather than our PhotoItem, because the whole
        point is to see past the defensive defaults.
        """
        import osxphotos

        photos = osxphotos.PhotosDB(str(library_path)).photos(movies=True, intrash=True)
        if not photos:
            pytest.skip("library is empty")
        missing = missing_attributes(photos[0])
        assert not missing, (
            f"{library_path.name}: osxphotos PhotoInfo no longer has {missing}. "
            f"contribute/library.py is reading defaults for these, which silently "
            f"changes what the copy contract selects."
        )

    def test_conversion_produces_usable_items(self, library_path):
        library = open_library(library_path)
        items = list(library.items())
        if not items:
            pytest.skip("library is empty")
        for item in items:
            assert item.uuid
            assert isinstance(item.persons, tuple)
            assert item.filesize >= 0


@needs_libraries
class TestPropertiesCarryRealValues:
    """Existing isn't enough — a property can survive a rename and still read
    empty. These assert the values actually vary across the fixture set, which
    is what proves the contract is seeing real data."""

    def test_some_library_reports_person_tags(self):
        """Person tags decide everything after the cutoff date."""
        assert any(
            item.persons for path in LIBRARIES for item in open_library(path).items()
        ), "no person tags found in any library — the post-cutoff rule cannot be trusted"

    def test_some_library_reports_dates(self):
        assert any(item.date for path in LIBRARIES for item in open_library(path).items())

    def test_not_everything_reads_as_shared(self):
        """is_shared defaults to True when absent, which would exclude the
        entire library. If nothing reads as un-shared, the property broke."""
        assert any(
            not item.is_shared for path in LIBRARIES for item in open_library(path).items()
        ), "every item reads as shared — the 'shared' property is probably renamed"

    def test_attribute_list_is_not_empty(self):
        assert PHOTOINFO_ATTRIBUTES

    def test_no_fixture_library_reads_as_empty(self):
        """An empty-looking library turns the property checks into skips.

        That is how a real defect slipped through once already: the adapter
        asked osxphotos for ``intrash=True`` meaning "include trashed", when it
        actually means "only trashed". Every fixture read as nearly empty, the
        checks that mattered skipped, and the suite still reported green. An
        empty library is now a failure, not a skip.
        """
        empty = [p.name for p in LIBRARIES if not list(open_library(p).items())]
        assert not empty, (
            f"these fixture libraries read as empty: {empty}. "
            f"Either the fixtures did not download, or the adapter's query is "
            f"wrong — check OsxPhotosLibrary.items()."
        )
