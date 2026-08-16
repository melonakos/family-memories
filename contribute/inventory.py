"""The dry run.

Scan-only. Applies the copy contract to every item in a library and aggregates
the result into a report, changing nothing anywhere. Its most important output
is the export size, because that is what determines which drive to buy and how
long the session with the contributor needs to be.

The report is also the artifact to walk through *with* the contributor. It says,
in counts they can check against their own sense of their library, exactly what
would leave it.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from settings import ContributeConfig, FamilyConfig

from .contract import add_months, decide
from .library import PhotoLibrary
from .models import Decision, Disposition

# Deliberately pessimistic: iCloud original downloads are rate-limited and
# routinely run far below a household's rated broadband speed. Better to
# over-quote the wait than to have someone plan an afternoon around it.
ASSUMED_DOWNLOAD_MBPS = 25.0

# How many unknown-date filenames to show before truncating. The full list goes
# to the JSON report; the console version stays readable.
UNKNOWN_DATE_SAMPLE = 20


def format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024.0 or unit == "TB":
            return f"{size:,.1f} {unit}" if unit != "B" else f"{int(size):,} B"
        size /= 1024.0
    return f"{size:,.1f} TB"


def format_duration(hours: float) -> str:
    minutes = hours * 60
    if minutes < 1:
        return "under a minute"
    if hours < 1:
        return f"{minutes:.0f} minutes"
    if hours < 24:
        return f"{hours:.1f} hours"
    return f"{hours / 24:.1f} days"


def plural(count: int, singular: str, suffix: str = "s") -> str:
    """``3 items`` / ``1 item``."""
    return f"{count:,} {singular}{'' if count == 1 else suffix}"


@dataclass
class InventoryReport:
    """Aggregated result of a dry run."""

    library: str
    cutoff_date: date
    scanned: int = 0
    counts: Counter[Disposition] = field(default_factory=Counter)
    bytes_by_disposition: Counter[Disposition] = field(default_factory=Counter)

    included_photos: int = 0
    included_movies: int = 0
    included_live_photos: int = 0

    cloud_only_count: int = 0
    cloud_only_bytes: int = 0

    subject_counts: Counter[str] = field(default_factory=Counter)

    untagged_window_count: int = 0
    untagged_window_bytes: int = 0
    untagged_window_end: date | None = None

    unknown_date_files: list[str] = field(default_factory=list)

    earliest_included: datetime | None = None
    latest_included: datetime | None = None

    @property
    def included_count(self) -> int:
        return sum(count for d, count in self.counts.items() if d.is_include)

    @property
    def included_bytes(self) -> int:
        return sum(size for d, size in self.bytes_by_disposition.items() if d.is_include)

    @property
    def excluded_count(self) -> int:
        return sum(
            count for d, count in self.counts.items() if not d.is_include and not d.needs_review
        )

    @property
    def estimated_download_hours(self) -> float:
        """Rough wait for cloud-only originals, at ASSUMED_DOWNLOAD_MBPS."""
        if not self.cloud_only_bytes:
            return 0.0
        bits = self.cloud_only_bytes * 8
        return bits / (ASSUMED_DOWNLOAD_MBPS * 1_000_000) / 3600


def build_inventory(
    library: PhotoLibrary,
    config: ContributeConfig,
    family: FamilyConfig,
) -> tuple[InventoryReport, list[Decision]]:
    """Apply the contract across a library and aggregate the rulings.

    Returns both the report and the full decision list, since ``export`` needs
    the per-item rulings and re-scanning a large library is slow.
    """
    report = InventoryReport(
        library=library.description,
        cutoff_date=config.cutoff_date,
        untagged_window_end=add_months(config.cutoff_date, config.untagged_report_months),
    )
    decisions: list[Decision] = []

    for item in library.items():
        decision = decide(item, config, family)
        decisions.append(decision)
        _accumulate(report, decision)

    return report, decisions


def _accumulate(report: InventoryReport, decision: Decision) -> None:
    item = decision.item
    report.scanned += 1
    report.counts[decision.disposition] += 1
    report.bytes_by_disposition[decision.disposition] += item.filesize

    if decision.disposition is Disposition.REVIEW_UNKNOWN_DATE:
        report.unknown_date_files.append(item.original_filename or item.uuid)
        return

    if decision.disposition is Disposition.EXCLUDE_UNTAGGED and decision.in_untagged_window:
        report.untagged_window_count += 1
        report.untagged_window_bytes += item.filesize
        return

    if not decision.is_include:
        return

    if item.is_movie:
        report.included_movies += 1
    else:
        report.included_photos += 1
    if item.is_live_photo:
        report.included_live_photos += 1

    # Cloud-only matters only for items we intend to copy — everything else
    # never needs to leave Apple's servers.
    if item.is_missing:
        report.cloud_only_count += 1
        report.cloud_only_bytes += item.filesize

    for name in decision.matched_subjects:
        report.subject_counts[name] += 1

    if item.date:
        if report.earliest_included is None or item.date < report.earliest_included:
            report.earliest_included = item.date
        if report.latest_included is None or item.date > report.latest_included:
            report.latest_included = item.date


def render_report(report: InventoryReport, family: FamilyConfig) -> str:
    """Format the report for a terminal, and for pasting into a message."""
    lines: list[str] = []
    add = lines.append

    add("=" * 68)
    add("CONTRIBUTION INVENTORY — dry run, nothing was copied or changed")
    add("=" * 68)
    add(f"Library:      {report.library}")
    add(f"Cutoff date:  {report.cutoff_date.isoformat()}")
    add(f"Items scanned: {report.scanned:,}")
    add("")

    add("-" * 68)
    add("WOULD BE COPIED")
    add("-" * 68)
    add(f"  {plural(report.included_count, 'item')}, {format_size(report.included_bytes)}")
    for disposition in (Disposition.INCLUDE_PRE_CUTOFF, Disposition.INCLUDE_TAGGED):
        count = report.counts.get(disposition, 0)
        size = report.bytes_by_disposition.get(disposition, 0)
        add(f"    {count:>8,}  {format_size(size):>12}   {disposition.value}")
    add("")
    add(
        f"  {plural(report.included_photos, 'photo')}, "
        f"{plural(report.included_movies, 'video')}, "
        f"{plural(report.included_live_photos, 'live photo')}"
    )
    if report.earliest_included and report.latest_included:
        add(
            f"  Date range: {report.earliest_included:%Y-%m-%d} "
            f"to {report.latest_included:%Y-%m-%d}"
        )
    add("")

    if report.subject_counts or family.subjects:
        add("  Tagged items by person (post-cutoff rule):")
        for subject in family.subjects:
            add(f"    {subject.name:<24} {report.subject_counts.get(subject.name, 0):>8,}")
        add("")

    add("-" * 68)
    add("DRIVE AND DOWNLOAD")
    add("-" * 68)
    add(f"  Export size:        {format_size(report.included_bytes)}")
    add(f"  Buy at least:       {format_size(int(report.included_bytes * 1.2))} of usable space")
    add("                      (20% headroom; format the drive exFAT)")
    if report.cloud_only_count:
        add("")
        add(
            f"  Cloud-only:         {plural(report.cloud_only_count, 'item')}, "
            f"{format_size(report.cloud_only_bytes)}"
        )
        add(
            f"  Must download first, roughly "
            f"{format_duration(report.estimated_download_hours)} "
            f"at {ASSUMED_DOWNLOAD_MBPS:.0f} Mbps."
        )
        add("  This is the long pole. Start it well before the handoff session,")
        add("  keep the Mac awake and plugged in, and expect it to run overnight.")
    else:
        add("  All originals are already on this Mac — no download wait.")
    add("")

    add("-" * 68)
    add("WOULD NOT BE COPIED")
    add("-" * 68)
    add(f"  {plural(report.excluded_count, 'item')}")
    for disposition in (
        Disposition.EXCLUDE_SCREENSHOT,
        Disposition.EXCLUDE_UNTAGGED,
        Disposition.EXCLUDE_ALBUM,
        Disposition.EXCLUDE_NOT_OWNED,
    ):
        count = report.counts.get(disposition, 0)
        if count:
            add(f"    {count:>8,}  {disposition.value}")
    add("")

    add("-" * 68)
    add("NEEDS A HUMAN")
    add("-" * 68)
    unknown = report.counts.get(Disposition.REVIEW_UNKNOWN_DATE, 0)
    if unknown:
        add(
            f"  {plural(unknown, 'item')} "
            f"{'has' if unknown == 1 else 'have'} no reliable date, "
            f"so the cutoff can't rule on {'it' if unknown == 1 else 'them'}."
        )
        add("  They are neither included nor excluded until someone decides.")
        for name in report.unknown_date_files[:UNKNOWN_DATE_SAMPLE]:
            add(f"    {name}")
        if unknown > UNKNOWN_DATE_SAMPLE:
            add(
                f"    ... and {unknown - UNKNOWN_DATE_SAMPLE:,} more "
                f"(full list in the JSON report)"
            )
    else:
        add("  No unknown-date items.")
    add("")

    if report.untagged_window_count:
        window_end = report.untagged_window_end
        add(
            f"  {plural(report.untagged_window_count, 'item')} "
            f"({format_size(report.untagged_window_bytes)}) fall between the cutoff and "
            f"{window_end.isoformat() if window_end else '?'} with no subject tagged."
        )
        add("  A big number here usually means face tagging lapsed for a while,")
        add("  not that these photos don't matter. It is information for")
        add("  renegotiating the cutoff — not a reason to override the contract.")
        add("")

    add("=" * 68)
    add("Nothing has been copied, deleted, or modified. This was a scan.")
    add("=" * 68)
    return "\n".join(lines)


def report_to_dict(report: InventoryReport) -> dict[str, Any]:
    """JSON-serializable form, with the full unknown-date list."""
    return {
        "library": report.library,
        "cutoff_date": report.cutoff_date.isoformat(),
        "scanned": report.scanned,
        "included": {
            "count": report.included_count,
            "bytes": report.included_bytes,
            "photos": report.included_photos,
            "movies": report.included_movies,
            "live_photos": report.included_live_photos,
            "earliest": report.earliest_included.isoformat() if report.earliest_included else None,
            "latest": report.latest_included.isoformat() if report.latest_included else None,
        },
        "by_disposition": {
            d.name: {
                "count": report.counts.get(d, 0),
                "bytes": report.bytes_by_disposition.get(d, 0),
            }
            for d in Disposition
        },
        "subject_counts": dict(report.subject_counts),
        "cloud_only": {
            "count": report.cloud_only_count,
            "bytes": report.cloud_only_bytes,
            "estimated_hours": round(report.estimated_download_hours, 2),
            "assumed_mbps": ASSUMED_DOWNLOAD_MBPS,
        },
        "untagged_window": {
            "count": report.untagged_window_count,
            "bytes": report.untagged_window_bytes,
            "window_end": report.untagged_window_end.isoformat()
            if report.untagged_window_end
            else None,
        },
        "unknown_date_files": report.unknown_date_files,
    }


def write_json_report(report: InventoryReport, path: Any) -> None:
    from pathlib import Path

    Path(path).write_text(
        json.dumps(report_to_dict(report), indent=2, ensure_ascii=False), encoding="utf-8"
    )
