"""Pre-flight checks.

Run this on the contributor's Mac before anything else. Every problem it finds
is one that would otherwise surface partway through a multi-hour export, in
someone else's house, with their photo library open.

It also resolves the one thing that cannot be checked from a development
machine: whether the installed osxphotos actually spells its export flags the
way this package expects.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from settings import Config, ConfigError, load_config

from . import export as export_mod


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    fatal: bool = True

    @property
    def symbol(self) -> str:
        if self.ok:
            return "PASS"
        return "FAIL" if self.fatal else "WARN"


def check_platform() -> Check:
    if sys.platform == "darwin":
        return Check("Platform", True, "macOS")
    return Check(
        "Platform",
        False,
        f"{sys.platform} — inventory and export need macOS. The contract logic, "
        f"manifest, and verify steps run here fine.",
    )


def check_osxphotos_module() -> Check:
    try:
        import osxphotos
    except ImportError:
        return Check(
            "osxphotos module",
            False,
            'not installed — run: pip install -e ".[macos]"',
        )
    version = getattr(osxphotos, "__version__", "unknown")
    return Check("osxphotos module", True, f"version {version}")


def check_osxphotos_cli() -> Check:
    path = shutil.which("osxphotos")
    if path:
        return Check("osxphotos CLI", True, path)
    return Check("osxphotos CLI", False, "not on PATH — the export step shells out to it")


def check_export_flags() -> list[Check]:
    """Confirm every flag the export step intends to use exists in this version."""
    try:
        help_text = export_mod.osxphotos_help()
    except export_mod.ExportError as exc:
        return [Check("Export flags", False, str(exc))]

    flags = export_mod.export_flags(Path("uuids.txt"), Path("report.csv"))
    missing_required, missing_optional = export_mod.check_flags(help_text, flags)

    checks: list[Check] = []
    if not missing_required and not missing_optional:
        checks.append(Check("Export flags", True, f"all {len(flags)} supported"))
        return checks

    for flag in missing_required:
        hint = export_mod.suggest_flag(flag.flag, help_text)
        detail = f"{flag.flag} not found in this osxphotos version"
        if hint:
            detail += f" — closest matches: {', '.join(hint)}"
        checks.append(Check(f"Export flag {flag.flag}", False, detail))

    for flag in missing_optional:
        checks.append(
            Check(
                f"Export flag {flag.flag}",
                False,
                f"not available; export will run without it ({flag.purpose})",
                fatal=False,
            )
        )
    return checks


def check_library(path: Path | None) -> Check:
    """Open the library read-only and count items."""
    from .library import LibraryError, open_library

    try:
        library = open_library(path)
        count = sum(1 for _ in library.items())
    except LibraryError as exc:
        return Check("Photo library", False, str(exc))
    except Exception as exc:  # noqa: BLE001 - report anything, never crash the doctor
        return Check(
            "Photo library",
            False,
            f"{exc}\nIf this mentions permissions, grant Full Disk Access to your "
            f"terminal in System Settings > Privacy & Security.",
        )
    return Check("Photo library", True, f"{count:,} items readable in {library.description}")


def check_config(config_path: Path | None) -> tuple[Check, Config | None]:
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        return Check("Config", False, str(exc)), None
    subjects = ", ".join(s.name for s in config.family.subjects)
    return (
        Check(
            "Config",
            True,
            f"{config.path.name}: cutoff {config.contribute.cutoff_date}, "
            f"{len(config.family.subjects)} subjects ({subjects})",
        ),
        config,
    )


def check_destination(config: Config | None) -> Check:
    if config is None or config.contribute.destination is None:
        return Check(
            "Destination",
            False,
            "no [contribute] destination set — required before export, not before inventory",
            fatal=False,
        )
    destination = config.contribute.destination
    if not destination.exists():
        return Check(
            "Destination",
            False,
            f"{destination} does not exist — plug in the drive, or leave this until "
            f"after the dry run reports the size",
            fatal=False,
        )
    free = shutil.disk_usage(destination).free
    return Check("Destination", True, f"{destination} — {free / 1024**3:,.1f} GB free")


def run_checks(config_path: Path | None = None, library_path: Path | None = None) -> list[Check]:
    checks: list[Check] = [check_platform()]
    config_check, config = check_config(config_path)
    checks.append(config_check)

    if sys.platform == "darwin":
        checks.append(check_osxphotos_module())
        checks.append(check_osxphotos_cli())
        if shutil.which("osxphotos"):
            checks.extend(check_export_flags())
        checks.append(check_library(library_path))

    checks.append(check_destination(config))
    return checks


def render_checks(checks: list[Check]) -> str:
    lines = ["", "PRE-FLIGHT CHECKS", "=" * 68]
    for check in checks:
        lines.append(f"[{check.symbol}] {check.name}")
        if check.detail:
            for line in check.detail.splitlines():
                lines.append(f"       {line}")
    lines.append("=" * 68)

    failures = [c for c in checks if not c.ok and c.fatal]
    warnings = [c for c in checks if not c.ok and not c.fatal]
    if failures:
        lines.append(f"{len(failures)} blocking problem(s). Fix before running the export.")
    elif warnings:
        lines.append(f"Ready, with {len(warnings)} warning(s).")
    else:
        lines.append("All checks passed.")
    lines.append("")
    return "\n".join(lines)
