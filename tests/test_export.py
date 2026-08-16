"""Tests for export command construction and its guardrails.

No osxphotos and no subprocess: what's checked here is that the command we
would hand to osxphotos is correct, that a version mismatch is caught before
anything runs, and that the selection list can never land on the drive.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from contribute.export import (
    DIRECTORY_TEMPLATE,
    ExportError,
    ExportFlag,
    build_export_command,
    check_destination,
    check_flags,
    export_flags,
    suggest_flag,
    supported_flags,
    write_uuid_file,
)
from contribute.models import Decision, Disposition, PhotoItem

HELP = """
Usage: osxphotos export [OPTIONS] DEST

Options:
  --uuid-from-file FILE   Export photos with UUIDs listed in FILE.
  --directory DIRECTORY   Export directory template.
  --download-missing      Download missing photos from iCloud.
  --sidecar FORMAT        Write sidecar (XMP, JSON, exiftool).
  --touch-file            Set file times to the photo date.
  --retry N               Retry failed exports N times.
  --report REPORT         Write an export report.
"""


def decision(uuid: str, include: bool) -> Decision:
    return Decision(
        item=PhotoItem(uuid=uuid, original_filename=f"{uuid}.HEIC", date=datetime(2010, 1, 1)),
        disposition=Disposition.INCLUDE_PRE_CUTOFF if include else Disposition.EXCLUDE_UNTAGGED,
    )


class TestCommandConstruction:
    def test_starts_with_the_export_subcommand(self, tmp_path):
        command = build_export_command(tmp_path, export_flags(tmp_path / "u.txt", None))
        assert command[:2] == ["osxphotos", "export"]
        assert command[2] == str(tmp_path)

    def test_restricts_the_export_to_the_selection_list(self, tmp_path):
        uuid_file = tmp_path / "u.txt"
        command = build_export_command(tmp_path, export_flags(uuid_file, None))
        assert "--uuid-from-file" in command
        assert str(uuid_file) in command

    def test_files_by_year_and_month_to_match_the_vault(self, tmp_path):
        command = build_export_command(tmp_path, export_flags(tmp_path / "u.txt", None))
        assert command[command.index("--directory") + 1] == DIRECTORY_TEMPLATE

    def test_downloads_icloud_originals(self, tmp_path):
        assert "--download-missing" in build_export_command(
            tmp_path, export_flags(tmp_path / "u.txt", None)
        )

    def test_writes_both_sidecar_formats(self, tmp_path):
        """XMP travels with the file into the vault; JSON rebuilds the manifest."""
        command = build_export_command(tmp_path, export_flags(tmp_path / "u.txt", None))
        assert command.count("--sidecar") == 2
        assert {"XMP", "JSON"} <= set(command)

    def test_report_flag_only_when_a_path_is_given(self, tmp_path):
        without = build_export_command(tmp_path, export_flags(tmp_path / "u.txt", None))
        assert "--report" not in without
        with_report = build_export_command(
            tmp_path, export_flags(tmp_path / "u.txt", tmp_path / "r.csv")
        )
        assert "--report" in with_report


class TestFlagVerification:
    """osxphotos options move between releases. Discovering that partway
    through an export onto someone's drive is not acceptable."""

    def test_parses_long_options_from_help(self):
        assert "--download-missing" in supported_flags(HELP)
        assert "--uuid-from-file" in supported_flags(HELP)

    def test_all_intended_flags_are_present_in_a_matching_version(self, tmp_path):
        missing_required, missing_optional = check_flags(
            HELP, export_flags(tmp_path / "u.txt", tmp_path / "r.csv")
        )
        assert missing_required == []
        assert missing_optional == []

    def test_missing_required_flag_is_reported(self):
        flags = [ExportFlag("--uuid-from-file", ("u.txt",), required=True)]
        missing_required, _ = check_flags("Options:\n  --directory X\n", flags)
        assert [f.flag for f in missing_required] == ["--uuid-from-file"]

    def test_missing_optional_flag_is_separated(self):
        flags = [ExportFlag("--touch-file", required=False)]
        missing_required, missing_optional = check_flags("Options:\n  --directory X\n", flags)
        assert missing_required == []
        assert [f.flag for f in missing_optional] == ["--touch-file"]

    def test_suggests_close_spellings(self):
        assert "--touch-file" in suggest_flag("--touch-files", HELP)

    def test_no_suggestion_for_something_unrelated(self):
        assert suggest_flag("--completely-different-thing", HELP) == []


class TestUuidFile:
    def test_writes_only_included_uuids(self, tmp_path):
        decisions = [decision("a", True), decision("b", False), decision("c", True)]
        path = tmp_path / "work" / "uuids.txt"
        count = write_uuid_file(decisions, path, tmp_path / "drive")
        assert count == 2
        assert path.read_text(encoding="utf-8").split() == ["a", "c"]

    def test_creates_the_working_directory(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "uuids.txt"
        write_uuid_file([decision("a", True)], path, tmp_path / "drive")
        assert path.is_file()

    def test_refuses_to_write_onto_the_destination_drive(self, tmp_path):
        """The selection list predates the review. Diffing it against what
        survives would itemize exactly what the contributor withheld."""
        drive = tmp_path / "drive"
        drive.mkdir()
        with pytest.raises(ExportError, match="withhold"):
            write_uuid_file([decision("a", True)], drive / "uuids.txt", drive)

    def test_refuses_nested_paths_inside_the_destination(self, tmp_path):
        drive = tmp_path / "drive"
        (drive / "sub").mkdir(parents=True)
        with pytest.raises(ExportError):
            write_uuid_file([decision("a", True)], drive / "sub" / "u.txt", drive)

    def test_allows_a_sibling_directory(self, tmp_path):
        drive = tmp_path / "drive"
        drive.mkdir()
        write_uuid_file([decision("a", True)], tmp_path / "work" / "u.txt", drive)
        assert (tmp_path / "work" / "u.txt").is_file()


class TestDestinationChecks:
    def test_empty_directory_is_clean(self, tmp_path):
        assert check_destination(tmp_path) == []

    def test_nonempty_directory_warns(self, tmp_path):
        (tmp_path / "stray.jpg").write_bytes(b"x")
        assert any("not empty" in w for w in check_destination(tmp_path))

    def test_refuses_to_export_into_a_photos_library(self, tmp_path):
        """Writing into anyone's library violates the read-only promise."""
        target = tmp_path / "Photos.photoslibrary" / "export"
        with pytest.raises(ExportError, match="Photos library"):
            check_destination(target)

    def test_missing_parent_is_fatal(self, tmp_path):
        with pytest.raises(ExportError, match="parent directory"):
            check_destination(tmp_path / "no" / "such" / "place")

    def test_a_file_where_a_directory_belongs_is_fatal(self, tmp_path):
        path = tmp_path / "afile"
        path.write_bytes(b"x")
        with pytest.raises(ExportError, match="not a directory"):
            check_destination(path)
