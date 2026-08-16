"""``family-memories index`` — inspect and maintain the index database."""

from __future__ import annotations

from pathlib import Path

import click

from settings import ConfigError, load_config

from .db import IndexDatabaseError, open_index
from .models import REVIEW_OPEN

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


def format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024.0 or unit == "TB":
            return f"{size:,.1f} {unit}" if unit != "B" else f"{int(size):,} B"
        size /= 1024.0
    return f"{size:,.1f} TB"


@click.group(name="index")
def index_group() -> None:
    """The SQLite index: assets, checksums, people, and the review queue."""


@index_group.command()
@config_option
def init(config_path: Path | None) -> None:
    """Create the index database, or migrate an existing one."""
    config = _config(config_path)
    try:
        with open_index(config.index.path) as index:
            version = index.version
    except IndexDatabaseError as exc:
        raise click.ClickException(str(exc)) from exc
    click.secho(f"Index ready at {config.index.path} (schema version {version})", fg="green")


@index_group.command()
@config_option
def status(config_path: Path | None) -> None:
    """Summarize what the index knows."""
    config = _config(config_path)
    try:
        with open_index(config.index.path, create=False) as index:
            stats = index.stats()
            sources = index.sources()
    except IndexDatabaseError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Index:  {config.index.path}")
    click.echo("")
    click.echo(f"  Assets:       {stats.assets:,} ({format_size(stats.total_bytes)})")
    click.echo(f"                {stats.photos:,} photos, {stats.videos:,} videos")
    if stats.earliest and stats.latest:
        click.echo(f"  Date range:   {stats.earliest:%Y-%m-%d} to {stats.latest:%Y-%m-%d}")
    if stats.undated:
        click.echo(f"  Undated:      {stats.undated:,}")
    click.echo(f"  Sources:      {stats.sources:,}")
    click.echo("")

    click.echo(f"  Linked, not imported: {stats.linked_files:,}")
    for reason, count in sorted(stats.linked_by_reason.items()):
        click.echo(f"      {count:>8,}  {reason}")
    click.echo("")

    if stats.open_reviews:
        click.secho(f"  Needs a human: {stats.open_reviews:,}", fg="yellow")
        for kind, count in sorted(stats.reviews_by_kind.items()):
            click.echo(f"      {count:>8,}  {kind}")
        click.echo("")
        click.echo("  See them with 'family-memories index review'.")
    else:
        click.echo("  Review queue: empty")

    if sources:
        click.echo("")
        click.echo("  Sources:")
        for source in sources:
            click.echo(f"      {source.label} ({source.kind}, {source.ingested_at})")


@index_group.command()
@config_option
@click.option("--kind", default=None, help="Only show one kind of review item.")
@click.option("--limit", type=int, default=50, show_default=True, help="Rows to show.")
@click.option(
    "--resolve",
    type=int,
    default=None,
    metavar="ID",
    help="Mark a review item resolved. Records the decision; changes no files.",
)
@click.option("--note", default="", help="Resolution note stored with --resolve.")
def review(
    config_path: Path | None, kind: str | None, limit: int, resolve: int | None, note: str
) -> None:
    """List items the pipeline refused to decide on its own.

    Nothing here was guessed at. Each row is a file whose date, identity, or
    relationship to an existing asset could not be established without a person
    looking at it.
    """
    config = _config(config_path)
    try:
        with open_index(config.index.path, create=False) as index:
            if resolve is not None:
                index.resolve_review(resolve, note or "resolved")
                click.secho(f"Review item {resolve} marked resolved.", fg="green")
                return

            items = index.reviews(status=REVIEW_OPEN, kind=kind)
    except IndexDatabaseError as exc:
        raise click.ClickException(str(exc)) from exc

    if not items:
        click.echo("Nothing awaiting review.")
        return

    click.echo(f"{len(items):,} item(s) awaiting a decision:")
    click.echo("")
    for item in items[:limit]:
        target = item.original_path or f"asset {item.asset_id}"
        click.echo(f"  [{item.id}] {item.kind}")
        click.echo(f"       {target}")
        for key, value in sorted(item.detail.items()):
            click.echo(f"       {key}: {value}")
    if len(items) > limit:
        click.echo(f"  ... and {len(items) - limit:,} more")
