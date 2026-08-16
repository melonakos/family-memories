"""``family-memories vault`` — inspect and verify the canonical archive."""

from __future__ import annotations

from pathlib import Path

import click

from index.db import IndexDatabaseError, open_index
from settings import ConfigError, load_config

from .verify import verify_vault

config_option = click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Path to config.toml (default: search upward from the current directory).",
)


def _config(config_path: Path | None):
    try:
        return load_config(config_path)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc


def require_vault(config, override: Path | None) -> Path:
    root = override or config.vault.path
    if root is None:
        raise click.ClickException(
            "No vault path. Set [vault] path in config.toml or pass --vault."
        )
    if not root.is_dir():
        raise click.ClickException(f"Vault directory does not exist: {root}")
    return root


@click.group(name="vault")
def vault_group() -> None:
    """The canonical archive: immutable originals, checksummed."""


@vault_group.command()
@config_option
@click.option(
    "--vault",
    "vault_override",
    type=click.Path(path_type=Path, file_okay=False),
    default=None,
    help="Vault root (default: [vault] path in config.toml).",
)
@click.option(
    "--quick",
    is_flag=True,
    help="Check existence and size only, skipping checksums. Fast, but blind to bit rot.",
)
def verify(config_path: Path | None, vault_override: Path | None, quick: bool) -> None:
    """Re-checksum the vault against the index.

    Run this after any move between drives, and on a schedule thereafter.
    Silent corruption is only silent until someone looks.
    """
    config = _config(config_path)
    root = require_vault(config, vault_override)

    click.echo(f"Verifying {root}{' (quick)' if quick else ''}...")
    try:
        with open_index(config.index.path, create=False) as index:
            result = verify_vault(root, index, deep=not quick)
    except IndexDatabaseError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"  {result.ok:,} of {result.checked:,} indexed assets intact")

    for label, items, colour in (
        ("CORRUPTED", result.corrupted, "red"),
        ("MISSING", result.missing, "red"),
        ("IN VAULT BUT NOT INDEXED", result.untracked, "yellow"),
    ):
        if not items:
            continue
        click.secho(f"  {label}: {len(items):,}", fg=colour)
        for name in items[:20]:
            click.echo(f"    {name}")
        if len(items) > 20:
            click.echo(f"    ... and {len(items) - 20:,} more")

    if result.passed:
        click.secho("Verified. Every indexed asset matches.", fg="green")
        return

    if result.untracked and not result.corrupted and not result.missing:
        click.echo("")
        click.echo("Untracked files usually mean an ingest was interrupted after the")
        click.echo("copy but before the index write. Re-running ingest will adopt them.")
    raise click.ClickException("Verification failed. Do not treat this vault as good.")
