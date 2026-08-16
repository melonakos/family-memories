# Design

The reasoning behind the architecture. Three projects, built in order: a
consent-forward transfer out of a contributor's photo library, a permanent two-tier
archive with rich search, and a selection engine feeding a genealogy-aware gallery
wall.

This document is deliberately free of any particular family's details. Names, dates,
paths, and room dimensions are configuration — see [`../config.example.toml`](../config.example.toml).

---

## Vocabulary

| Term | Meaning |
| --- | --- |
| **Owner** | The person maintaining the archive |
| **Contributor** | Anyone who shares photos from their own library — a co-parent, a grandparent, a sibling |
| **Subject** | A child (or other person) the archive is being built around; the roster is config |
| **Vault** | The canonical, immutable archive of originals |
| **Living library** | The everyday browsing library that syncs to phones |
| **Index** | The SQLite database tying it all together |

---

## Ground rules

1. **Consent-forward.** Tools that touch anyone else's library are copy-only, run
   offline, and end with that person's private review before anything changes hands.
   Open source is a trust feature: anyone can read what the code does.
2. **Vault immutability.** Original files are never modified. All enrichment
   (keywords, captions, links) is additive — stored in the index and written only to
   viewing or export copies.
3. **One primary per job.** The living library is where you browse; the vault is the
   canonical archive; offsite cloud storage is the backup leg. No second "primary"
   photo system.
4. **Nothing silently guessed.** Unknown dates, uncertain matches, and borderline
   duplicates get flagged for human review, not auto-resolved.

---

## Project 1 — The Contribution Kit

A Python tool built on `osxphotos` that runs on a contributor's Mac and copies an
agreed set of photos and videos to an external drive. Written once, general enough
for any relative who wants to contribute.

### The copy contract

The contract is the whole point: the contributor agrees in advance to exactly what
leaves their library, and the tool provably does only that. Both parties should read
this section together before it runs.

Expressed in config, the default shape is:

**Copied to the drive**

1. Everything dated before a **cutoff date** — photos, videos, screenshots, all of it.
   The cutoff is typically the point where the two households' libraries diverge.
2. On or after the cutoff — only items tagged in the contributor's People album with
   one of the configured **subjects**. Any subject tag present in that library counts.

**Never copied**

3. Screenshots dated on or after the cutoff — a hard override, applied even if a
   tagged face appears in one.
4. The Hidden album, Recently Deleted, and shared items the contributor doesn't own.
5. Anything the contributor removes during review.

### Hard guarantees

- Nothing is ever deleted or modified in the contributor's library or their cloud
  account. The tool is strictly read-only toward their data.
- It makes no network calls.
- Review removals are not itemized anywhere. The final manifest is regenerated from
  what remains on the drive, so the owner never receives a list of what was withheld.

That last guarantee matters more than it looks. A review pass is only honest if
declining to share something is invisible.

### Stages

1. **Inventory (dry run).** Scan-only, changes nothing. Reports item counts by rule,
   total export size, how much is cloud-only and must download first (expect long
   waits if the library uses Optimize Mac Storage), how many post-cutoff screenshots
   were excluded, unknown-date items, and untagged items in the window just after the
   cutoff — data for deciding whether that gap matters.
2. **Export.** `osxphotos` export of originals with all metadata, Live Photo pairs
   intact, `--download-missing` for cloud originals. Layout: `YYYY/MM/` plus a
   manifest CSV (filename, date, source album, person tags, checksum).
3. **Private review — manual, no app.** For a one-time use, don't build software. The
   contributor reviews the drive in the Finder's gallery view, sorted by date, with
   large previews; deleting works the way they already expect. What matters is that
   the review happens before handoff, not that it has a custom UI. Optionally they can
   drop notes in a plain text file to caption anything — the pipeline picks those up
   later.
4. **Verify and hand off.** Checksum verification, drive formatted exFAT, SSD
   preferred because it makes the review pass much less tedious.

Buy the drive only after the dry run reports the size. Make a second copy promptly —
one drive is not a backup.

---

## Project 2 — Archive and Living Library

### Two tiers

**Tier 1 — the Vault (canonical archive).** Every original, immutable, checksummed,
in a plain `YYYY/MM/` folder tree. A plain tree is deliberate: it stays readable
decades from now with no software at all. Working copy on SSD, local mirror on HDD,
and an `rclone` sync to a **personal** cloud account — never an employer-controlled
one, which you can lose access to overnight. That's a classic 3-2-1 backup.

**Tier 2 — the living library (Apple Photos + iCloud).** Everything compatible
imports at full resolution; "Optimize iPhone Storage" handles keeping phones from
filling up. Size the cloud tier after dedupe numbers land, and remember that quota is
shared with device backups.

Modern phone media needs no transcode leg. If ProRes or another archival codec shows
up, detect it and handle it as a special case rather than transcoding everything.

Initial upload of a terabyte-scale library takes days to weeks. Run it overnight with
the machine awake.

### The ingest pipeline

One inbox folder, one command. Everything arriving — a contribution drive, an old
backup, a camera dump — goes through the same path:

1. **Dedupe.** SHA-256 exact hash plus a perceptual hash against the index. Near
   duplicates from resizing or recompression are caught. A low-resolution file that
   matches a high-resolution master is **linked, not imported** — the archive never
   accumulates twins.
2. **Normalize.** Dates and timezones fixed from EXIF. Unknowns flagged, never
   guessed.
3. **Enrich.** Person keywords carried over from the contribution manifest. Music
   recognition on videos: extract the audio, fingerprint it, caption the result
   (`♫ Title — Artist`). Locations added to items with no GPS.
4. **File.** Original to the vault. A full-quality viewing copy to the living library,
   tagged with a vault-linked keyword such as `vault:AB12CD` so any item on a phone
   can be traced back to its original.
5. **Record.** Write to the SQLite index: asset ID, checksums, perceptual hash, vault
   path, living-library UUID, derivative links, persons, enrichments, genealogy links.

The index is what makes "tracked and maintained" a real claim rather than a hope.

Phone search comes free from this design: captions and keywords sync through the
living library and are matched by its search bar. Searching a song title, a person, a
place, or an ancestor's name just works, with no app to build.

### Sharing

**Now: curated shared albums.** The pipeline queues candidates by keyword flag, but
adding them to a shared album stays a manual approval step. Themed albums work well
("best of the 2010s", or one child through the years). Note that shared albums
compress heavily — roughly 2048px and 720p. Fine for browsing, useless as an archive.
Web links cover relatives on non-Apple devices.

**Phase two: [Immich](https://immich.app) on a small always-on box.** A cheap mini PC
or a retired Mac, Docker install, with an **external library pointed at the vault
read-only** so it cannot modify or delete originals. One login per family member,
face and free-text search, and Tailscale for remote access without exposing anything
to the open internet.

**Rejected: iCloud Shared Photo Library.** It caps at the owner plus five
participants, which a larger family exceeds, and it grants every participant delete
rights over shared items. Wrong trust model for an heirloom archive.

---

## Project 3 — Selection Engine, Genealogy, Wall Planner

### A. Selection engine

Picking which photos deserve wall space is mostly a constraint problem, not an
aesthetics problem.

**Quality signals.** Apple's internal aesthetic and curation scores, which `osxphotos`
exposes, plus face signals — eyes open, in focus, reasonably large in frame. No custom
aesthetic model to begin with; the built-in scores are good enough to rank a shortlist.
Optionally, a vision model can judge the finalists for emotional quality, which is the
one thing the numeric scores miss: genuine laughter versus a posed row of faces.

**Coverage constraints — the real logic.**

- Every subject represented, at multiple ages, with **per-child quotas enforced in
  code**. In a blended family this is the difference between balance being guaranteed
  and balance being hoped for. It is the single most important rule here.
- Whole-family shots weighted up.
- Era spread and event diversity, so one well-photographed vacation can't colonize
  the wall.
- **Eligibility rule.** Photos that include an adult who is not the owner qualify only
  if at least one subject is also in the shot. Configurable per person, and the reason
  is social rather than technical: it keeps a wall built for the children from turning
  into a wall about the adults.

**Output.** A shortlist at three to five times the needed frame count. The final cut
is human — ideally made by the family together, which is half the point of the project.

### B. Genealogy

**Reality check.** The FamilySearch API is closed to general public and personal use.
Do not plan around a personal API key.

**Tree data.** Sync through a FamilySearch-certified desktop application (RootsMagic,
MacFamilyTree), export GEDCOM, and load the genealogy graph into the index. GEDCOM is
an ugly format but it is universal and it outlives vendors.

**Portraits.** Family trees are often rich with scanned photographs. Download the best
portrait per ancestor by hand — the photographic era only reaches back three or four
generations, so this is dozens of people and an afternoon or two of work, and it makes
a good project to do with kids. Verify direct lines first: collaborative world trees
accumulate bad merges, and an incorrectly attached ancestor is worse than a missing one.

**Ingest like any other photo.** Keywords (`ancestor`, name, generation, paternal or
maternal line) and captions carrying name plus life dates — "Given Surname, 1874–1951,
great-great-grandfather" — which makes every ancestor instantly findable from a phone.

**Restoration.** Upscaled or restored copies are allowed for printing and are clearly
labeled as such. The untouched scan always stays in the vault.

**The payoff.** With a genealogy graph and person tags in one index, the archive can
generate cross-generation pairings automatically: a child at eight beside their
great-great-grandfather at eight, printed as a framed side-by-side. This is the
feature that justifies the whole index design.

### C. Wall planner

**Inputs.** A photograph of the actual wall plus rough measurements. Frame count is an
**output** of wall capacity, not an input — deciding "I want twenty frames" first is
how galleries end up crowded or sparse.

**Frame-system-first.** Choose one cohesive, purchasable set — matching gallery frames
in mixed standard sizes, plus one or two large anchors for a whole-family shot or a
heritage centerpiece. The layout engine arranges **outer frame dimensions**, not print
sizes: molding adds real width, and mats change the opening. Standard spacing is two
to three inches, with the arrangement's centerline at 57–60 inches, which is gallery
convention and roughly standing eye level.

**Layouts to iterate visually,** rendered onto the wall photo: a uniform grid, a
salon-style organic cluster, or a heritage tree with the children at center and
generations radiating outward.

**Validation.** DPI check for each assigned print size, targeting 150–300 DPI, and
crop previews — phone photos are 4:3 while most frame openings are 3:2 or 5:4, so
flag any assignment that would cut off a head.

**Outputs.** A layout render on the wall photo, a frame shopping list, a print order
list (which photo, what size, which crop), and 1:1 printable hanging templates with
nail positions marked.

**Lighting**, since it determines whether the finished wall looks expensive or cheap:
use 2700K bulbs at CRI 90 or above in *every* fixture in the room. Mismatched color
temperature is the most common thing that undermines a finished gallery, and high CRI
matters specifically for skin tones across several generations of print stock. Aim
adjustable fixtures at roughly 30° from 18–24 inches off the wall — that angle keeps
glare off the glass. Avoid grazing the wall from directly above, which throws raking
light across every drywall seam.

---

## Repo structure

```
family-memories/
├── README.md
├── CLAUDE.md               # conventions + guardrails for AI-assisted work
├── config.example.toml     # copy to config.toml; config.toml is gitignored
├── docs/
│   └── design.md           # this file
├── contribute/             # Project 1: dry-run inventory, filtered export, manifest
├── vault/                  # canonical archive: filing, checksums, mirror + offsite sync
├── index/                  # SQLite schema + shared library (assets, hashes, people, genealogy)
├── ingest/                 # one-command pipeline: dedupe, normalize, file, push to library
├── enrich/                 # additive metadata: person keywords, music ID, locations, ancestors
└── heritage/               # GEDCOM load, genealogy graph, selection engine, wall planner
```

Module names are lowercase single words with no hyphens, because they are Python
packages and get imported. The repo name uses a hyphen, which is fine — it is never
imported. Split `heritage` into `heritage` + `wall` only if it actually grows; not
preemptively.

`.gitignore` excludes all image and video files, the index database, GEDCOM exports,
and generated manifests. Code and docs only, never family media and never real names.

---

## Build order

1. **Repo scaffold + Project 1** — the dry-run inventory and filtered exporter. Fully
   self-contained, and it unblocks scheduling a session with a contributor, which has
   the longest lead time of anything here.
2. **Pipeline core** — index schema, dedupe, vault filing. Develop against the owner's
   own library while waiting on a contribution drive.
3. **Enrichment modules** — music recognition, person keywords, locations.
4. **Living-library import leg** + shared-album candidate queue.
5. **Selection engine** — needs a populated index to be worth anything.
6. **Genealogy** — certified-app sync, GEDCOM load, manual portrait harvest. Runs in
   parallel with everything else, any time.
7. **Wall planner** — layout engine, frame sourcing, print list, hanging templates.
8. **Phase two** — Immich server.
