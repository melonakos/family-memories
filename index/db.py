"""The index database.

Plain sqlite3 with explicit SQL — no ORM. The schema is small, the queries are
few, and an archive meant to outlive its tooling shouldn't depend on a mapping
layer to be readable.

Writes go through a transaction per ingested file, so an interrupted run leaves
the index consistent with the vault rather than half-describing it.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import (
    REVIEW_OPEN,
    REVIEW_RESOLVED,
    Asset,
    IndexStats,
    LinkedFile,
    ReviewItem,
    Source,
    parse_datetime,
)

SCHEMA_VERSION = 1
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class IndexDatabaseError(Exception):
    """Raised when the index cannot be opened, created, or migrated."""


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Index:
    """A connection to the index database.

    Use as a context manager, or call ``close()``. ``open_index`` is the normal
    entry point.
    """

    def __init__(self, connection: sqlite3.Connection, path: Path) -> None:
        self._db = connection
        self.path = path

    # ---------------------------------------------------------------- lifecycle

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Index:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """One unit of work. Rolls back entirely on any exception."""
        try:
            with self._db:
                yield self._db
        except sqlite3.Error as exc:
            raise IndexDatabaseError(f"Index write failed: {exc}") from exc

    # ----------------------------------------------------------------- schema

    @property
    def version(self) -> int:
        row = self._db.execute(
            "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
        ).fetchone()
        return int(row["version"]) if row else 0

    def initialize(self) -> int:
        """Create or migrate the schema. Returns the resulting version.

        Safe to call on an existing index; it applies only what's missing.
        """
        if not self._has_table("schema_version"):
            self._apply_base_schema()
            return SCHEMA_VERSION

        current = self.version
        if current > SCHEMA_VERSION:
            raise IndexDatabaseError(
                f"{self.path} was written by a newer version of this tool "
                f"(schema {current}, this build understands {SCHEMA_VERSION}). "
                f"Upgrade rather than risk writing a format it doesn't share."
            )
        # Future migrations are applied here, in order, inside one transaction.
        return current

    def _has_table(self, name: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        return row is not None

    def _apply_base_schema(self) -> None:
        try:
            sql = SCHEMA_PATH.read_text(encoding="utf-8")
        except OSError as exc:
            raise IndexDatabaseError(f"Could not read the schema at {SCHEMA_PATH}: {exc}") from exc
        with self.transaction() as db:
            db.executescript(sql)
            db.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, utcnow()),
            )

    # ----------------------------------------------------------------- sources

    def add_source(self, label: str, kind: str = "other") -> int:
        """Record a batch's provenance, or return the existing id for the label."""
        existing = self._db.execute("SELECT id FROM sources WHERE label = ?", (label,)).fetchone()
        if existing:
            return int(existing["id"])
        with self.transaction() as db:
            cursor = db.execute(
                "INSERT INTO sources (label, kind, ingested_at) VALUES (?, ?, ?)",
                (label, kind, utcnow()),
            )
        return int(cursor.lastrowid)

    def sources(self) -> list[Source]:
        rows = self._db.execute("SELECT * FROM sources ORDER BY ingested_at").fetchall()
        return [Source.from_row(r) for r in rows]

    # ------------------------------------------------------------------ assets

    def asset_by_sha256(self, sha256: str) -> Asset | None:
        row = self._db.execute("SELECT * FROM assets WHERE sha256 = ?", (sha256,)).fetchone()
        return Asset.from_row(row) if row else None

    def asset_by_vault_path(self, vault_path: str) -> Asset | None:
        row = self._db.execute(
            "SELECT * FROM assets WHERE vault_path = ?", (vault_path,)
        ).fetchone()
        return Asset.from_row(row) if row else None

    def assets(self) -> Iterator[Asset]:
        for row in self._db.execute("SELECT * FROM assets ORDER BY id"):
            yield Asset.from_row(row)

    def hashed_assets(self) -> list[Asset]:
        """Every asset carrying a perceptual hash.

        Loaded once per ingest run and compared in memory: Hamming distance
        isn't expressible in SQL, and re-querying per candidate file would be
        quadratic.
        """
        rows = self._db.execute("SELECT * FROM assets WHERE phash IS NOT NULL").fetchall()
        return [Asset.from_row(r) for r in rows]

    def add_asset(
        self,
        *,
        sha256: str,
        vault_path: str,
        original_filename: str,
        media_type: str,
        filesize: int,
        taken_at: datetime | None,
        taken_at_source: str,
        phash: str | None = None,
        width: int | None = None,
        height: int | None = None,
        source_id: int | None = None,
    ) -> int:
        with self.transaction() as db:
            cursor = db.execute(
                """
                INSERT INTO assets (
                    sha256, phash, vault_path, original_filename, taken_at,
                    taken_at_source, media_type, filesize, width, height,
                    imported_at, source_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sha256,
                    phash,
                    vault_path,
                    original_filename,
                    taken_at.isoformat() if taken_at else None,
                    taken_at_source,
                    media_type,
                    filesize,
                    width,
                    height,
                    utcnow(),
                    source_id,
                ),
            )
        return int(cursor.lastrowid)

    # ------------------------------------------------------------ linked files

    def add_linked_file(
        self,
        *,
        sha256: str,
        original_path: str,
        master_asset_id: int,
        reason: str,
        filesize: int,
    ) -> int:
        with self.transaction() as db:
            cursor = db.execute(
                """
                INSERT INTO linked_files
                    (sha256, original_path, master_asset_id, reason, filesize, seen_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (sha256, original_path, master_asset_id, reason, filesize, utcnow()),
            )
        return int(cursor.lastrowid)

    def linked_files(self) -> list[LinkedFile]:
        rows = self._db.execute("SELECT * FROM linked_files ORDER BY id").fetchall()
        return [LinkedFile.from_row(r) for r in rows]

    def has_linked_path(self, original_path: str) -> bool:
        """Whether this exact path was already recorded as linked.

        Makes re-running ingest over the same inbox idempotent rather than
        accumulating a duplicate link row per run.
        """
        row = self._db.execute(
            "SELECT 1 FROM linked_files WHERE original_path = ? LIMIT 1", (original_path,)
        ).fetchone()
        return row is not None

    # ------------------------------------------------------------ review queue

    def queue_review(
        self,
        kind: str,
        *,
        asset_id: int | None = None,
        original_path: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> int:
        with self.transaction() as db:
            cursor = db.execute(
                """
                INSERT INTO review_queue
                    (asset_id, original_path, kind, detail, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    original_path,
                    kind,
                    json.dumps(detail or {}, ensure_ascii=False),
                    REVIEW_OPEN,
                    utcnow(),
                ),
            )
        return int(cursor.lastrowid)

    def has_open_review_for_path(self, original_path: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM review_queue WHERE original_path = ? AND status = ? LIMIT 1",
            (original_path, REVIEW_OPEN),
        ).fetchone()
        return row is not None

    def reviews(
        self, status: str | None = REVIEW_OPEN, kind: str | None = None
    ) -> list[ReviewItem]:
        sql = "SELECT * FROM review_queue"
        clauses, params = [], []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id"
        return [ReviewItem.from_row(r) for r in self._db.execute(sql, params).fetchall()]

    def resolve_review(self, review_id: int, resolution: str) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE review_queue SET status = ?, resolution = ?, resolved_at = ? WHERE id = ?",
                (REVIEW_RESOLVED, resolution, utcnow(), review_id),
            )

    # ----------------------------------------------------------------- persons

    def add_person(self, name: str) -> int:
        existing = self._db.execute("SELECT id FROM persons WHERE name = ?", (name,)).fetchone()
        if existing:
            return int(existing["id"])
        with self.transaction() as db:
            cursor = db.execute("INSERT INTO persons (name) VALUES (?)", (name,))
        return int(cursor.lastrowid)

    def tag_asset(self, asset_id: int, person_name: str, source: str = "manifest") -> None:
        person_id = self.add_person(person_name)
        with self.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO asset_persons (asset_id, person_id, source) "
                "VALUES (?, ?, ?)",
                (asset_id, person_id, source),
            )

    def persons_for_asset(self, asset_id: int) -> list[str]:
        rows = self._db.execute(
            """
            SELECT p.name FROM persons p
            JOIN asset_persons ap ON ap.person_id = p.id
            WHERE ap.asset_id = ? ORDER BY p.name
            """,
            (asset_id,),
        ).fetchall()
        return [r["name"] for r in rows]

    # ------------------------------------------------------------------- stats

    def stats(self) -> IndexStats:
        def scalar(sql: str, *params: Any) -> Any:
            row = self._db.execute(sql, params).fetchone()
            return row[0] if row else None

        def grouped(sql: str) -> dict[str, int]:
            return {r[0]: r[1] for r in self._db.execute(sql).fetchall()}

        return IndexStats(
            assets=scalar("SELECT COUNT(*) FROM assets") or 0,
            total_bytes=scalar("SELECT COALESCE(SUM(filesize), 0) FROM assets") or 0,
            photos=scalar("SELECT COUNT(*) FROM assets WHERE media_type = 'photo'") or 0,
            videos=scalar("SELECT COUNT(*) FROM assets WHERE media_type = 'video'") or 0,
            undated=scalar("SELECT COUNT(*) FROM assets WHERE taken_at IS NULL") or 0,
            linked_files=scalar("SELECT COUNT(*) FROM linked_files") or 0,
            open_reviews=scalar(
                "SELECT COUNT(*) FROM review_queue WHERE status = ?", REVIEW_OPEN
            )
            or 0,
            sources=scalar("SELECT COUNT(*) FROM sources") or 0,
            earliest=parse_datetime(scalar("SELECT MIN(taken_at) FROM assets")),
            latest=parse_datetime(scalar("SELECT MAX(taken_at) FROM assets")),
            reviews_by_kind=grouped(
                "SELECT kind, COUNT(*) FROM review_queue WHERE status = 'open' GROUP BY kind"
            ),
            linked_by_reason=grouped(
                "SELECT reason, COUNT(*) FROM linked_files GROUP BY reason"
            ),
        )


def open_index(path: Path, create: bool = True) -> Index:
    """Open the index, creating and migrating it unless ``create`` is False."""
    resolved = Path(path).expanduser()
    if not create and not resolved.exists():
        raise IndexDatabaseError(
            f"No index at {resolved}. Run 'family-memories index init' first."
        )
    resolved.parent.mkdir(parents=True, exist_ok=True)

    try:
        connection = sqlite3.connect(resolved)
    except sqlite3.Error as exc:
        raise IndexDatabaseError(f"Could not open {resolved}: {exc}") from exc

    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    # WAL keeps a long ingest from blocking a concurrent read, and survives an
    # abrupt stop without corrupting the database.
    connection.execute("PRAGMA journal_mode = WAL")

    index = Index(connection, resolved)
    if create:
        index.initialize()
    return index
