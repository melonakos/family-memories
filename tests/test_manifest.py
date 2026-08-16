"""Tests for manifest generation and verification.

Two things are being protected here: the integrity guarantee (checksums catch a
bad copy) and the privacy guarantee (nothing records what the contributor
removed during review).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contribute.manifest import (
    MANIFEST_NAME,
    ManifestError,
    build_manifest,
    iter_media,
    read_manifest,
    read_sidecar_metadata,
    remove_orphaned_sidecars,
    sha256_file,
    verify_manifest,
    write_manifest,
)

# sha256 of b"hello"
HELLO_SHA = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


@pytest.fixture
def drive(tmp_path: Path) -> Path:
    """An exported drive: two photos and a video, with JSON sidecars."""
    root = tmp_path / "drive"
    (root / "2010" / "06").mkdir(parents=True)
    (root / "2018" / "03").mkdir(parents=True)

    (root / "2010" / "06" / "IMG_0001.HEIC").write_bytes(b"hello")
    (root / "2010" / "06" / "IMG_0001.HEIC.json").write_text(
        json.dumps(
            {
                "date": "2010-06-15T12:00:00",
                "albums": ["Summer", "Trip"],
                "persons": ["Subject One", "Subject Two"],
            }
        ),
        encoding="utf-8",
    )
    (root / "2010" / "06" / "IMG_0002.MOV").write_bytes(b"video-bytes")
    (root / "2018" / "03" / "IMG_1001.HEIC").write_bytes(b"another")
    return root


class TestChecksum:
    def test_known_value(self, tmp_path):
        path = tmp_path / "f.bin"
        path.write_bytes(b"hello")
        assert sha256_file(path) == HELLO_SHA

    def test_large_file_streams_correctly(self, tmp_path):
        """Files here are gigabytes; the chunked read must match a one-shot hash."""
        import hashlib

        path = tmp_path / "big.bin"
        payload = b"x" * (3 * 1024 * 1024 + 17)
        path.write_bytes(payload)
        assert sha256_file(path) == hashlib.sha256(payload).hexdigest()


class TestIterMedia:
    def test_finds_media_recursively(self, drive):
        names = {p.name for p in iter_media(drive)}
        assert names == {"IMG_0001.HEIC", "IMG_0002.MOV", "IMG_1001.HEIC"}

    def test_skips_sidecars_and_dotfiles(self, drive):
        (drive / ".DS_Store").write_bytes(b"junk")
        assert not any(p.name.startswith(".") for p in iter_media(drive))
        assert not any(p.suffix == ".json" for p in iter_media(drive))

    def test_skips_the_manifest_itself(self, drive):
        (drive / MANIFEST_NAME).write_text("path,sha256\n", encoding="utf-8")
        assert MANIFEST_NAME not in {p.name for p in iter_media(drive)}

    def test_extension_matching_is_case_insensitive(self, tmp_path):
        root = tmp_path / "d"
        root.mkdir()
        (root / "A.JPG").write_bytes(b"x")
        (root / "b.jpeg").write_bytes(b"x")
        assert len(list(iter_media(root))) == 2


class TestSidecars:
    def test_reads_metadata(self, drive):
        data = read_sidecar_metadata(drive / "2010" / "06" / "IMG_0001.HEIC")
        assert data["persons"] == "Subject One; Subject Two"
        assert data["albums"] == "Summer; Trip"

    def test_missing_sidecar_is_not_an_error(self, drive):
        assert read_sidecar_metadata(drive / "2018" / "03" / "IMG_1001.HEIC") == {}

    def test_corrupt_sidecar_is_not_an_error(self, drive):
        """Metadata is a nicety; the file and its checksum are what matter."""
        (drive / "2018" / "03" / "IMG_1001.HEIC.json").write_text("{not json", encoding="utf-8")
        assert read_sidecar_metadata(drive / "2018" / "03" / "IMG_1001.HEIC") == {}

    def test_dropped_extension_naming_is_found(self, tmp_path):
        root = tmp_path / "d"
        root.mkdir()
        (root / "IMG_9.HEIC").write_bytes(b"x")
        (root / "IMG_9.json").write_text(json.dumps({"persons": ["A"]}), encoding="utf-8")
        assert read_sidecar_metadata(root / "IMG_9.HEIC")["persons"] == "A"


class TestOrphanCleanup:
    """A sidecar whose photo was deleted during review is itself a record of
    the deletion. It gets removed, and the count is never surfaced."""

    def test_orphan_is_deleted(self, drive):
        (drive / "2010" / "06" / "IMG_0001.HEIC").unlink()
        remove_orphaned_sidecars(drive)
        assert not (drive / "2010" / "06" / "IMG_0001.HEIC.json").exists()

    def test_live_sidecars_are_kept(self, drive):
        remove_orphaned_sidecars(drive)
        assert (drive / "2010" / "06" / "IMG_0001.HEIC.json").exists()

    def test_returns_nothing(self, drive):
        """Deliberate: a return value would be the removal count, which is
        exactly what must not leak. See the manifest module docstring."""
        (drive / "2010" / "06" / "IMG_0001.HEIC").unlink()
        assert remove_orphaned_sidecars(drive) is None

    def test_dropped_extension_orphan_is_deleted(self, tmp_path):
        root = tmp_path / "d"
        root.mkdir()
        (root / "IMG_9.json").write_text("{}", encoding="utf-8")
        remove_orphaned_sidecars(root)
        assert not (root / "IMG_9.json").exists()


class TestBuildManifest:
    def test_lists_every_media_file(self, drive):
        rows = build_manifest(drive)
        assert {r.path for r in rows} == {
            "2010/06/IMG_0001.HEIC",
            "2010/06/IMG_0002.MOV",
            "2018/03/IMG_1001.HEIC",
        }

    def test_paths_use_forward_slashes(self, drive):
        """The manifest travels between macOS and Windows."""
        assert all("\\" not in row.path for row in build_manifest(drive))

    def test_records_checksums_and_sizes(self, drive):
        row = next(r for r in build_manifest(drive) if r.path.endswith("IMG_0001.HEIC"))
        assert row.sha256 == HELLO_SHA
        assert row.size_bytes == 5

    def test_carries_sidecar_metadata(self, drive):
        row = next(r for r in build_manifest(drive) if r.path.endswith("IMG_0001.HEIC"))
        assert row.persons == "Subject One; Subject Two"

    def test_regenerates_from_what_remains_after_review(self, drive):
        """The core privacy guarantee: a removed photo leaves no trace anywhere,
        so the manifest cannot be diffed to reveal what was withheld."""
        build_manifest(drive)
        (drive / "2010" / "06" / "IMG_0001.HEIC").unlink()

        rows = build_manifest(drive)
        paths = {r.path for r in rows}
        assert "2010/06/IMG_0001.HEIC" not in paths
        assert len(rows) == 2

        # And nothing on the drive still references the removed item.
        remaining = " ".join(p.name for p in drive.rglob("*") if p.is_file())
        assert "IMG_0001" not in remaining

    def test_missing_directory_raises(self, tmp_path):
        with pytest.raises(ManifestError, match="Not a directory"):
            build_manifest(tmp_path / "nope")


class TestWriteAndRead:
    def test_round_trip(self, drive):
        write_manifest(build_manifest(drive), drive)
        rows = read_manifest(drive)
        assert len(rows) == 3
        assert next(r for r in rows if r.path.endswith("IMG_0001.HEIC")).sha256 == HELLO_SHA

    def test_written_to_the_drive_root(self, drive):
        assert write_manifest(build_manifest(drive), drive) == drive / MANIFEST_NAME

    def test_read_without_a_manifest_raises(self, drive):
        with pytest.raises(ManifestError, match="Run the manifest step"):
            read_manifest(drive)

    def test_unicode_survives_the_round_trip(self, tmp_path):
        root = tmp_path / "d"
        root.mkdir()
        (root / "IMG_1.JPG").write_bytes(b"x")
        (root / "IMG_1.JPG.json").write_text(
            json.dumps({"persons": ["Zoë Ñuñez"]}), encoding="utf-8"
        )
        write_manifest(build_manifest(root), root)
        assert read_manifest(root)[0].persons == "Zoë Ñuñez"


class TestVerify:
    def test_clean_drive_passes(self, drive):
        write_manifest(build_manifest(drive), drive)
        result = verify_manifest(drive)
        assert result.passed
        assert result.ok == result.checked == 3

    def test_detects_corruption(self, drive):
        write_manifest(build_manifest(drive), drive)
        (drive / "2010" / "06" / "IMG_0001.HEIC").write_bytes(b"corrupted")
        result = verify_manifest(drive)
        assert not result.passed
        assert result.corrupted == ["2010/06/IMG_0001.HEIC"]

    def test_detects_a_missing_file(self, drive):
        write_manifest(build_manifest(drive), drive)
        (drive / "2018" / "03" / "IMG_1001.HEIC").unlink()
        result = verify_manifest(drive)
        assert result.missing == ["2018/03/IMG_1001.HEIC"]

    def test_detects_a_file_not_in_the_manifest(self, drive):
        """Catches a partial re-copy landing files the manifest never saw."""
        write_manifest(build_manifest(drive), drive)
        (drive / "2018" / "03" / "EXTRA.JPG").write_bytes(b"surprise")
        result = verify_manifest(drive)
        assert result.unlisted == ["2018/03/EXTRA.JPG"]
        assert not result.passed

    def test_same_bytes_in_a_new_location_is_still_flagged(self, drive):
        """A duplicate with a matching checksum is still an unexpected file."""
        write_manifest(build_manifest(drive), drive)
        (drive / "2010" / "06" / "COPY.HEIC").write_bytes(b"hello")
        assert verify_manifest(drive).unlisted == ["2010/06/COPY.HEIC"]
