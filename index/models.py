"""Typed rows for the index.

Plain dataclasses, converted at the database boundary, so nothing above this
layer handles raw sqlite3 rows or has to remember column order.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# taken_at_source values. An asset always records how its date was determined,
# so a wrong date can be traced to the thing that produced it.
DATE_FROM_SIDECAR = "sidecar"
DATE_FROM_EXIF = "exif"
DATE_UNKNOWN = "unknown"

# linked_files.reason values.
EXACT_DUPLICATE = "exact_duplicate"
LOW_RES_TWIN = "low_res_twin"

# review_queue.kind values.
REVIEW_UNKNOWN_DATE = "unknown_date"
REVIEW_AMBIGUOUS_MATCH = "ambiguous_match"
REVIEW_HIGHER_RES_ARRIVED = "higher_res_arrived"

REVIEW_OPEN = "open"
REVIEW_RESOLVED = "resolved"
REVIEW_DISMISSED = "dismissed"


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


@dataclass(frozen=True)
class Source:
    id: int
    label: str
    kind: str
    ingested_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Source:
        return cls(
            id=row["id"], label=row["label"], kind=row["kind"], ingested_at=row["ingested_at"]
        )


@dataclass(frozen=True)
class Asset:
    id: int
    sha256: str
    vault_path: str
    original_filename: str
    media_type: str
    filesize: int
    taken_at_source: str
    imported_at: str
    phash: str | None = None
    taken_at: datetime | None = None
    width: int | None = None
    height: int | None = None
    source_id: int | None = None

    @property
    def has_date(self) -> bool:
        return self.taken_at is not None

    @property
    def pixels(self) -> int | None:
        if self.width and self.height:
            return self.width * self.height
        return None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Asset:
        return cls(
            id=row["id"],
            sha256=row["sha256"],
            vault_path=row["vault_path"],
            original_filename=row["original_filename"],
            media_type=row["media_type"],
            filesize=row["filesize"],
            taken_at_source=row["taken_at_source"],
            imported_at=row["imported_at"],
            phash=row["phash"],
            taken_at=parse_datetime(row["taken_at"]),
            width=row["width"],
            height=row["height"],
            source_id=row["source_id"],
        )


@dataclass(frozen=True)
class LinkedFile:
    """A file that was seen and deliberately not imported."""

    id: int
    sha256: str
    original_path: str
    master_asset_id: int
    reason: str
    filesize: int
    seen_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> LinkedFile:
        return cls(
            id=row["id"],
            sha256=row["sha256"],
            original_path=row["original_path"],
            master_asset_id=row["master_asset_id"],
            reason=row["reason"],
            filesize=row["filesize"],
            seen_at=row["seen_at"],
        )


@dataclass(frozen=True)
class ReviewItem:
    id: int
    kind: str
    status: str
    created_at: str
    detail: dict[str, Any] = field(default_factory=dict)
    asset_id: int | None = None
    original_path: str | None = None
    resolution: str | None = None
    resolved_at: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> ReviewItem:
        try:
            detail = json.loads(row["detail"] or "{}")
        except json.JSONDecodeError:
            detail = {}
        return cls(
            id=row["id"],
            kind=row["kind"],
            status=row["status"],
            created_at=row["created_at"],
            detail=detail if isinstance(detail, dict) else {},
            asset_id=row["asset_id"],
            original_path=row["original_path"],
            resolution=row["resolution"],
            resolved_at=row["resolved_at"],
        )


@dataclass(frozen=True)
class IndexStats:
    assets: int = 0
    total_bytes: int = 0
    photos: int = 0
    videos: int = 0
    undated: int = 0
    linked_files: int = 0
    open_reviews: int = 0
    sources: int = 0
    earliest: datetime | None = None
    latest: datetime | None = None
    reviews_by_kind: dict[str, int] = field(default_factory=dict)
    linked_by_reason: dict[str, int] = field(default_factory=dict)
