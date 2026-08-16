"""The copy contract.

One pure function, ``decide``, is the entire agreement with the contributor
expressed as code. Everything else in this package either feeds it items or
acts on its rulings. Keeping it pure and dependency-free is deliberate: this is
the part someone should be able to read, in full, before agreeing to run the
tool on their photo library.

Rule order matters, and it is not arbitrary:

1. Ownership and album exclusions come first. They are absolute — no later rule
   can pull an item back in.
2. Unknown dates stop the line. An item whose date can't be trusted cannot be
   judged against a date cutoff, so it goes to a human (ground rule 4).
3. Before the cutoff, everything is copied.
4. On or after the cutoff, the screenshot override runs *before* the subject-tag
   rule, so a tagged face in a screenshot does not rescue it.
5. On or after the cutoff, subject tags decide.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date

from settings import ContributeConfig, FamilyConfig, normalize_tag

from .models import Decision, Disposition, PhotoItem


def add_months(start: date, months: int) -> date:
    """Return ``start`` advanced by ``months``, clamping to the end of the month.

    Used only for the untagged-reporting window, where landing on the 28th
    versus the 30th changes nothing — but silently raising ValueError on
    January 31st would.
    """
    if months == 0:
        return start
    total = start.month - 1 + months
    year = start.year + total // 12
    month = total % 12 + 1
    return date(year, month, min(start.day, monthrange(year, month)[1]))


def _is_excluded_album(item: PhotoItem, config: ContributeConfig) -> bool:
    excluded = config.excluded_album_set
    return any(normalize_tag(album) in excluded for album in item.albums)


def decide(
    item: PhotoItem,
    config: ContributeConfig,
    family: FamilyConfig,
) -> Decision:
    """Apply the copy contract to a single item.

    Returns a ruling with its reason attached. Never mutates the item, never
    touches the filesystem, never reaches the network.
    """
    matched = family.subjects_in(item.persons)

    # 1. Absolute exclusions. Not the contributor's to give, or explicitly
    #    withheld by being hidden or already deleted.
    if config.exclude_not_owned and item.is_shared:
        return Decision(item, Disposition.EXCLUDE_NOT_OWNED, matched)

    if item.in_trash or item.is_hidden or _is_excluded_album(item, config):
        return Decision(item, Disposition.EXCLUDE_ALBUM, matched)

    # 2. No trustworthy date means no defensible ruling. Flag it; a person
    #    decides. Inferring a date from a filename here would be exactly the
    #    kind of silent guess ground rule 4 forbids.
    if item.date is None:
        return Decision(item, Disposition.REVIEW_UNKNOWN_DATE, matched)

    taken = item.date.date()

    # 3. Before the cutoff: everything, screenshots included.
    if taken < config.cutoff_date:
        return Decision(item, Disposition.INCLUDE_PRE_CUTOFF, matched)

    # 4. The screenshot override, deliberately ahead of the tag rule.
    if config.exclude_screenshots_after_cutoff and item.is_screenshot:
        return Decision(item, Disposition.EXCLUDE_SCREENSHOT, matched)

    # 5. On or after the cutoff, a subject must be in the shot.
    if matched:
        return Decision(item, Disposition.INCLUDE_TAGGED, matched)

    window_end = add_months(config.cutoff_date, config.untagged_report_months)
    return Decision(
        item,
        Disposition.EXCLUDE_UNTAGGED,
        matched,
        in_untagged_window=taken < window_end,
    )


def decide_all(
    items: list[PhotoItem],
    config: ContributeConfig,
    family: FamilyConfig,
) -> list[Decision]:
    """Apply the contract across a library."""
    return [decide(item, config, family) for item in items]
