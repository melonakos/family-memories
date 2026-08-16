"""Establishing when a file was taken, and how big it is.

Dates are the one thing this pipeline refuses to be clever about. There is no
inference from filenames, no falling back to filesystem timestamps, no "close
enough". A file whose date cannot be read from its own metadata or a sidecar is
undated, and it goes to a human. That is ground rule 4, and it is why an archive
built this way can be trusted decades later: a date in the index came from
somewhere real, and ``taken_at_source`` says where.

Extraction runs through exiftool, which is a hard dependency. It reads EXIF,
QuickTime, and everything else the archive will encounter, and it is the same
tool osxphotos leans on. Calls are batched — exiftool's startup cost dominates
per-file usage, and a library-scale run would otherwise spend most of its time
launching processes.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mediafiles import read_sidecar_metadata

EXIFTOOL = "exiftool"

# Ordered by trustworthiness. DateTimeOriginal is when the shutter fired;
# CreateDate can be a re-encode; MediaCreateDate is the video equivalent.
DATE_TAGS = ("DateTimeOriginal", "CreateDate", "MediaCreateDate")

EXIFTOOL_ARGS = (
    "-json",
    "-charset",
    "filename=utf8",
    "-DateTimeOriginal",
    "-CreateDate",
    "-MediaCreateDate",
    "-ImageWidth",
    "-ImageHeight",
    "-MIMEType",
)

# How many files to hand exiftool at once. Large enough that startup cost is
# amortized, small enough to keep memory and argument files reasonable.
BATCH_SIZE = 200

SOURCE_SIDECAR = "sidecar"
SOURCE_EXIF = "exif"
SOURCE_UNKNOWN = "unknown"

# exiftool renders dates as "2015:06:15 12:00:00", optionally with subseconds
# and a UTC offset.
_EXIF_DATE = re.compile(
    r"^(?P<y>\d{4})[:\-](?P<m>\d{2})[:\-](?P<d>\d{2})[ T]"
    r"(?P<H>\d{2}):(?P<M>\d{2}):(?P<S>\d{2})"
)


class ExiftoolError(Exception):
    """Raised when exiftool is missing or cannot be run."""


@dataclass(frozen=True)
class FileMetadata:
    taken_at: datetime | None = None
    taken_at_source: str = SOURCE_UNKNOWN
    width: int | None = None
    height: int | None = None

    @property
    def has_date(self) -> bool:
        return self.taken_at is not None


def require_exiftool() -> str:
    """Return the exiftool executable, or explain how to install it.

    Checked before any work begins rather than at the first file, so a missing
    dependency costs a second instead of an aborted half-run.
    """
    found = shutil.which(EXIFTOOL)
    if found:
        return found
    raise ExiftoolError(
        "exiftool is required for ingest and was not found on PATH.\n"
        "  macOS:    brew install exiftool\n"
        "  Windows:  winget install OliverBetz.ExifTool\n"
        "  Debian:   sudo apt install libimage-exiftool-perl\n"
        "It reads the dates and dimensions this pipeline refuses to guess at."
    )


def exiftool_version() -> str:
    require_exiftool()
    try:
        result = subprocess.run(
            [EXIFTOOL, "-ver"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExiftoolError(f"Could not run exiftool: {exc}") from exc
    return result.stdout.strip()


def parse_exif_datetime(value: object) -> datetime | None:
    """Parse an exiftool timestamp, rejecting its placeholder zeros.

    exiftool reports an unset date as ``0000:00:00 00:00:00``. Passing that
    through would file a photo under year zero; treating it as unknown sends it
    to review instead.
    """
    if not value:
        return None
    match = _EXIF_DATE.match(str(value).strip())
    if not match:
        return None
    parts = {k: int(v) for k, v in match.groupdict().items()}
    if parts["y"] == 0 or parts["m"] == 0 or parts["d"] == 0:
        return None
    try:
        return datetime(
            parts["y"], parts["m"], parts["d"], parts["H"], parts["M"], parts["S"]
        )
    except ValueError:
        return None


def parse_sidecar_datetime(value: str) -> datetime | None:
    """Parse a sidecar date, which osxphotos writes as ISO 8601."""
    if not value:
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return parse_exif_datetime(text)
    # Normalize to naive local time: the archive stores wall-clock capture time,
    # and mixing aware and naive values makes every later comparison a landmine.
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def read_exiftool(paths: list[Path]) -> dict[str, dict]:
    """Read metadata for many files at once, keyed by absolute path string.

    Paths go through an argument file rather than the command line: a batch of
    a few hundred names would otherwise blow past the Windows command length
    limit, and argument files sidestep quoting problems entirely.
    """
    if not paths:
        return {}
    require_exiftool()

    results: dict[str, dict] = {}
    for start in range(0, len(paths), BATCH_SIZE):
        batch = paths[start : start + BATCH_SIZE]
        with tempfile.NamedTemporaryFile(
            "w", suffix=".args", delete=False, encoding="utf-8"
        ) as handle:
            handle.write("\n".join(str(Path(p).resolve()) for p in batch))
            argfile = handle.name
        try:
            proc = subprocess.run(
                [EXIFTOOL, *EXIFTOOL_ARGS, "-@", argfile],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if proc.stdout.strip():
                try:
                    for entry in json.loads(proc.stdout):
                        source = entry.get("SourceFile")
                        if source:
                            results[str(Path(source).resolve())] = entry
                except json.JSONDecodeError as exc:
                    raise ExiftoolError(f"Could not parse exiftool output: {exc}") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise ExiftoolError(f"exiftool failed: {exc}") from exc
        finally:
            Path(argfile).unlink(missing_ok=True)

    return results


def _as_int(value: object) -> int | None:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def metadata_for(path: Path, exif: dict | None) -> FileMetadata:
    """Combine sidecar and embedded metadata into one answer.

    The sidecar wins when present. It carries the date from the photo library
    the file was exported from, which reflects any correction a human made
    there — a more considered answer than the camera's original guess.
    """
    exif = exif or {}
    width = _as_int(exif.get("ImageWidth"))
    height = _as_int(exif.get("ImageHeight"))

    sidecar_date = parse_sidecar_datetime(read_sidecar_metadata(path).get("date", ""))
    if sidecar_date:
        return FileMetadata(sidecar_date, SOURCE_SIDECAR, width, height)

    for tag in DATE_TAGS:
        taken = parse_exif_datetime(exif.get(tag))
        if taken:
            return FileMetadata(taken, SOURCE_EXIF, width, height)

    return FileMetadata(None, SOURCE_UNKNOWN, width, height)
