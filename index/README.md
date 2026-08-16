# index

The SQLite database that ties everything together, plus the shared data layer every
other module reads and writes through.

The index is what makes "tracked and maintained" a real claim rather than a hope. It
is also the only place enrichment lives — the vault holds bytes, the index holds
everything we know about them.

## What it stores

- **Assets** — stable ID, vault path, original filename, dates and timezone,
  date-confidence flag.
- **Hashes** — SHA-256 for exact matching, perceptual hash for near-duplicate
  detection.
- **Derivatives** — links from an original to its viewing copies, low-resolution
  twins that were linked rather than imported, and any restored versions.
- **Living-library links** — the browsing library's UUID and the `vault:` keyword,
  so any item on a phone traces back to its original.
- **People** — person tags carried in from contribution manifests and face data.
- **Genealogy** — the graph loaded from GEDCOM, and links from ancestors to their
  portraits.
- **Review queue** — anything flagged as uncertain: unknown dates, ambiguous
  duplicates, unmatched people. Nothing is auto-resolved out of this queue.

## Rebuildability

The index should be reconstructible from the vault plus the GEDCOM. Hashes and dates
re-derive from the files themselves; the parts that cannot be re-derived — human
review decisions, captions, genealogy links — are the parts worth backing up
separately. Keep that distinction in mind when adding a column.

The database file is gitignored.

## Commands

```bash
family-memories index init      # create or migrate the database
family-memories index status    # what the archive contains
family-memories index review    # items the pipeline refused to decide alone
```

## Status

Implemented. Schema v1 covers assets, linked files, the review queue, sources,
people, and derivatives. Migrations are versioned in `schema_version`; a database
written by a newer build is refused rather than risked.
