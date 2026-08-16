"""``family-memories ingest`` — one inbox, one command."""

from __future__ import annotations

from pathlib import Path

import click

from index.cli import format_size
from index.db import IndexDatabaseError, open_index
from settings import ConfigError, load_config

from .metadata import ExiftoolError, exiftool_version
from .pipeline import IngestResult, ingest

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


@click.group(name="ingest")
def ingest_group() -> None:
    """Bring new files into the vault: dedupe, date, file, record."""


@ingest_group.command(name="run")
@config_option
@click.option(
    "--inbox",
    type=click.Path(path_type=Path, file_okay=False),
    default=None,
    help="Folder to ingest (default: [ingest] inbox in config.toml).",
)
@click.option(
    "--vault",
    "vault_override",
    type=click.Path(path_type=Path, file_okay=False),
    default=None,
    help="Vault root (default: [vault] path in config.toml).",
)
@click.option(
    "--source",
    "source_label",
    default=None,
    help="Label recording where this batch came from.",
)
@click.option("--dry-run", is_flag=True, help="Report what would happen. Writes nothing.")
@click.option("--quiet", is_flag=True, help="Suppress per-file progress.")
def run(
    config_path: Path | None,
    inbox: Path | None,
    vault_override: Path | None,
    source_label: str | None,
    dry_run: bool,
    quiet: bool,
) -> None:
    """Ingest everything in the inbox.

    Nothing in the inbox is modified or deleted, so this is safe to re-run: a
    second pass recognises what it already imported and does nothing. Always
    worth a --dry-run first.
    """
    config = _config(config_path)

    inbox_path = inbox or config.ingest.inbox
    if inbox_path is None:
        raise click.ClickException(
            "No inbox. Set [ingest] inbox in config.toml or pass --inbox."
        )
    vault_root = vault_override or config.vault.path
    if vault_root is None:
        raise click.ClickException(
            "No vault path. Set [vault] path in config.toml or pass --vault."
        )
    if not dry_run:
        vault_root.mkdir(parents=True, exist_ok=True)

    try:
        version = exiftool_version()
    except ExiftoolError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Inbox:    {inbox_path}")
    click.echo(f"Vault:    {vault_root}")
    click.echo(f"Index:    {config.index.path}")
    click.echo(f"exiftool: {version}")
    if dry_run:
        click.secho("DRY RUN — nothing will be copied or recorded.", fg="yellow")
    click.echo("")

    def progress(path: str, position: int, total: int) -> None:
        if not quiet:
            click.echo(f"  [{position}/{total}] {Path(path).name}", err=True)

    try:
        with open_index(config.index.path) as index:
            result = ingest(
                inbox_path,
                vault_root,
                index,
                threshold=config.ingest.phash_threshold,
                source_label=source_label,
                dry_run=dry_run,
                progress=progress,
            )
    except (IndexDatabaseError, ExiftoolError) as exc:
        raise click.ClickException(str(exc)) from exc
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo("")
    click.echo(render_result(result, dry_run))
    if result.errors:
        raise click.ClickException(f"{len(result.errors)} file(s) failed. Nothing was lost.")


def render_result(result: IngestResult, dry_run: bool) -> str:
    lines: list[str] = []
    add = lines.append

    add("=" * 60)
    add("INGEST DRY RUN — nothing was written" if dry_run else "INGEST COMPLETE")
    add("=" * 60)
    add(f"  Scanned:   {result.scanned:,} files")
    add(f"  Imported:  {result.imported:,} ({format_size(result.imported_bytes)})")
    add(f"  Linked:    {result.linked:,} ({format_size(result.linked_bytes)}) — already archived")
    for reason, count in sorted(result.linked_reasons.items()):
        add(f"      {count:>8,}  {reason}")
    add(f"  Deferred:  {result.deferred:,} — awaiting a human decision")
    if result.failed:
        add(f"  Failed:    {result.failed:,}")

    add("")
    if result.balanced:
        add("  Every scanned file is accounted for.")
    else:
        # Should be unreachable. If it ever prints, a file went missing from
        # the accounting and that is more serious than any count above.
        add(
            f"  ACCOUNTING MISMATCH: {result.accounted:,} accounted vs "
            f"{result.scanned:,} scanned. Please report this."
        )

    if result.review_reasons:
        add("")
        add("  Queued for review:")
        for kind, count in sorted(result.review_reasons.items()):
            add(f"      {count:>8,}  {kind}")
        add("  See them with 'family-memories index review'.")

    if result.undated:
        add("")
        add(f"  {result.undated:,} file(s) had no readable date. They were archived under")
        add("  'undated/' rather than filed under a guess, and flagged for review.")

    if result.renamed:
        add("")
        add(f"  {result.renamed:,} file(s) got a checksum suffix to avoid a name collision.")

    if result.errors:
        add("")
        add("  Errors:")
        for message in result.errors[:10]:
            add(f"      {message}")
        if len(result.errors) > 10:
            add(f"      ... and {len(result.errors) - 10:,} more")

    add("=" * 60)
    return "\n".join(lines)
