"""``family-memories contribute`` — the four stages of the contribution kit.

    doctor      pre-flight checks, run first on the contributor's Mac
    inventory   stage 1, the dry run: what would be copied, and how big
    export      stage 2, the copy — the only command that writes to the drive
    manifest    stage 4a, rebuild the manifest from what survived review
    verify      stage 4b, re-checksum the drive before and after it travels

Stage 3, the contributor's private review, is deliberately not here. It happens
in the Finder. See docs/design.md for why building a UI for it would be worse.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import click

from settings import ConfigError, load_config

from . import doctor as doctor_mod
from . import export as export_mod
from . import manifest as manifest_mod
from .demo import build_demo_library
from .inventory import build_inventory, format_size, render_report, write_json_report
from .library import LibraryError, open_library

config_option = click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Path to config.toml (default: search upward from the current directory).",
)
library_option = click.option(
    "--library",
    "library_path",
    type=click.Path(path_type=Path, file_okay=False),
    default=None,
    help="Path to a .photoslibrary bundle (default: the system photo library).",
)


def _load(config_path: Path | None):
    try:
        return load_config(config_path)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc


@click.group(name="contribute")
def contribute() -> None:
    """Copy an agreed subset of photos out of a contributor's library.

    Read-only toward their library, offline, and reviewed by them before handoff.
    """


@contribute.command()
@config_option
@library_option
def doctor(config_path: Path | None, library_path: Path | None) -> None:
    """Check the environment before running anything that matters."""
    checks = doctor_mod.run_checks(config_path, library_path)
    click.echo(doctor_mod.render_checks(checks))
    if any(not c.ok and c.fatal for c in checks):
        sys.exit(1)


@contribute.command()
@config_option
@library_option
@click.option("--demo", is_flag=True, help="Run against a synthetic library. Works on any OS.")
@click.option(
    "--json-out",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Also write the full report as JSON. Keep this off the destination drive.",
)
def inventory(
    config_path: Path | None,
    library_path: Path | None,
    demo: bool,
    json_out: Path | None,
) -> None:
    """Stage 1 — the dry run. Scans only; copies and changes nothing.

    The export size it reports is what determines which drive to buy, so run
    this before spending any money.
    """
    config = _load(config_path)

    if demo:
        cutoff = datetime.combine(config.contribute.cutoff_date, datetime.min.time())
        library = build_demo_library(config.family, cutoff)
    else:
        try:
            library = open_library(library_path)
        except LibraryError as exc:
            raise click.ClickException(
                f"{exc}\n\nTo see how the rules behave, run with --demo."
            ) from exc

    report, _ = build_inventory(library, config.contribute, config.family)
    click.echo(render_report(report, config.family))

    if json_out:
        if config.contribute.destination and _is_inside(json_out, config.contribute.destination):
            raise click.ClickException(
                "Refusing to write the inventory report onto the destination drive. "
                "It describes the library before review; the drive should only ever "
                "carry what survives it."
            )
        write_json_report(report, json_out)
        click.echo(f"Full report written to {json_out}")


def _describe_missing_flag(flag: export_mod.ExportFlag, help_text: str) -> str:
    """One line explaining a flag this osxphotos version doesn't have."""
    line = f"  {flag.flag} — {flag.purpose}"
    suggestions = export_mod.suggest_flag(flag.flag, help_text)
    if suggestions:
        line += f"\n    closest in this version: {', '.join(suggestions)}"
    return line


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        return path.expanduser().resolve().is_relative_to(parent.expanduser().resolve())
    except (OSError, ValueError):
        return False


@contribute.command()
@config_option
@library_option
@click.option(
    "--destination",
    type=click.Path(path_type=Path, file_okay=False),
    default=None,
    help="Export target (default: [contribute] destination in config.toml).",
)
@click.option(
    "--work-dir",
    type=click.Path(path_type=Path, file_okay=False),
    default=Path(".contribute-work"),
    show_default=True,
    help="Where the selection list and export log go. Never the destination drive.",
)
@click.option("--dry-run", is_flag=True, help="Print the export command without running it.")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
def export(
    config_path: Path | None,
    library_path: Path | None,
    destination: Path | None,
    work_dir: Path,
    dry_run: bool,
    yes: bool,
) -> None:
    """Stage 2 — copy the selected items to the drive.

    Writes only to the destination. Never modifies the contributor's library.
    """
    config = _load(config_path)
    target = destination or config.contribute.destination
    if target is None:
        raise click.ClickException(
            "No destination. Set [contribute] destination in config.toml or pass --destination."
        )

    try:
        warnings = export_mod.check_destination(target)
    except export_mod.ExportError as exc:
        raise click.ClickException(str(exc)) from exc
    for warning in warnings:
        click.secho(f"warning: {warning}", fg="yellow")

    try:
        library = open_library(library_path)
    except LibraryError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo("Scanning the library and applying the copy contract...")
    report, decisions = build_inventory(library, config.contribute, config.family)
    click.echo(
        f"  {report.included_count:,} of {report.scanned:,} items selected, "
        f"{format_size(report.included_bytes)}"
    )
    if report.included_count == 0:
        raise click.ClickException("The contract selected nothing. Check the cutoff date and tags.")

    # Verify the installed osxphotos spells its flags the way we expect,
    # before writing anything anywhere.
    try:
        help_text = export_mod.osxphotos_help()
    except export_mod.ExportError as exc:
        raise click.ClickException(str(exc)) from exc

    work = work_dir.expanduser()
    uuid_file = work / "selected-uuids.txt"
    report_file = work / "export-log.csv"
    flags = export_mod.export_flags(uuid_file, report_file)
    missing_required, missing_optional = export_mod.check_flags(help_text, flags)

    if missing_required:
        details = "\n".join(_describe_missing_flag(f, help_text) for f in missing_required)
        raise click.ClickException(
            f"This osxphotos version does not support flags the export needs:\n{details}\n"
            f"Run 'family-memories contribute doctor' for the full report."
        )
    for flag in missing_optional:
        click.secho(f"warning: {flag.flag} unavailable, continuing without it", fg="yellow")
        flags = [f for f in flags if f.flag != flag.flag]

    try:
        count = export_mod.write_uuid_file(decisions, uuid_file, target)
    except export_mod.ExportError as exc:
        raise click.ClickException(str(exc)) from exc

    command = export_mod.build_export_command(target, flags)
    click.echo("")
    click.echo(f"Selection list: {uuid_file} ({count:,} items)")
    click.echo("Command:")
    click.echo(f"  {' '.join(command)}")
    click.echo("")

    if dry_run:
        click.echo("Dry run — nothing was exported.")
        return

    if report.cloud_only_count:
        click.secho(
            f"{report.cloud_only_count:,} originals must download from iCloud first "
            f"({format_size(report.cloud_only_bytes)}). Keep this Mac awake and plugged in.",
            fg="yellow",
        )
    if not yes:
        click.confirm(f"Export {count:,} items to {target}?", abort=True)

    code = export_mod.run_export(command)
    if code != 0:
        raise click.ClickException(f"osxphotos exited {code}. The drive may be incomplete.")

    click.echo("")
    click.secho("Export complete.", fg="green")
    click.echo("")
    click.echo("Next: the contributor reviews the drive privately, in the Finder.")
    click.echo("  Gallery view, sorted by date, large previews. Deleting works as usual.")
    click.echo("  Nothing they remove is recorded anywhere.")
    click.echo("")
    click.echo("Then run 'family-memories contribute manifest' to catalogue what remains.")


@contribute.command()
@config_option
@click.option(
    "--destination",
    type=click.Path(path_type=Path, file_okay=False),
    default=None,
    help="The reviewed drive (default: [contribute] destination in config.toml).",
)
def manifest(config_path: Path | None, destination: Path | None) -> None:
    """Stage 4a — catalogue what survived the review.

    Run only after the contributor has finished reviewing. The manifest is built
    from the files that remain, so it records what was shared and never what
    was withheld.
    """
    config = _load(config_path)
    target = destination or config.contribute.destination
    if target is None:
        raise click.ClickException("No destination. Pass --destination or set it in config.toml.")

    click.echo(f"Cataloguing {target} and checksumming every file...")
    try:
        rows = manifest_mod.build_manifest(target)
        path = manifest_mod.write_manifest(rows, target)
    except manifest_mod.ManifestError as exc:
        raise click.ClickException(str(exc)) from exc

    total = sum(row.size_bytes for row in rows)
    click.echo(f"  {len(rows):,} files, {format_size(total)}")
    click.secho(f"Manifest written to {path}", fg="green")
    click.echo("")
    click.echo("Make the second copy now — one drive is not a backup — then run")
    click.echo("'family-memories contribute verify' against each copy.")


@contribute.command()
@config_option
@click.option(
    "--destination",
    type=click.Path(path_type=Path, file_okay=False),
    default=None,
    help="The drive to verify (default: [contribute] destination in config.toml).",
)
def verify(config_path: Path | None, destination: Path | None) -> None:
    """Stage 4b — re-checksum a drive against its manifest."""
    config = _load(config_path)
    target = destination or config.contribute.destination
    if target is None:
        raise click.ClickException("No destination. Pass --destination or set it in config.toml.")

    click.echo(f"Verifying {target}...")
    try:
        result = manifest_mod.verify_manifest(target)
    except manifest_mod.ManifestError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"  {result.ok:,} of {result.checked:,} files match their checksum")
    for label, items in (
        ("CORRUPTED", result.corrupted),
        ("MISSING", result.missing),
        ("NOT IN MANIFEST", result.unlisted),
    ):
        if items:
            click.secho(f"  {label}: {len(items):,}", fg="red")
            for name in items[:20]:
                click.echo(f"    {name}")
            if len(items) > 20:
                click.echo(f"    ... and {len(items) - 20:,} more")

    if result.passed:
        click.secho("Verified. Every file matches.", fg="green")
    else:
        raise click.ClickException("Verification failed. Do not treat this copy as good.")
