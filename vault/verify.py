"""Verifying the vault against the index.

Bit rot, a truncated copy, and a file quietly deleted by something else all look
identical to a healthy archive until someone checks. This is that check.

It reads; it never repairs. A repair is a judgement about which copy is
authoritative, and that belongs to a person with the mirror in front of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from index.db import Index
from mediafiles import iter_media, sha256_file


@dataclass
class VaultVerifyResult:
    checked: int = 0
    ok: int = 0
    corrupted: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)
    """Files present in the vault that the index doesn't know about.

    Usually an interrupted ingest: the copy landed, the index write didn't.
    """

    @property
    def passed(self) -> bool:
        return not (self.corrupted or self.missing or self.untracked)


def verify_vault(vault_root: Path, index: Index, deep: bool = True) -> VaultVerifyResult:
    """Check every indexed asset against the bytes on disk.

    ``deep`` re-hashes file contents. With it off, only existence and size are
    checked — much faster, and enough to catch an accidental deletion, but blind
    to silent corruption.
    """
    root = Path(vault_root).expanduser()
    result = VaultVerifyResult()
    indexed_paths: set[str] = set()

    for asset in index.assets():
        indexed_paths.add(asset.vault_path)
        target = root / asset.vault_path
        result.checked += 1

        if not target.is_file():
            result.missing.append(asset.vault_path)
            continue
        if target.stat().st_size != asset.filesize:
            result.corrupted.append(asset.vault_path)
            continue
        if deep and sha256_file(target) != asset.sha256:
            result.corrupted.append(asset.vault_path)
            continue
        result.ok += 1

    for path in iter_media(root):
        relative = path.relative_to(root).as_posix()
        if relative not in indexed_paths:
            result.untracked.append(relative)

    return result
