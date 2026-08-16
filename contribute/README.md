# contribute

**Project 1 — the Contribution Kit.**

Copies an agreed subset of photos and videos out of a contributor's Apple Photos
library onto an external drive, so it can be reviewed privately and handed over.

Built on [`osxphotos`](https://github.com/RhetTbull/osxphotos). **macOS only.**

## The copy contract

Defined in `[contribute]` in `config.toml`. Read it with the contributor before
running anything — the contract is the agreement, and the code exists to prove the
tool honors it. Full text in [`../docs/design.md`](../docs/design.md#the-copy-contract).

## Hard guarantees

This module is **strictly read-only** toward the contributor's data.

- Never deletes, moves, or modifies anything in their library or cloud account.
- Makes no network calls.
- Does not itemize what the contributor removed during review. The final manifest is
  regenerated from what remains on the drive, so nobody receives a list of what was
  withheld — a review pass is only honest if declining to share something is invisible.

## Usage

```bash
family-memories contribute doctor       # pre-flight checks — run this first
family-memories contribute inventory    # stage 1: the dry run
family-memories contribute export       # stage 2: the copy
#                                         stage 3: their review, in the Finder
family-memories contribute manifest     # stage 4a: catalogue what remains
family-memories contribute verify       # stage 4b: re-checksum
```

To see how the copy contract behaves without touching a real library — on any
machine, macOS or not — run the inventory against a synthetic one:

```bash
family-memories contribute inventory --demo
```

That is worth doing *with* the contributor. It shows the rules operating on
invented photos, including the case people find most surprising: a post-cutoff
screenshot with a subject's face in it is still excluded.

## Stages

1. **Inventory** — a dry run that changes nothing and reports counts, total size,
   how much must download from the cloud first, exclusions by rule, unknown-date
   items, and untagged items in the window after the cutoff. Buy the drive only
   after this reports the size.
2. **Export** — originals with full metadata, Live Photo pairs intact, filed as
   `YYYY/MM/`. Delegates to the `osxphotos export` CLI, restricted to the UUIDs
   the contract selected.
3. **Review** — manual, in the Finder. No software; see the design doc for why.
4. **Verify** — regenerate the manifest from what survived, checksum everything,
   then make a second copy and verify that too.

## Design notes

**Flags are verified, not assumed.** osxphotos tracks Apple's Photos schema and
its options move between releases. The export declares its flags as data and
checks them against `osxphotos export --help` before running, so a version
mismatch surfaces in `doctor` rather than halfway through a copy onto someone's
drive.

**Missing properties degrade toward copying less.** The adapter reads osxphotos
properties defensively. Where a property is absent — a rename, a schema change —
the fallback is always the value that copies *fewer* items. An item that can't
be confirmed as the contributor's own is treated as not theirs to give.

**No manifest exists before the review.** There is deliberately no export-time
record of what was selected, because diffing it against the surviving files
would itemize exactly what the contributor chose to withhold. The selection list
is written to a working directory on their Mac and refuses to be written onto
the drive. Sidecars orphaned by a deletion are removed silently, and their count
is never reported.

## Status

Implemented and tested, but **not yet run against a real photo library** — that
requires a Mac. Run `doctor` there first; it is what confirms the osxphotos
assumptions this code makes.
