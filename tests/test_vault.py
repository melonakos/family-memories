"""Tests for vault filing and verification.

The vault is the archive of record. These tests are mostly about one property:
it never loses or overwrites anything, whatever it is asked to do.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from conftest import requires_exiftool
from factories import make_image

from index.db import open_index
from mediafiles import sha256_file
from vault.filing import UNDATED_DIRECTORY, VaultError, file_asset, month_directory, target_path
from vault.verify import verify_vault

TAKEN = datetime(2015, 6, 15, 12, 0)


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    return root


@pytest.fixture
def index(tmp_path):
    with open_index(tmp_path / "index.db") as db:
        yield db


class TestMonthDirectory:
    def test_dated(self):
        assert month_directory(datetime(2015, 6, 15)) == "2015/06"

    def test_pads_single_digit_months(self):
        assert month_directory(datetime(2015, 1, 5)) == "2015/01"

    def test_undated_gets_its_own_bucket(self):
        """Undated files are archived, not filed under an invented date."""
        assert month_directory(None) == UNDATED_DIRECTORY


class TestFiling:
    def test_files_into_year_and_month(self, tmp_path, vault):
        source = make_image(tmp_path / "in" / "IMG_1.jpg")
        filed = file_asset(source, vault, TAKEN)
        assert filed.relative_path == "2015/06/IMG_1.jpg"
        assert (vault / "2015" / "06" / "IMG_1.jpg").is_file()

    def test_relative_path_uses_forward_slashes(self, tmp_path, vault):
        """The index travels between platforms; the separator must not."""
        source = make_image(tmp_path / "in" / "IMG_1.jpg")
        assert "\\" not in file_asset(source, vault, TAKEN).relative_path

    def test_source_is_left_untouched(self, tmp_path, vault):
        source = make_image(tmp_path / "in" / "IMG_1.jpg")
        before = sha256_file(source)
        file_asset(source, vault, TAKEN)
        assert source.is_file()
        assert sha256_file(source) == before

    def test_copy_is_byte_identical(self, tmp_path, vault):
        source = make_image(tmp_path / "in" / "IMG_1.jpg")
        filed = file_asset(source, vault, TAKEN)
        assert sha256_file(filed.vault_path) == sha256_file(source)
        assert filed.sha256 == sha256_file(source)

    def test_undated_file_goes_to_the_undated_bucket(self, tmp_path, vault):
        source = make_image(tmp_path / "in" / "IMG_1.jpg")
        filed = file_asset(source, vault, None)
        assert filed.relative_path == f"{UNDATED_DIRECTORY}/IMG_1.jpg"

    def test_dry_run_writes_nothing(self, tmp_path, vault):
        source = make_image(tmp_path / "in" / "IMG_1.jpg")
        filed = file_asset(source, vault, TAKEN, dry_run=True)
        assert filed.relative_path == "2015/06/IMG_1.jpg"
        assert not filed.vault_path.exists()
        assert not (vault / "2015").exists()

    def test_missing_source_raises(self, tmp_path, vault):
        with pytest.raises(VaultError, match="Not a file"):
            file_asset(tmp_path / "nope.jpg", vault, TAKEN)


class TestCollisions:
    def test_same_name_different_content_is_disambiguated(self, tmp_path, vault):
        """Two cameras both produce IMG_0001.jpg. Neither may be lost."""
        first = make_image(tmp_path / "a" / "IMG_0001.jpg", seed=1)
        second = make_image(tmp_path / "b" / "IMG_0001.jpg", seed=2)

        filed_first = file_asset(first, vault, TAKEN)
        filed_second = file_asset(second, vault, TAKEN)

        assert filed_first.relative_path != filed_second.relative_path
        assert not filed_first.renamed
        assert filed_second.renamed
        assert sha256_file(filed_first.vault_path) == sha256_file(first)
        assert sha256_file(filed_second.vault_path) == sha256_file(second)

    def test_disambiguated_name_is_deterministic(self, tmp_path, vault):
        """Derived from content, so a rerun lands in the same place."""
        first = make_image(tmp_path / "a" / "IMG_0001.jpg", seed=1)
        second = make_image(tmp_path / "b" / "IMG_0001.jpg", seed=2)
        file_asset(first, vault, TAKEN)
        once = file_asset(second, vault, TAKEN).relative_path
        again = target_path(vault, second, TAKEN, sha256_file(second))
        assert again.relative_to(vault).as_posix() == once

    def test_refiling_identical_content_is_a_no_op(self, tmp_path, vault):
        """An interrupted run re-files the same bytes; that must be harmless."""
        source = make_image(tmp_path / "in" / "IMG_1.jpg")
        first = file_asset(source, vault, TAKEN)
        second = file_asset(source, vault, TAKEN)
        assert first.relative_path == second.relative_path
        assert len(list((vault / "2015" / "06").iterdir())) == 1

    def test_never_overwrites_different_content(self, tmp_path, vault):
        """The append-only guarantee, asserted directly."""
        existing = vault / "2015" / "06" / "IMG_1.jpg"
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"the original, which must survive")

        source = make_image(tmp_path / "in" / "IMG_1.jpg")
        filed = file_asset(source, vault, TAKEN)

        assert existing.read_bytes() == b"the original, which must survive"
        assert filed.vault_path != existing


class TestVerify:
    def _fill(self, tmp_path, vault, index, count=2):
        for i in range(count):
            source = make_image(tmp_path / "in" / f"IMG_{i}.jpg", seed=i)
            filed = file_asset(source, vault, TAKEN)
            index.add_asset(
                sha256=filed.sha256,
                vault_path=filed.relative_path,
                original_filename=source.name,
                media_type="photo",
                filesize=filed.filesize,
                taken_at=TAKEN,
                taken_at_source="exif",
            )

    def test_clean_vault_passes(self, tmp_path, vault, index):
        self._fill(tmp_path, vault, index)
        result = verify_vault(vault, index)
        assert result.passed
        assert result.ok == result.checked == 2

    def test_detects_corruption(self, tmp_path, vault, index):
        self._fill(tmp_path, vault, index, count=1)
        target = next((vault / "2015" / "06").iterdir())
        # Same length, different bytes — size checks alone would miss this.
        target.write_bytes(b"x" * target.stat().st_size)
        result = verify_vault(vault, index)
        assert not result.passed
        assert len(result.corrupted) == 1

    def test_quick_mode_misses_same_size_corruption(self, tmp_path, vault, index):
        """Documents the tradeoff --quick makes, so nobody mistakes it for safe."""
        self._fill(tmp_path, vault, index, count=1)
        target = next((vault / "2015" / "06").iterdir())
        target.write_bytes(b"x" * target.stat().st_size)
        assert verify_vault(vault, index, deep=False).passed

    def test_detects_a_missing_file(self, tmp_path, vault, index):
        self._fill(tmp_path, vault, index, count=1)
        next((vault / "2015" / "06").iterdir()).unlink()
        assert verify_vault(vault, index).missing

    def test_detects_untracked_files(self, tmp_path, vault, index):
        """An ingest that copied then died before writing the index row."""
        self._fill(tmp_path, vault, index, count=1)
        make_image(vault / "2015" / "06" / "STRAY.jpg", seed=42)
        result = verify_vault(vault, index)
        assert result.untracked == ["2015/06/STRAY.jpg"]
        assert not result.passed


@requires_exiftool
class TestFilingRealMedia:
    def test_preserves_exif_through_the_copy(self, tmp_path, vault):
        """copy2, not a re-encode: the archived original must stay original."""
        from factories import make_dated_image

        from ingest.metadata import metadata_for, read_exiftool

        source = make_dated_image(tmp_path / "in" / "IMG_1.jpg", TAKEN)
        filed = file_asset(source, vault, TAKEN)
        exif = read_exiftool([filed.vault_path])
        meta = metadata_for(filed.vault_path, exif.get(str(filed.vault_path.resolve())))
        assert meta.taken_at == TAKEN
