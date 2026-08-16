"""Reading a contributor's Apple Photos library.

The only module in this package that imports osxphotos, and it imports it
lazily so the rest of the kit — the contract, the inventory maths, the manifest
— stays importable and testable on any platform.

**This module is read-only toward the contributor's data.** It opens the Photos
database, reads properties, and returns plain ``PhotoItem`` snapshots. There is
no code path here that writes, deletes, or modifies anything, and none should
ever be added. Export is a separate step that writes only to the destination
drive.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from .models import PhotoItem

# Filename shapes Apple and iOS have used for screen captures over the years.
# Used only to *widen* screenshot detection, never to narrow it: a false
# positive here excludes one more post-cutoff screenshot, which is the
# conservative direction. See is_screenshot() below.
_SCREENSHOT_PREFIXES = ("screenshot", "screen shot", "simulator screen shot")

# Every osxphotos PhotoInfo property the copy contract depends on.
#
# photo_to_item() reads these defensively, so a renamed property degrades
# quietly rather than crashing — good for not losing an export halfway, bad
# for noticing. This tuple closes that gap: `doctor` and the macOS integration
# tests assert each name still exists on a real PhotoInfo, turning a silent
# behaviour change into a loud one.
PHOTOINFO_ATTRIBUTES = (
    "uuid",
    "original_filename",
    "date",
    "persons",
    "albums",
    "screenshot",
    "hidden",
    "intrash",
    "shared",
    "isphoto",
    "ismovie",
    "live_photo",
    "ismissing",
    "original_filesize",
    "path",
)


def missing_attributes(photo: object) -> list[str]:
    """Which depended-on properties this PhotoInfo does not have.

    A non-empty result means osxphotos changed shape and the contract may be
    reading defaults instead of real values.
    """
    return [name for name in PHOTOINFO_ATTRIBUTES if not hasattr(photo, name)]


class LibraryError(Exception):
    """Raised when a photo library can't be opened or read."""


class PhotoLibrary(Protocol):
    """What the rest of the kit needs from a photo library."""

    def items(self) -> Iterator[PhotoItem]: ...

    @property
    def description(self) -> str: ...


def _looks_like_screenshot(filename: str) -> bool:
    name = filename.strip().casefold()
    return any(name.startswith(prefix) for prefix in _SCREENSHOT_PREFIXES)


def _as_datetime(value: Any) -> datetime | None:
    """Normalize a date, treating Apple's placeholder dates as unknown.

    Photos stores an item with no usable date as 1970-01-01 in some import
    paths. Passing that through as a real date would sail it under any modern
    cutoff and copy it silently, which is precisely the guess ground rule 4
    forbids — so it becomes None and lands in the review queue instead.
    """
    if not isinstance(value, datetime):
        return None
    if value.year <= 1970:
        return None
    return value


def _str_tuple(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(str(v) for v in value if v is not None and str(v).strip())


def photo_to_item(photo: Any) -> PhotoItem:
    """Convert an osxphotos ``PhotoInfo`` into a plain ``PhotoItem``.

    Attribute access is defensive on purpose. osxphotos tracks a moving target
    — Apple reshapes the Photos schema most years — and a renamed property
    should not silently change what gets copied. Booleans that widen the copy
    default to the *safe* value when absent, so a missing attribute never
    quietly includes more than the contract allows. Run ``contribute doctor``
    on the actual Mac to confirm every property resolved.
    """
    filename = str(getattr(photo, "original_filename", "") or getattr(photo, "filename", ""))

    return PhotoItem(
        uuid=str(photo.uuid),
        original_filename=filename,
        date=_as_datetime(getattr(photo, "date", None)),
        persons=_str_tuple(getattr(photo, "persons", ())),
        albums=_str_tuple(getattr(photo, "albums", ())),
        # Either signal is enough to call it a screenshot; both directions of
        # error here are "exclude one more post-cutoff screenshot".
        is_screenshot=bool(getattr(photo, "screenshot", False)) or _looks_like_screenshot(filename),
        is_hidden=bool(getattr(photo, "hidden", False)),
        in_trash=bool(getattr(photo, "intrash", False)),
        # Default True: if osxphotos can't tell us whether an item was shared
        # into the library, treat it as not the contributor's to give.
        is_shared=bool(getattr(photo, "shared", True)),
        is_photo=bool(getattr(photo, "isphoto", True)),
        is_movie=bool(getattr(photo, "ismovie", False)),
        is_live_photo=bool(getattr(photo, "live_photo", False)),
        is_missing=bool(getattr(photo, "ismissing", False)),
        filesize=int(getattr(photo, "original_filesize", 0) or 0),
        path=str(photo.path) if getattr(photo, "path", None) else None,
    )


class OsxPhotosLibrary:
    """A real Apple Photos library, read through osxphotos."""

    def __init__(self, db: Any, source: str) -> None:
        self._db = db
        self._source = source

    @property
    def description(self) -> str:
        return self._source

    def items(self) -> Iterator[PhotoItem]:
        """Every item in the library, including trashed and hidden ones.

        Deliberately unfiltered. The contract — not this adapter, and not
        osxphotos' own query flags — decides what is excluded, so that every
        exclusion has a recorded reason and shows up in the inventory. A
        library filter here would make items vanish with no accounting.
        """
        for photo in self._db.photos(movies=True, intrash=True):
            yield photo_to_item(photo)


def open_library(path: Path | None = None) -> OsxPhotosLibrary:
    """Open the contributor's Photos library.

    ``path`` points at a ``.photoslibrary`` bundle; omit it for the system
    library. Raises LibraryError with an actionable message rather than an
    ImportError traceback when run off a Mac.
    """
    if sys.platform != "darwin":
        raise LibraryError(
            "Reading a Photos library requires macOS. The inventory and export "
            "steps run on the contributor's Mac; the contract logic and manifest "
            "tools run anywhere."
        )
    try:
        import osxphotos
    except ImportError as exc:
        raise LibraryError(
            "osxphotos is not installed. Install it with:\n"
            '    pip install -e ".[macos]"'
        ) from exc

    try:
        db = osxphotos.PhotosDB(str(path)) if path else osxphotos.PhotosDB()
    except Exception as exc:  # osxphotos raises a variety of types here
        target = str(path) if path else "the system photo library"
        raise LibraryError(
            f"Could not open {target}: {exc}\n"
            "If this is the first run, macOS may need to grant Full Disk Access "
            "to your terminal (System Settings > Privacy & Security)."
        ) from exc

    return OsxPhotosLibrary(db, str(path) if path else "system photo library")


class FakeLibrary:
    """An in-memory library for tests and for demonstrating the contract.

    Lives in the shipped package rather than the test directory so that
    ``contribute inventory --demo`` can show a contributor exactly how the
    rules behave on synthetic data, on any machine, before anyone points the
    tool at their real photos.
    """

    def __init__(self, items: list[PhotoItem], description: str = "fake library") -> None:
        self._items = list(items)
        self._description = description

    @property
    def description(self) -> str:
        return self._description

    def items(self) -> Iterator[PhotoItem]:
        yield from self._items
