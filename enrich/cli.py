"""``family-memories enrich`` — additive metadata for archived assets.

Nothing here modifies a vault original. Every command writes to the index only,
and every value it writes records where it came from.
"""

from __future__ import annotations

from pathlib import Path

import click

from index.db import IndexDatabaseError, open_index
from settings import ConfigError, load_config

from .backfill import backfill_locations
from .locations import infer_locations
from .persons import tag_from_source

config_option = click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Path to config.toml (default: search upward from the current directory).",
)
dry_run_option = click.option(
    "--dry-run", is_flag=True, help="Report what would change. Writes nothing."
)


def _config(config_path: Path | None):
    try:
        return load_config(config_path)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc


@click.group(name="enrich")
def enrich_group() -> None:
    """Add person tags, locations, and captions to the index.

    Additive only: originals in the vault are never modified.
    """


@enrich_group.command()
@config_option
@click.argument("source_dir", type=click.Path(path_type=Path, file_okay=False, exists=True))
@dry_run_option
def persons(config_path: Path | None, source_dir: Path, dry_run: bool) -> None:
    """Tag archived assets with the people named in SOURCE_DIR.

    Reads a manifest.csv if the folder has one, otherwise JSON sidecars.
    Matching is by checksum, so tags land correctly even on files the vault
    renamed to resolve a collision.
    """
    config = _config(config_path)
    try:
        with open_index(config.index.path, create=False) as index:
            result = tag_from_source(source_dir, index, config.family, dry_run=dry_run)
    except IndexDatabaseError as exc:
        raise click.ClickException(str(exc)) from exc
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc

    if dry_run:
        click.secho("DRY RUN — nothing was written.", fg="yellow")
    click.echo(f"  Records read:  {result.files_read:,}")
    click.echo(f"  Matched:       {result.matched:,} ({result.coverage:.0%} of those read)")
    if result.unmatched:
        click.echo(f"  Not in vault:  {result.unmatched:,}")
    click.echo(f"  Tags applied:  {result.tags_applied:,}")

    if result.people:
        click.echo("")
        click.echo("  People tagged:")
        for name, count in sorted(result.people.items(), key=lambda kv: -kv[1]):
            click.echo(f"      {count:>8,}  {name}")

    if result.unknown_people:
        click.echo("")
        click.secho("  Names not in the configured roster:", fg="yellow")
        for name, count in sorted(result.unknown_people.items(), key=lambda kv: -kv[1]):
            click.echo(f"      {count:>8,}  {name}")
        click.echo("")
        click.echo("  If any of these is a nickname for one of the children, add it to")
        click.echo("  that subject's `tags` in config.toml and re-run. Left as-is, their")
        click.echo("  photos won't count toward that child's share of the wall.")


@enrich_group.command()
@config_option
@click.option(
    "--window-hours",
    type=float,
    default=None,
    help="Override [enrich] location_window_hours for this run.",
)
@dry_run_option
def locations(config_path: Path | None, window_hours: float | None, dry_run: bool) -> None:
    """Infer locations for photos taken near ones that have coordinates.

    Only same-day neighbours within the window are used, and only
    camera-recorded locations serve as anchors — inferring from an inference
    would let one guess spread across the archive.
    """
    config = _config(config_path)
    window = window_hours or config.enrich.location_window_hours
    try:
        with open_index(config.index.path, create=False) as index:
            result = infer_locations(index, window_hours=window, dry_run=dry_run)
    except IndexDatabaseError as exc:
        raise click.ClickException(str(exc)) from exc

    if dry_run:
        click.secho("DRY RUN — nothing was written.", fg="yellow")
    click.echo(f"  Window: {window} hours, same day only")
    click.echo(f"  Unlocated photos considered: {result.considered:,}")
    click.echo(f"  Locations inferred:          {result.inferred:,}")
    for band, count in sorted(result.by_confidence.items()):
        click.echo(f"      {count:>8,}  {band} confidence")
    if result.skipped_no_neighbour:
        click.echo(f"  Left alone (no near neighbour): {result.skipped_no_neighbour:,}")
    click.echo("")
    click.echo("  Inferred locations are stored as 'inferred', never as recorded.")


@enrich_group.command()
@config_option
@click.option(
    "--vault",
    "vault_override",
    type=click.Path(path_type=Path, file_okay=False),
    default=None,
    help="Vault root (default: [vault] path in config.toml).",
)
@dry_run_option
def backfill(config_path: Path | None, vault_override: Path | None, dry_run: bool) -> None:
    """Re-read metadata from vault originals to fill gaps in the index.

    Use after a schema change adds a field, or after a metadata bug is fixed.
    Reads the vault; never writes to it.
    """
    config = _config(config_path)
    root = vault_override or config.vault.path
    if root is None:
        raise click.ClickException(
            "No vault path. Set [vault] path in config.toml or pass --vault."
        )

    click.echo(f"Re-reading originals under {root}...")
    try:
        with open_index(config.index.path, create=False) as index:
            result = backfill_locations(root, index, dry_run=dry_run)
    except IndexDatabaseError as exc:
        raise click.ClickException(str(exc)) from exc

    if dry_run:
        click.secho("DRY RUN — nothing was written.", fg="yellow")
    click.echo(f"  Examined:            {result.examined:,}")
    click.echo(f"  Locations recovered: {result.located:,}")
    click.echo(f"  Still unlocated:     {result.still_unlocated:,}")
    if result.missing_files:
        click.secho(f"  MISSING FROM VAULT:  {len(result.missing_files):,}", fg="red")
        for name in result.missing_files[:10]:
            click.echo(f"      {name}")
        click.echo("  Run 'family-memories vault verify' — these should not be missing.")
