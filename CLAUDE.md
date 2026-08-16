# CLAUDE.md

Conventions and guardrails for working in this repo. Read [`docs/design.md`](docs/design.md)
for the architecture and the reasoning behind it.

## What this project is

Tools for consolidating a family's photos into a permanent searchable archive and
selecting prints for a gallery wall. It is **general-purpose software meant for any
family**, not a script for one household.

## Guardrails

These are not style preferences. Violating one is a bug.

**1. No real names, ever.** No relative's name, no address, no household
measurement appears in tracked source, docs, comments, tests, or commit messages.
Personal specifics belong in `config.toml` (gitignored) or `private/` (gitignored).
When writing examples or fixtures, use placeholders — "Child One", "the contributor",
"the owner". Assume everything committed here becomes public eventually.

**2. No family media in the repo.** Images, videos, the index database, GEDCOM
exports, and generated manifests are all gitignored. Never `git add -f` past that.
Test fixtures are synthetic files, generated at test time.

**3. Contributor libraries are read-only.** Code under `contribute/` may open a
photo library, copy from it, and nothing else. No writes, no deletions, no metadata
edits, no network calls of any kind. If you find yourself needing a write to a
contributor's library, the design is wrong — stop and raise it.

**4. Vault originals are immutable.** Once a file is filed into the vault it is never
modified or moved. Enrichment goes into the index and into derivative copies. Vault
writes are create-only.

**5. Nothing is silently guessed.** Unknown dates, ambiguous face matches, and
borderline duplicates get recorded as "needs review" and surfaced to a human. Never
pick the most likely answer and move on. Where a threshold exists, ambiguity inside
the margin is a flag, not a decision.

**6. Dry run before anything destructive or slow.** Every command that copies,
imports, or files at scale takes `--dry-run` and reports counts and sizes before
doing real work.

## Layout

Top-level packages are single lowercase words with no hyphens, because they're
imported: `contribute`, `vault`, `index`, `ingest`, `enrich`, `heritage`. Each has a
README describing its scope. Don't add a package without a reason from the design
doc; don't split one preemptively.

## Conventions

- Python 3.11+, type hints on public functions.
- `pathlib.Path` for all paths — never string concatenation. Development happens on
  Windows and macOS both.
- Config is read once into a typed object and passed down. No module-level reads of
  `config.toml`, no globals.
- CLI entry points use `click`. Every long-running command reports progress; libraries
  of this size take hours and a silent process is indistinguishable from a hung one.
- Format and lint with `ruff`. Test with `pytest`.
- Errors on irreplaceable data fail loudly and stop. Never `except: pass` around a
  file operation.

## Platform notes

`osxphotos` is macOS-only, so `contribute/` and the living-library leg of `ingest/`
only run there. Keep that dependency isolated behind an interface so `vault/`,
`index/`, and the rest of `ingest/` stay cross-platform and testable anywhere.

## Commits

Describe behavior, not files touched. Never include a real name in a commit message —
including in test data being added.
