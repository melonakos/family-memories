# enrich

Additive metadata. Everything here writes to the index and to viewing copies — never
to a vault original.

Enrichment is what makes the archive searchable from a phone without building an app:
captions and keywords sync through the living library and are matched by its own
search bar. Searching a song title, a person, a place, or an ancestor's name just
works.

## What it adds

- **Person keywords** — carried over from a contribution manifest's face tags, and
  from the living library's own face data.
- **Music identification** — extract the audio track from a video, fingerprint it,
  and caption the result `♫ Title — Artist`. Home videos are full of music nobody
  remembers the name of, and it dates footage better than any timestamp.
- **Locations** — fill in GPS for items that lack it, using nearby items from the
  same day as reference. Inferred locations are marked as inferred.
- **Ancestor metadata** — keywords (`ancestor`, name, generation, line) and captions
  carrying name plus life dates, so historical portraits are as findable as last
  week's photos.

## Rules

Every enrichment records its source and confidence. An inferred location and a
camera-recorded one are not the same fact and must not be stored as if they were.
Low-confidence results go to the review queue.

## Commands

```bash
family-memories enrich persons <folder>   # tag from a manifest or sidecars
family-memories enrich locations          # infer places for nearby photos
family-memories enrich backfill           # re-read vault originals to fill gaps
```

All three take `--dry-run`.

## Provenance is the point

Every value records where it came from, because an inference and an observation
are different facts:

- `gps_source` is `exif` or `inferred`, and an inference never overwrites a
  camera-recorded location — whatever order they arrive in.
- Location inference never chains off another inference. Otherwise one guess
  propagates across the archive, each step looking as solid as the last.
- It never crosses midnight. Two photos six hours apart on one afternoon are
  usually in the same place; the same six hours spanning midnight usually
  aren't.
- Anything outside the window is left unlocated. An empty field is honest; a
  wrong coordinate is a fact the archive repeats forever.

Person tags match on **SHA-256, never filename**, so a tag lands on the right
photograph even after the vault renamed it to resolve a collision. Names are
resolved against the configured roster, and any name that isn't in it is still
applied but reported — an unconfigured nickname is exactly how a child's photos
quietly go missing from their share of the wall.

## Status

Person tags, location inference, and backfill are implemented.

**Music identification is not built.** It needs ffmpeg, a Chromaprint
fingerprinter, and an AcoustID key, none of which could be verified here — and
AcoustID matches released recordings, so its hit rate on background music in
home video is likely low. It is a delight feature, not a foundation, and
shipping an untested version of it would be worse than not having it. The design
is in [`../docs/design.md`](../docs/design.md).
