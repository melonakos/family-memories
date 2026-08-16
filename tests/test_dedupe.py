"""Tests for duplicate detection.

The asymmetry these tests protect: importing a duplicate is untidy, but
*linking* something that wasn't a duplicate silently removes a photograph from
the archive and nobody finds out. So the automatic path is narrow and
everything else escalates to a person.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from factories import downscale, make_image

from index.models import Asset
from ingest.dedupe import (
    Verdict,
    classify,
    compute_phash,
    hamming_distance,
    is_strictly_smaller,
)


def asset(
    *,
    id: int = 1,
    phash: str = "de8f2130da8607f9",
    width: int | None = 4000,
    height: int | None = 3000,
    filesize: int = 5_000_000,
    sha: str = "a" * 64,
) -> Asset:
    return Asset(
        id=id,
        sha256=sha,
        vault_path="2015/06/IMG_0001.JPG",
        original_filename="IMG_0001.JPG",
        media_type="photo",
        filesize=filesize,
        taken_at_source="exif",
        imported_at="2026-01-01T00:00:00+00:00",
        phash=phash,
        taken_at=datetime(2015, 6, 15),
        width=width,
        height=height,
    )


class TestHammingDistance:
    def test_identical_hashes(self):
        assert hamming_distance("ff00ff00ff00ff00", "ff00ff00ff00ff00") == 0

    def test_one_bit_apart(self):
        assert hamming_distance("0000000000000000", "0000000000000001") == 1

    @pytest.mark.parametrize(
        ("left", "right"),
        [("", "ff00"), ("ff00", ""), ("ff00", "ff0000"), ("zzzz", "ff00")],
    )
    def test_unusable_input_never_reads_as_a_match(self, left, right):
        """Malformed hashes must not accidentally look identical."""
        assert hamming_distance(left, right) > 64


class TestStrictlySmaller:
    def test_smaller_in_every_way(self):
        assert is_strictly_smaller(1000, 750, 100_000, asset())

    def test_same_dimensions_is_not_smaller(self):
        assert not is_strictly_smaller(4000, 3000, 100_000, asset())

    def test_one_dimension_larger_is_not_smaller(self):
        """A crop can be smaller one way and bigger the other. Not a twin."""
        assert not is_strictly_smaller(5000, 100, 100_000, asset())

    def test_bigger_file_is_not_smaller(self):
        assert not is_strictly_smaller(1000, 750, 9_000_000, asset())

    @pytest.mark.parametrize("missing", [{"width": None}, {"height": None}])
    def test_unknown_master_dimensions_block_the_shortcut(self, missing):
        """An unknown is not evidence. Without dimensions, no auto-link."""
        assert not is_strictly_smaller(1000, 750, 100, asset(**missing))

    def test_unknown_candidate_dimensions_block_the_shortcut(self):
        assert not is_strictly_smaller(None, None, 100, asset())


class TestClassify:
    def test_exact_checksum_match_wins_immediately(self):
        result = classify(
            sha256="a" * 64,
            phash="de8f2130da8607f9",
            width=4000,
            height=3000,
            filesize=5_000_000,
            exact_match=asset(),
            hashed_assets=[],
        )
        assert result.verdict is Verdict.EXACT_DUPLICATE
        assert result.distance == 0

    def test_nothing_similar_is_new(self):
        result = classify(
            sha256="b" * 64,
            phash="0000000000000000",
            width=4000,
            height=3000,
            filesize=5_000_000,
            exact_match=None,
            hashed_assets=[asset(phash="ffffffffffffffff")],
        )
        assert result.verdict is Verdict.NEW

    def test_empty_archive_is_new(self):
        result = classify(
            sha256="b" * 64,
            phash="de8f2130da8607f9",
            width=100,
            height=100,
            filesize=100,
            exact_match=None,
            hashed_assets=[],
        )
        assert result.verdict is Verdict.NEW

    def test_no_phash_skips_similarity_entirely(self):
        """Video and undecodable formats get exact-hash dedupe only."""
        result = classify(
            sha256="b" * 64,
            phash=None,
            width=None,
            height=None,
            filesize=100,
            exact_match=None,
            hashed_assets=[asset()],
        )
        assert result.verdict is Verdict.NEW

    def test_smaller_copy_is_linked_as_a_twin(self):
        result = classify(
            sha256="b" * 64,
            phash="de8f2130da8607f9",
            width=1000,
            height=750,
            filesize=100_000,
            exact_match=None,
            hashed_assets=[asset()],
        )
        assert result.verdict is Verdict.LOW_RES_TWIN
        assert result.master.id == 1

    def test_similar_at_the_same_size_goes_to_review(self):
        """A burst neighbour hashes like its siblings. Never auto-resolved."""
        result = classify(
            sha256="b" * 64,
            phash="de8f2130da8607f9",
            width=4000,
            height=3000,
            filesize=5_100_000,
            exact_match=None,
            hashed_assets=[asset()],
        )
        assert result.verdict is Verdict.AMBIGUOUS_MATCH
        assert result.verdict.needs_review

    def test_a_better_copy_arriving_goes_to_review(self):
        """The vault never replaces a file, so promotion is a human decision."""
        result = classify(
            sha256="b" * 64,
            phash="de8f2130da8607f9",
            width=6000,
            height=4000,
            filesize=9_000_000,
            exact_match=None,
            hashed_assets=[asset()],
        )
        assert result.verdict is Verdict.HIGHER_RES_ARRIVED
        assert result.verdict.needs_review

    def test_threshold_is_respected(self):
        near = "de8f2130da8607f8"  # one bit from the master
        strict = classify(
            sha256="b" * 64,
            phash=near,
            width=100,
            height=100,
            filesize=100,
            exact_match=None,
            hashed_assets=[asset()],
            threshold=0,
        )
        assert strict.verdict is Verdict.NEW

        loose = classify(
            sha256="b" * 64,
            phash=near,
            width=100,
            height=100,
            filesize=100,
            exact_match=None,
            hashed_assets=[asset()],
            threshold=8,
        )
        assert loose.verdict is Verdict.LOW_RES_TWIN

    def test_picks_the_nearest_of_several(self):
        far = asset(id=1, phash="ffffffffffffffff")
        near = asset(id=2, phash="de8f2130da8607f9")
        result = classify(
            sha256="b" * 64,
            phash="de8f2130da8607f9",
            width=100,
            height=100,
            filesize=100,
            exact_match=None,
            hashed_assets=[far, near],
        )
        assert result.master.id == 2


class TestPerceptualHashOnRealImages:
    """The synthetic vectors above assume phash behaves a certain way. These
    confirm it actually does, on real JPEGs."""

    def test_downscaled_copy_hashes_the_same(self, tmp_path):
        original = make_image(tmp_path / "a.jpg", size=(800, 600), seed=7)
        smaller = downscale(original, tmp_path / "a_small.jpg")
        assert hamming_distance(compute_phash(original), compute_phash(smaller)) <= 8

    def test_different_images_hash_far_apart(self, tmp_path):
        first = make_image(tmp_path / "a.jpg", seed=1)
        second = make_image(tmp_path / "b.jpg", seed=99)
        assert hamming_distance(compute_phash(first), compute_phash(second)) > 8

    def test_identical_bytes_hash_identically(self, tmp_path):
        first = make_image(tmp_path / "a.jpg", seed=3)
        second = make_image(tmp_path / "b.jpg", seed=3)
        assert compute_phash(first) == compute_phash(second)

    def test_video_gets_no_hash(self, tmp_path):
        video = tmp_path / "clip.mov"
        video.write_bytes(b"not really a video")
        assert compute_phash(video) is None

    def test_unreadable_image_degrades_to_no_hash(self, tmp_path):
        """Not a failure worth stopping an import for."""
        broken = tmp_path / "broken.jpg"
        broken.write_bytes(b"this is not a JPEG")
        assert compute_phash(broken) is None
