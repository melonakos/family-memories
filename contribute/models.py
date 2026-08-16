"""Plain data types for the contribution kit.

Nothing here imports osxphotos. ``PhotoItem`` is a normalized snapshot of one
library item, so the copy contract can be reasoned about and tested on any
machine — the rules are the part that has to be right, and they shouldn't
require a Mac and a real photo library to exercise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Disposition(Enum):
    """What the copy contract decided about one item, and why.

    The value doubles as the label used in reports.
    """

    INCLUDE_PRE_CUTOFF = "included: before cutoff"
    INCLUDE_TAGGED = "included: tagged with a subject"
    EXCLUDE_NOT_OWNED = "excluded: shared into the library by someone else"
    EXCLUDE_ALBUM = "excluded: hidden or recently deleted"
    EXCLUDE_SCREENSHOT = "excluded: screenshot on or after cutoff"
    EXCLUDE_UNTAGGED = "excluded: on or after cutoff, no subject tagged"
    REVIEW_UNKNOWN_DATE = "needs review: no reliable date"

    @property
    def is_include(self) -> bool:
        return self in (Disposition.INCLUDE_PRE_CUTOFF, Disposition.INCLUDE_TAGGED)

    @property
    def needs_review(self) -> bool:
        return self is Disposition.REVIEW_UNKNOWN_DATE


@dataclass(frozen=True)
class PhotoItem:
    """One item in a contributor's photo library, normalized.

    Every field is something the copy contract or the inventory report needs.
    Fields default to the safe reading, so an adapter that can't determine a
    value doesn't accidentally widen what gets copied.
    """

    uuid: str
    original_filename: str
    date: datetime | None = None
    persons: tuple[str, ...] = ()
    albums: tuple[str, ...] = ()
    is_screenshot: bool = False
    is_hidden: bool = False
    in_trash: bool = False
    is_shared: bool = False
    is_photo: bool = True
    is_movie: bool = False
    is_live_photo: bool = False
    is_missing: bool = False
    """True when the original lives only in the cloud and must download before export.

    Drives the "this will take a while" number in the inventory, which is the
    single most useful figure in the whole report for anyone using Optimize
    Mac Storage.
    """
    filesize: int = 0
    path: str | None = None

    @property
    def has_date(self) -> bool:
        return self.date is not None


@dataclass(frozen=True)
class Decision:
    """The contract's ruling on one item."""

    item: PhotoItem
    disposition: Disposition
    matched_subjects: tuple[str, ...] = ()
    in_untagged_window: bool = False
    """Excluded as untagged, but dated soon after the cutoff.

    Reported separately because a cluster here usually means face tagging
    lapsed for a while rather than that the photos genuinely don't matter —
    it's information for renegotiating the contract, not for overriding it.
    """

    @property
    def is_include(self) -> bool:
        return self.disposition.is_include

    @property
    def reason(self) -> str:
        return self.disposition.value
