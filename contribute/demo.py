"""A synthetic library that exercises every branch of the copy contract.

Exists so ``contribute inventory --demo`` runs on any machine, with no Photos
library and no Mac. Two uses:

1. Showing a contributor how the rules behave — on invented photos — before
   anyone points the tool at their real library. Far better than describing the
   contract in prose and hoping it was understood.
2. Sanity-checking a ``config.toml`` (cutoff date, subject tags) without a
   multi-minute scan.

Deterministic: no randomness, no clock reads, so two runs always agree.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from settings import FamilyConfig

from .library import FakeLibrary
from .models import PhotoItem

MB = 1024 * 1024


def _item(uuid: str, filename: str, when: datetime | None, **kwargs) -> PhotoItem:
    defaults = {"filesize": 3 * MB, "is_shared": False}
    return PhotoItem(uuid=uuid, original_filename=filename, date=when, **{**defaults, **kwargs})


def build_demo_library(family: FamilyConfig, cutoff: datetime) -> FakeLibrary:
    """A small library covering every disposition the contract can return."""
    subjects = [s.name for s in family.subjects] or ["Subject One"]
    before = cutoff - timedelta(days=400)
    well_before = cutoff - timedelta(days=2200)
    after = cutoff + timedelta(days=200)
    well_after = cutoff + timedelta(days=1500)

    items: list[PhotoItem] = [
        # Before the cutoff: everything qualifies, tagged or not.
        _item("pre-001", "IMG_0001.JPG", well_before, persons=(subjects[0],)),
        _item("pre-002", "IMG_0002.JPG", well_before),
        _item("pre-003", "IMG_0003.HEIC", before, persons=tuple(subjects[:2])),
        _item("pre-004", "IMG_0004.MOV", before, is_movie=True, is_photo=False, filesize=180 * MB),
        # A pre-cutoff screenshot: copied, because the override is post-cutoff only.
        _item("pre-005", "Screenshot 2014-06-01.png", before, is_screenshot=True, filesize=400_000),
        # Cloud-only original — the download-wait number in the report.
        _item("pre-006", "IMG_0006.JPG", before, is_missing=True, filesize=5 * MB),
        # Live photo pair.
        _item("pre-007", "IMG_0007.HEIC", before, is_live_photo=True, persons=(subjects[0],)),
        # After the cutoff, tagged with a subject: qualifies.
        _item("post-001", "IMG_1001.HEIC", after, persons=(subjects[0],)),
        _item("post-002", "IMG_1002.HEIC", well_after, persons=tuple(subjects[:2])),
        _item("post-003", "IMG_1003.MOV", well_after, is_movie=True, is_photo=False,
              persons=(subjects[-1],), filesize=250 * MB),
        # Tag spelled with stray whitespace and different case — still matches.
        _item("post-004", "IMG_1004.HEIC", after, persons=(f"  {subjects[0].upper()} ",)),
        # After the cutoff, screenshot WITH a subject tagged: still excluded.
        # The single most important case in this file.
        _item("post-005", "Screenshot 2016-03-02.png", after, is_screenshot=True,
              persons=(subjects[0],), filesize=500_000),
        # After the cutoff, untagged: excluded. Dated inside the reporting window.
        _item("post-006", "IMG_1006.HEIC", after),
        _item("post-007", "IMG_1007.HEIC", after),
        # Untagged and well past the window: excluded, not reported as a gap.
        _item("post-008", "IMG_1008.HEIC", well_after),
        # Never copied, whatever the date or tags.
        _item("skip-001", "IMG_2001.HEIC", before, is_hidden=True, persons=(subjects[0],)),
        _item("skip-002", "IMG_2002.HEIC", before, in_trash=True),
        _item("skip-003", "IMG_2003.HEIC", before, is_shared=True, persons=(subjects[0],)),
        _item("skip-004", "IMG_2004.HEIC", before, albums=("Recently Deleted",)),
        # No usable date: goes to a human, never guessed either way.
        _item("unknown-001", "scan_014.jpg", None, filesize=8 * MB),
        _item("unknown-002", "scan_015.jpg", None, filesize=8 * MB),
    ]
    return FakeLibrary(items, description="demo library (synthetic — no real photos)")
