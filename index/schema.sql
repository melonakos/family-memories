-- The index: everything known about every file in the vault.
--
-- Design notes that matter when adding a column:
--
-- * Derived vs decided. Most of this is re-derivable from the vault plus
--   sidecars: checksums, hashes, dimensions, dates. What is NOT re-derivable is
--   human judgement — review_queue resolutions above all. Keep that distinction
--   clear, because it determines what actually has to be backed up.
--
-- * Three-way accounting. Every file the pipeline sees becomes exactly one of:
--   an `assets` row (copied into the vault), a `linked_files` row (deliberately
--   not copied, with a reason and a master), or a `review_queue` row (a human
--   decides). Nothing is silently dropped.
--
-- Dates are ISO 8601 text. SQLite has no date type, and text sorts correctly.

PRAGMA foreign_keys = ON;

CREATE TABLE schema_version (
    version    INTEGER NOT NULL,
    applied_at TEXT    NOT NULL
);

-- Where a batch of files came from. Provenance survives even after the
-- original drive is wiped and reused.
CREATE TABLE sources (
    id          INTEGER PRIMARY KEY,
    label       TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL,              -- contribution | camera | backup | other
    ingested_at TEXT NOT NULL
);

-- One row per original file in the vault.
CREATE TABLE assets (
    id                INTEGER PRIMARY KEY,
    sha256            TEXT    NOT NULL UNIQUE,
    phash             TEXT,                 -- NULL for video and undecodable formats
    vault_path        TEXT    NOT NULL UNIQUE,
    original_filename TEXT    NOT NULL,
    taken_at          TEXT,                 -- NULL means unknown, never guessed
    taken_at_source   TEXT    NOT NULL,     -- sidecar | exif | unknown
    media_type        TEXT    NOT NULL,     -- photo | video
    filesize          INTEGER NOT NULL,
    width             INTEGER,
    height            INTEGER,
    imported_at       TEXT    NOT NULL,
    source_id         INTEGER REFERENCES sources(id)
);

CREATE INDEX idx_assets_phash    ON assets(phash) WHERE phash IS NOT NULL;
CREATE INDEX idx_assets_taken_at ON assets(taken_at);
CREATE INDEX idx_assets_source   ON assets(source_id);

-- Files seen and deliberately not imported. Keeping these is what makes
-- "why isn't this photo in the archive?" an answerable question.
CREATE TABLE linked_files (
    id              INTEGER PRIMARY KEY,
    sha256          TEXT    NOT NULL,
    original_path   TEXT    NOT NULL,
    master_asset_id INTEGER NOT NULL REFERENCES assets(id),
    reason          TEXT    NOT NULL,       -- exact_duplicate | low_res_twin
    filesize        INTEGER NOT NULL,
    seen_at         TEXT    NOT NULL
);

CREATE INDEX idx_linked_sha    ON linked_files(sha256);
CREATE INDEX idx_linked_master ON linked_files(master_asset_id);

-- Anything a human has to decide. The one table whose contents cannot be
-- rebuilt by re-scanning the vault.
CREATE TABLE review_queue (
    id            INTEGER PRIMARY KEY,
    asset_id      INTEGER REFERENCES assets(id),
    original_path TEXT,
    kind          TEXT NOT NULL,            -- unknown_date | ambiguous_match | ...
    detail        TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'open',   -- open | resolved | dismissed
    resolution    TEXT,
    created_at    TEXT NOT NULL,
    resolved_at   TEXT
);

CREATE INDEX idx_review_status ON review_queue(status, kind);

CREATE TABLE persons (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE asset_persons (
    asset_id  INTEGER NOT NULL REFERENCES assets(id),
    person_id INTEGER NOT NULL REFERENCES persons(id),
    source    TEXT    NOT NULL,             -- manifest | faces
    PRIMARY KEY (asset_id, person_id)
);

-- Viewing copies, restored scans, and anything else generated from a master.
-- The master itself is never modified.
CREATE TABLE derivatives (
    id              INTEGER PRIMARY KEY,
    master_asset_id INTEGER NOT NULL REFERENCES assets(id),
    kind            TEXT    NOT NULL,       -- viewing_copy | restored
    location        TEXT    NOT NULL,       -- path, or library UUID
    created_at      TEXT    NOT NULL
);

CREATE INDEX idx_derivatives_master ON derivatives(master_asset_id);
