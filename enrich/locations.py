"""Filling in locations for photos that have none.

Phones record GPS; older cameras and scans do not. But photographs come in
sequences, and a picture taken eleven minutes after one with coordinates was
almost certainly taken in the same place.

That "almost certainly" is the whole design problem. This module infers, and an
inference must never be mistaken for an observation, so:

* an inferred location is stored with ``gps_source = 'inferred'``, alongside the
  camera-recorded ones but permanently distinguishable from them;
* a camera-recorded location is never overwritten;
* inference only runs within a bounded time window, and confidence falls off
  with the gap, so a photo from the far end of a long day is left alone rather
  than pinned to breakfast;
* the day boundary is respected. Two photos six hours apart on the same
  afternoon are usually in the same place; the same six hours spanning midnight
  usually are not.

Anything outside those bounds stays unlocated. An empty field is honest; a wrong
coordinate is a fact the archive will repeat forever.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from index.db import Index
from index.models import GPS_INFERRED, Asset

# How far from a located photo an inference may reach. Beyond this the guess
# stops being better than nothing.
DEFAULT_WINDOW_HOURS = 6.0

# Below this, the inference is recorded but flagged as weak.
HIGH_CONFIDENCE_MINUTES = 60


@dataclass
class LocationResult:
    considered: int = 0
    inferred: int = 0
    skipped_no_neighbour: int = 0
    skipped_different_day: int = 0
    by_confidence: dict[str, int] = field(default_factory=dict)

    @property
    def coverage(self) -> float:
        return self.inferred / self.considered if self.considered else 0.0


def confidence_for(gap: timedelta) -> float:
    """Confidence from the time gap: 1.0 at zero, decaying to 0 at the window."""
    minutes = abs(gap.total_seconds()) / 60
    window = DEFAULT_WINDOW_HOURS * 60
    if minutes >= window:
        return 0.0
    return round(1.0 - (minutes / window), 3)


def nearest_located(
    when: datetime,
    anchors: list[Asset],
    anchor_times: list[datetime],
    window: timedelta,
) -> Asset | None:
    """The located photo closest in time, within ``window`` and the same day.

    ``anchors`` must be sorted by ``taken_at``; a binary search keeps this
    linear overall rather than quadratic on a large archive.
    """
    if not anchors:
        return None

    position = bisect.bisect_left(anchor_times, when)
    best: Asset | None = None
    best_gap: timedelta | None = None

    for candidate_index in (position - 1, position):
        if not 0 <= candidate_index < len(anchors):
            continue
        candidate = anchors[candidate_index]
        if candidate.taken_at is None:
            continue
        gap = abs(candidate.taken_at - when)
        if gap > window:
            continue
        # Same-place-same-day. Crossing midnight usually means travel or sleep.
        if candidate.taken_at.date() != when.date():
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = candidate, gap

    return best


def infer_locations(
    index: Index,
    window_hours: float = DEFAULT_WINDOW_HOURS,
    dry_run: bool = False,
) -> LocationResult:
    """Give unlocated photos the coordinates of their nearest located neighbour.

    Only camera-recorded locations are used as anchors. Inferring from an
    inference would let one guess propagate across a whole archive, each step
    looking as solid as the last.
    """
    result = LocationResult()
    window = timedelta(hours=window_hours)

    anchors = [a for a in index.assets_with_location(source="exif") if a.taken_at]
    anchors.sort(key=lambda a: a.taken_at)  # type: ignore[arg-type,return-value]
    anchor_times = [a.taken_at for a in anchors]

    for asset in index.assets_missing_location():
        if asset.taken_at is None:
            continue
        result.considered += 1

        neighbour = nearest_located(asset.taken_at, anchors, anchor_times, window)
        if neighbour is None or neighbour.taken_at is None:
            result.skipped_no_neighbour += 1
            continue

        gap = abs(neighbour.taken_at - asset.taken_at)
        confidence = confidence_for(gap)
        if confidence <= 0:
            result.skipped_no_neighbour += 1
            continue

        band = "high" if gap <= timedelta(minutes=HIGH_CONFIDENCE_MINUTES) else "low"
        result.by_confidence[band] = result.by_confidence.get(band, 0) + 1
        result.inferred += 1

        if not dry_run:
            index.set_location(
                asset.id,
                neighbour.gps_latitude,  # type: ignore[arg-type]
                neighbour.gps_longitude,  # type: ignore[arg-type]
                source=GPS_INFERRED,
            )

    return result
