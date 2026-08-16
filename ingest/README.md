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

## Requirements

`exiftool` is required. It reads the dates and dimensions this pipeline refuses
to guess at, for both stills and video.

```bash
brew install exiftool                        # macOS
winget install OliverBetz.ExifTool           # Windows
sudo apt install libimage-exiftool-perl      # Debian/Ubuntu
```

## Commands

```bash
family-memories ingest run --dry-run   # what would happen; writes nothing
family-memories ingest run             # do it
```

## Ordering

Files are processed highest-resolution first, not in filesystem order. Otherwise
results depend on how the filesystem enumerates — Windows sorts paths
case-insensitively, so a subfolder can precede a top-level file there and follow
it on macOS. For byte-identical duplicates that only decides which name is kept;
for a downscaled twin it decides whether the full-resolution master is archived
or diverted to the review queue.

## Status

Implemented: dedupe, date normalization, vault filing, and index recording. The
push to the living library is build order step 4 and is not here yet.
