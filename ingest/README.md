# ingest

One inbox folder, one command. Everything arriving takes the same path — a
contribution drive, an old backup, a camera dump, a shoebox of scans.

## The pipeline

1. **Dedupe** — SHA-256 exact match plus perceptual hash against the index. A
   low-resolution file matching a high-resolution master is **linked, not imported**,
   so the archive never accumulates twins. Matches inside the ambiguous margin are
   flagged for review rather than resolved.
2. **Normalize** — dates and timezones fixed from EXIF. Unknowns are flagged, never
   inferred from filenames or folder names.
3. **Enrich** — hand off to [`../enrich`](../enrich) for person keywords, music
   identification, and locations.
4. **File** — original to the vault; a full-quality viewing copy to the living
   library, tagged `vault:<id>`.
5. **Record** — write everything to the index.

## Interface boundary

Only the final step, pushing to Apple Photos, is macOS-specific. Keep it behind an
interface so dedupe, normalization, and vault filing stay cross-platform and testable
on any machine.

## Dry run

Every command here takes `--dry-run` and reports what it would do — counts by
disposition, total bytes, and the review queue it would generate — before touching
anything. Runs at this scale take hours, and finding out afterward is too late.

## Status

Not yet implemented. Depends on [`../index`](../index).
