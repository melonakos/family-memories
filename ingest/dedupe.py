"""Deciding whether a file is already in the archive.

Two questions, in order:

1. **Is it the same bytes?** SHA-256 against the index. Certain, cheap, final.
2. **Is it the same picture?** A perceptual hash within a small Hamming
   distance. Suggestive, never certain.

The second question is where archives quietly lose photographs. A perceptual
hash cannot tell a downscaled copy from a different frame of the same burst —
both look alike to it. So a phash match on its own decides nothing. It is only
acted on in one narrow, checkable case:

    same picture AND strictly smaller in both dimensions AND smaller on disk

That is a low-resolution twin, and linking it rather than importing it is the
right call. Everything else — a near match at similar size, a burst neighbour,
or a *better* copy arriving after a worse one — goes to a human. Being wrong
here means silently discarding a photo nobody knows is missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from index.models import Asset
from mediafiles import can_perceptual_hash

# Below this, two hashes are treated as the same picture for *proposal*
# purposes only. Config can tighten it; acting on a proposal still requires the
# resolution test below.
DEFAULT_THRESHOLD = 8


class Verdict(Enum):
    """What the deduplicator concluded about one file."""

    NEW = "new"
    EXACT_DUPLICATE = "exact_duplicate"
    LOW_RES_TWIN = "low_res_twin"
    AMBIGUOUS_MATCH = "ambiguous_match"
    HIGHER_RES_ARRIVED = "higher_res_arrived"

    @property
    def imports(self) -> bool:
        return self is Verdict.NEW

    @property
    def needs_review(self) -> bool:
        return self in (Verdict.AMBIGUOUS_MATCH, Verdict.HIGHER_RES_ARRIVED)


@dataclass(frozen=True)
class DedupeResult:
    verdict: Verdict
    master: Asset | None = None
    distance: int | None = None
    detail: str = ""


def compute_phash(path: Path) -> str | None:
    """Perceptual hash of an image, or None if it can't be hashed.

    Video gets no perceptual hash — a frame-based one is a different problem
    with different failure modes, and exact-hash dedupe already covers the case
    that matters (the same file arriving twice).
    """
    if not can_perceptual_hash(path):
        return None
    try:
        import imagehash
        from PIL import Image

        with Image.open(path) as image:
            return str(imagehash.phash(image))
    except Exception:
        # An unreadable or exotic image is not a failure worth stopping for.
        # It simply gets exact-hash dedupe like a video.
        return None


def hamming_distance(left: str, right: str) -> int:
    """Bit difference between two hex perceptual hashes.

    Returns a deliberately large number for mismatched or malformed hashes so
    they can never read as a match.
    """
    if not left or not right or len(left) != len(right):
        return 999
    try:
        return bin(int(left, 16) ^ int(right, 16)).count("1")
    except ValueError:
        return 999


def is_strictly_smaller(
    candidate_width: int | None,
    candidate_height: int | None,
    candidate_size: int,
    master: Asset,
) -> bool:
    """Whether the candidate is unambiguously a lesser copy of the master.

    Requires *both* dimensions to be smaller and the file to be smaller. Any
    missing dimension makes the answer no — an unknown is not evidence.
    """
    if not candidate_width or not candidate_height:
        return False
    if not master.width or not master.height:
        return False
    return (
        candidate_width < master.width
        and candidate_height < master.height
        and candidate_size < master.filesize
    )


def classify(
    *,
    sha256: str,
    phash: str | None,
    width: int | None,
    height: int | None,
    filesize: int,
    exact_match: Asset | None,
    hashed_assets: list[Asset],
    threshold: int = DEFAULT_THRESHOLD,
) -> DedupeResult:
    """Decide what to do with one incoming file.

    Pure: takes the index's current state as arguments and touches nothing.
    """
    if exact_match is not None:
        return DedupeResult(
            Verdict.EXACT_DUPLICATE,
            master=exact_match,
            distance=0,
            detail=f"identical to {exact_match.vault_path}",
        )

    if not phash:
        return DedupeResult(Verdict.NEW)

    nearest: Asset | None = None
    nearest_distance = 999
    for asset in hashed_assets:
        distance = hamming_distance(phash, asset.phash or "")
        if distance < nearest_distance:
            nearest, nearest_distance = asset, distance

    if nearest is None or nearest_distance > threshold:
        return DedupeResult(Verdict.NEW, distance=None if nearest is None else nearest_distance)

    if is_strictly_smaller(width, height, filesize, nearest):
        return DedupeResult(
            Verdict.LOW_RES_TWIN,
            master=nearest,
            distance=nearest_distance,
            detail=(
                f"{width}x{height} copy of {nearest.width}x{nearest.height} "
                f"{nearest.vault_path}"
            ),
        )

    # A better copy of something already archived. The vault never replaces a
    # file, so which one becomes canonical is a decision, not a computation.
    if (
        nearest.width
        and nearest.height
        and width
        and height
        and width > nearest.width
        and height > nearest.height
    ):
        return DedupeResult(
            Verdict.HIGHER_RES_ARRIVED,
            master=nearest,
            distance=nearest_distance,
            detail=(
                f"{width}x{height} is larger than archived "
                f"{nearest.width}x{nearest.height} at {nearest.vault_path}"
            ),
        )

    return DedupeResult(
        Verdict.AMBIGUOUS_MATCH,
        master=nearest,
        distance=nearest_distance,
        detail=(
            f"looks like {nearest.vault_path} (distance {nearest_distance}) but is not "
            f"clearly a lesser copy"
        ),
    )
