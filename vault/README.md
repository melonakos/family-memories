# vault

The canonical archive. Every original, immutable, checksummed, in a plain `YYYY/MM/`
folder tree.

The plain tree is deliberate. It stays readable decades from now with no software at
all — if every tool in this repo is abandoned, the archive is still just folders of
files that any computer can open.

## Responsibilities

- **Filing** — place an accepted original into `YYYY/MM/` under the vault root.
  Create-only; a path that already exists is an error, never an overwrite.
- **Checksums** — SHA-256 recorded at file time and re-verified on a schedule to
  catch bit rot.
- **Mirror** — sync to a second local drive on separate physical media.
- **Offsite** — `rclone` sync to a personal cloud account. Use a personal account,
  never an employer-controlled one; access to those can vanish overnight.

Together that's a 3-2-1 backup: three copies, two media types, one offsite.

## Immutability

Once a file is filed here it is never modified or moved. All enrichment — keywords,
captions, genealogy links — lives in the index and in derivative copies. Any code
path in this module that opens a vault file for writing is a bug.

## Commands

```bash
family-memories vault verify           # re-checksum against the index
family-memories vault verify --quick   # existence and size only; blind to bit rot
```

## Status

Filing and verification implemented. Mirror and offsite `rclone` sync are
deliberately deferred until the drives exist — there is nothing real to back up
yet, and an untested backup is worse than a known-absent one.
