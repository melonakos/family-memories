# family-memories

[![tests](https://github.com/melonakos/family-memories/actions/workflows/tests.yml/badge.svg)](https://github.com/melonakos/family-memories/actions/workflows/tests.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Tools for consolidating a family's photos and videos into a permanent, searchable
archive — and for turning the best of it into something you can hang on a wall.

Most families' pictures are scattered across several phones, a few old hard drives,
and more than one person's cloud account. Some of those libraries belong to people
who are no longer in the same household. This project handles that situation
carefully: it gathers what's scattered, keeps the originals untouched forever, makes
the whole collection searchable from a phone, and helps choose a balanced set of
prints for a gallery wall.

**Status:** early. Design is settled; implementation starts with the contribution kit.

---

## The four ground rules

Everything in this repo follows these. They are the reason to trust the tools with
irreplaceable files.

1. **Consent-forward.** Any tool that reads someone else's photo library is
   copy-only, runs entirely offline, and ends with that person privately reviewing
   what was copied before it changes hands. Nothing is deleted or modified in a
   contributor's library, ever. The code is readable so anyone can verify that.
2. **Vault immutability.** Original files are never modified. Every enrichment —
   keywords, captions, genealogy links — is additive, stored in the index and
   written only to viewing or export copies.
3. **One primary per job.** A living library you browse daily, a canonical vault
   that is the archive of record, and an offsite backup leg. No second system
   claiming to be the source of truth.
4. **Nothing silently guessed.** Unknown dates, uncertain face matches, and
   borderline duplicates are flagged for a human, never auto-resolved.

---

## What's here

| Module | Job |
| --- | --- |
| `contribute/` | Copy an agreed subset of photos out of a contributor's library to a drive, with a dry-run inventory first |
| `vault/` | The canonical archive: filing, checksums, local mirror, offsite sync |
| `index/` | SQLite schema and shared data layer — assets, hashes, people, genealogy |
| `ingest/` | One-command pipeline: dedupe, normalize dates, file to vault, push to the living library |
| `enrich/` | Additive metadata: person keywords, music identification on videos, locations |
| `heritage/` | GEDCOM load, genealogy graph, print selection engine, gallery wall planner |

Full rationale for each decision is in [`docs/design.md`](docs/design.md).

---

## Configuration

Nothing about a specific family is hardcoded. You describe your household once in a
`config.toml`, and the tools read from it — the roster of children, the date cutoff
for a contribution, per-child print quotas, vault and backup paths, wall dimensions.

Copy [`config.example.toml`](config.example.toml) to `config.toml` and edit it.
`config.toml` is gitignored, because it contains your family's real names.

```bash
cp config.example.toml config.toml
```

---

## Requirements

- **Python 3.11+**
- **[exiftool](https://exiftool.org)** — required by the ingest pipeline. It reads
  the dates and dimensions the pipeline refuses to guess at.
  `brew install exiftool` · `winget install OliverBetz.ExifTool` ·
  `sudo apt install libimage-exiftool-perl`
- **macOS** for the contribution kit and the living-library leg — both are built on
  [`osxphotos`](https://github.com/RhetTbull/osxphotos), which reads the Apple Photos
  library. The vault, index, and ingest pipeline are cross-platform.
- Enough disk for two full copies of the archive, plus an offsite target.

## Install

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"        # add ",macos" on a Mac for the osxphotos legs
```

## Quick start

The contribution kit is the first working piece. See how the copy contract
behaves against a synthetic library — no Mac and no real photos needed:

```bash
cp config.example.toml config.toml   # then edit the roster and cutoff date
family-memories contribute inventory --demo
```

On the contributor's Mac, `family-memories contribute doctor` checks the
environment, then `inventory` reports what would be copied and how large it is.
Full walkthrough in [`contribute/README.md`](contribute/README.md).

The archive pipeline runs anywhere. Point it at a folder of photos:

```bash
family-memories index init
family-memories ingest run --dry-run    # what would happen; writes nothing
family-memories ingest run              # copy into the vault, record the index
family-memories index status            # what the archive now contains
family-memories index review            # anything it refused to decide alone
family-memories vault verify            # re-checksum the vault
```

Re-running `ingest run` is safe — it never modifies the inbox, and a second pass
recognises everything it already imported.

---

## Privacy

This repo holds code and design documents only. Photos, videos, the index database,
GEDCOM exports, and export manifests are all gitignored — manifests and GEDCOMs
because they list real people by name. Household-specific notes live in `private/`,
which is never committed.

If you fork this, keep it that way.

## License

MIT — see [LICENSE](LICENSE).
