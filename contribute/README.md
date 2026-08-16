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

## Stages

1. **Inventory** — a dry run that changes nothing and reports counts, total size,
   how much must download from the cloud first, exclusions by rule, unknown-date
   items, and untagged items in the window after the cutoff.
2. **Export** — originals with full metadata, Live Photo pairs intact, filed as
   `YYYY/MM/` with a manifest CSV.
3. **Review** — manual, in the Finder. No software; see the design doc for why.
4. **Verify** — checksum the drive and regenerate the manifest.

Buy the drive only after the inventory reports the size.

## Status

Not yet implemented. This is the first thing to build.
