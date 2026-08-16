"""Stage 2 — the export.

Delegates the actual copying to the ``osxphotos export`` CLI rather than
reimplementing it. That tool already handles the genuinely hard parts: cloud
downloads, Live Photo pairing, edited versions, burst stacks, sidecars, and
retry on flaky iCloud responses. What lives here is the part osxphotos can't
know — which items the contract selected, and the guardrails around where the
bytes are allowed to land.

Flag names are declared as data and **verified against the installed
osxphotos** before anything runs, rather than assumed. osxphotos tracks Apple's
Photos schema and its options move between releases; discovering a renamed flag
by way of a half-finished export onto a contributor's drive is not acceptable.
"""

from __future__ import annotations

import difflib
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .models import Decision

# Filed as YYYY/MM/ to match the vault. osxphotos template syntax.
DIRECTORY_TEMPLATE = "{created.year}/{created.mm}"


class ExportError(Exception):
    """Raised when the export cannot be run safely."""


@dataclass(frozen=True)
class ExportFlag:
    flag: str
    args: tuple[str, ...] = ()
    required: bool = True
    purpose: str = ""
    verified: bool = True
    """False for flags whose exact spelling could not be checked against a real
    osxphotos install while writing this. ``doctor`` resolves all of them."""

    def render(self) -> list[str]:
        return [self.flag, *self.args]


def export_flags(uuid_file: Path, report_path: Path | None) -> list[ExportFlag]:
    """The flags that implement the export stage of the design doc."""
    flags = [
        ExportFlag(
            "--uuid-from-file",
            (str(uuid_file),),
            purpose="Export exactly the items the contract selected, nothing else.",
        ),
        ExportFlag(
            "--directory",
            (DIRECTORY_TEMPLATE,),
            purpose="File as YYYY/MM/, matching the vault layout.",
        ),
        ExportFlag(
            "--download-missing",
            purpose="Pull originals that live only in iCloud. The slow part.",
        ),
        ExportFlag(
            "--sidecar",
            ("XMP",),
            purpose="Standards-compatible metadata that travels with the file.",
        ),
        ExportFlag(
            "--sidecar",
            ("JSON",),
            purpose="Machine-readable metadata; the manifest is rebuilt from these.",
        ),
        ExportFlag(
            "--touch-file",
            required=False,
            purpose=(
                "Set each file's modification time to the date the photo was taken, "
                "so the Finder sorts the review drive chronologically."
            ),
        ),
        ExportFlag(
            "--retry",
            ("3",),
            required=False,
            purpose="Retry transient iCloud download failures.",
        ),
    ]
    if report_path is not None:
        flags.append(
            ExportFlag(
                "--report",
                (str(report_path),),
                required=False,
                purpose="Per-file export log. Written off the drive, never onto it.",
            )
        )
    return flags


def build_export_command(destination: Path, flags: list[ExportFlag]) -> list[str]:
    command = ["osxphotos", "export", str(destination)]
    for flag in flags:
        command.extend(flag.render())
    return command


def osxphotos_help() -> str:
    """Capture ``osxphotos export --help`` for flag verification."""
    if shutil.which("osxphotos") is None:
        raise ExportError(
            "The osxphotos command is not on PATH. Install it with:\n"
            '    pip install -e ".[macos]"'
        )
    try:
        result = subprocess.run(
            ["osxphotos", "export", "--help"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExportError(f"Could not run osxphotos: {exc}") from exc
    return result.stdout + result.stderr


def supported_flags(help_text: str) -> set[str]:
    """Every long option osxphotos' help output mentions."""
    return set(re.findall(r"--[a-zA-Z0-9][a-zA-Z0-9-]*", help_text))


def check_flags(
    help_text: str, flags: list[ExportFlag]
) -> tuple[list[ExportFlag], list[ExportFlag]]:
    """Split the intended flags into (missing_required, missing_optional)."""
    available = supported_flags(help_text)
    missing_required = [f for f in flags if f.flag not in available and f.required]
    missing_optional = [f for f in flags if f.flag not in available and not f.required]
    return missing_required, missing_optional


def suggest_flag(flag: str, help_text: str) -> list[str]:
    """Closest spellings in the installed version, for a useful error message."""
    return difflib.get_close_matches(flag, sorted(supported_flags(help_text)), n=3, cutoff=0.6)


def write_uuid_file(decisions: list[Decision], path: Path, destination: Path) -> int:
    """Write the selected UUIDs to a working file, and return how many.

    Refuses to write inside the destination drive. The drive must end up
    holding photos and a manifest regenerated *after* review — never a record
    of what was originally selected, because diffing that against the surviving
    files would itemize exactly what the contributor chose to withhold.
    """
    resolved = path.expanduser().resolve()
    try:
        inside_destination = resolved.is_relative_to(destination.expanduser().resolve())
    except (OSError, ValueError):
        inside_destination = False
    if inside_destination:
        raise ExportError(
            f"Refusing to write the selection list inside the destination drive "
            f"({resolved}). Keep it on the contributor's Mac: comparing it against "
            f"what survives review would reveal what they chose to withhold."
        )

    uuids = [d.item.uuid for d in decisions if d.is_include]
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text("\n".join(uuids) + "\n", encoding="utf-8")
    return len(uuids)


def check_destination(destination: Path) -> list[str]:
    """Validate the export target. Returns warnings; raises on anything fatal."""
    warnings: list[str] = []
    resolved = destination.expanduser()

    # The read-only promise comes first. This must not depend on whether the
    # path happens to exist yet — a library bundle is off limits either way.
    if ".photoslibrary" in str(resolved):
        raise ExportError(
            f"Destination is inside a Photos library ({resolved}). The export must "
            f"write to a separate drive, never into anyone's library."
        )
    if not resolved.parent.exists():
        raise ExportError(f"Destination's parent directory does not exist: {resolved.parent}")
    if resolved.exists() and not resolved.is_dir():
        raise ExportError(f"Destination exists but is not a directory: {resolved}")
    if resolved.is_dir() and any(resolved.iterdir()):
        warnings.append(
            f"{resolved} is not empty. Exporting into a directory with existing "
            f"files makes the post-review manifest ambiguous."
        )
    return warnings


def run_export(command: list[str], dry_run: bool = False) -> int:
    """Run the export, streaming output. Returns the exit code.

    Not captured — a multi-hour export with an invisible progress log is
    indistinguishable from a hung process.
    """
    if dry_run:
        return 0
    try:
        return subprocess.run(command, check=False).returncode
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExportError(f"Export failed to start: {exc}") from exc
