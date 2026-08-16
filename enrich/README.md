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

## Status

Not yet implemented.
