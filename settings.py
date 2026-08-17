"""Load ``config.toml`` into typed objects.

Read once at startup, passed down explicitly. No module-level state, no global
config object — see CLAUDE.md. Named ``settings`` rather than ``config`` so the
module doesn't collide with the many other things called ``config`` on a Python
path, and so ``import config`` never ambiguously means the TOML file.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_NAME = "config.toml"
EXAMPLE_CONFIG_NAME = "config.example.toml"


class ConfigError(Exception):
    """Raised when config.toml is missing, malformed, or missing a required value.

    Always fatal. A half-understood config is how a tool ends up copying the
    wrong decade of someone's photo library.
    """


def normalize_tag(value: str) -> str:
    """Casefold and collapse whitespace, for comparing person tags.

    Face tags are typed by hand over many years and drift: trailing spaces,
    double spaces, inconsistent capitalization. Matching on the raw string
    silently drops photos.
    """
    return " ".join(str(value).split()).casefold()


@dataclass(frozen=True)
class Subject:
    """A person the archive is built around — usually a child."""

    name: str
    tags: tuple[str, ...] = ()
    quota: int | None = None

    @property
    def match_tags(self) -> frozenset[str]:
        """Every normalized spelling that counts as this person.

        The subject's own name always matches, whether or not it was repeated
        in the ``tags`` list.
        """
        return frozenset(normalize_tag(t) for t in (self.name, *self.tags))


@dataclass(frozen=True)
class Adult:
    """An adult who appears in the collection.

    ``requires_subject`` drives the wall-selection eligibility rule: when true,
    photos of this person only qualify if a subject is also in the shot.
    """

    name: str
    requires_subject: bool = True


@dataclass(frozen=True)
class FamilyConfig:
    subjects: tuple[Subject, ...]
    adults: tuple[Adult, ...] = ()

    @property
    def tag_index(self) -> dict[str, Subject]:
        """Normalized tag -> subject. Built once per call site, not cached globally."""
        index: dict[str, Subject] = {}
        for subject in self.subjects:
            for tag in subject.match_tags:
                index[tag] = subject
        return index

    def subjects_in(self, persons: tuple[str, ...]) -> tuple[str, ...]:
        """Which subjects appear among these person tags, by subject name.

        Deduplicated and returned in roster order so reports are stable.
        """
        index = self.tag_index
        found = {
            index[normalize_tag(p)].name for p in persons if normalize_tag(p) in index
        }
        return tuple(s.name for s in self.subjects if s.name in found)


@dataclass(frozen=True)
class ContributeConfig:
    """The copy contract. See docs/design.md, Project 1."""

    cutoff_date: date
    destination: Path | None = None
    exclude_screenshots_after_cutoff: bool = True
    exclude_albums: tuple[str, ...] = ("Hidden", "Recently Deleted")
    exclude_not_owned: bool = True
    untagged_report_months: int = 18

    @property
    def excluded_album_set(self) -> frozenset[str]:
        return frozenset(normalize_tag(a) for a in self.exclude_albums)


SUPPORTED_VAULT_LAYOUTS = ("YYYY/MM",)


@dataclass(frozen=True)
class IndexConfig:
    """The SQLite index. Defaults to a file beside config.toml."""

    path: Path = Path("index.db")


@dataclass(frozen=True)
class VaultConfig:
    """The canonical archive.

    ``path`` is optional at load time because the drives may not exist yet —
    the dry run reports the size that determines what to buy. Commands that
    need it fail with a clear message rather than inventing a location.
    """

    path: Path | None = None
    mirror_path: Path | None = None
    layout: str = "YYYY/MM"
    verify_interval_days: int = 90


@dataclass(frozen=True)
class IngestConfig:
    inbox: Path | None = None
    phash_threshold: int = 8
    """Perceptual-hash distance below which two images are near-duplicates.

    Only ever used to *propose* a match. Acting on one requires the candidate
    to be an unambiguous lower-resolution twin; anything else goes to review.
    """


@dataclass(frozen=True)
class EnrichConfig:
    location_window_hours: float = 6.0
    """How far in time a location inference may reach.

    Only ever narrows what gets inferred. Inference additionally never crosses
    midnight and never chains off another inference.
    """


@dataclass(frozen=True)
class Config:
    family: FamilyConfig
    contribute: ContributeConfig
    index: IndexConfig
    vault: VaultConfig
    ingest: IngestConfig
    enrich: EnrichConfig
    path: Path
    raw: dict[str, Any]
    """Unparsed sections (enrich, living_library, heritage, wall, ...).

    Typed accessors get added as each module is built, rather than writing
    speculative parsing for stages that don't exist yet.
    """


def _as_date(value: Any, field: str) -> date:
    """Accept a bare TOML date or an ISO string.

    TOML has a native date type, so ``cutoff_date = 2015-01-01`` arrives as a
    ``date`` while ``"2015-01-01"`` arrives as a string. Both are reasonable
    things to write, so both are accepted.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError as exc:
            raise ConfigError(
                f"{field}: {value!r} is not a valid date. Use YYYY-MM-DD."
            ) from exc
    raise ConfigError(f"{field}: expected a date, got {type(value).__name__}.")


def _parse_family(raw: dict[str, Any]) -> FamilyConfig:
    section = raw.get("family")
    if not isinstance(section, dict):
        raise ConfigError("config.toml is missing a [family] section.")

    raw_subjects = section.get("subjects") or []
    if not raw_subjects:
        raise ConfigError(
            "[family] defines no subjects. List at least one person the archive "
            "is being built around."
        )

    subjects: list[Subject] = []
    seen: set[str] = set()
    for entry in raw_subjects:
        if not isinstance(entry, dict) or not str(entry.get("name", "")).strip():
            raise ConfigError(f"[family] subject entry has no name: {entry!r}")
        name = str(entry["name"]).strip()
        if normalize_tag(name) in seen:
            raise ConfigError(f"[family] lists {name!r} twice.")
        seen.add(normalize_tag(name))

        quota = entry.get("quota")
        if quota is not None and (not isinstance(quota, int) or quota < 0):
            raise ConfigError(f"[family] {name}: quota must be a non-negative integer.")

        subjects.append(
            Subject(
                name=name,
                tags=tuple(str(t).strip() for t in entry.get("tags", []) if str(t).strip()),
                quota=quota,
            )
        )

    # A tag spelling that maps to two different people makes every downstream
    # count wrong, and the failure is silent. Catch it at load.
    owners: dict[str, str] = {}
    for subject in subjects:
        for tag in subject.match_tags:
            if tag in owners and owners[tag] != subject.name:
                raise ConfigError(
                    f"[family] tag {tag!r} is claimed by both {owners[tag]!r} and "
                    f"{subject.name!r}. Person tags must be unambiguous."
                )
            owners[tag] = subject.name

    adults = tuple(
        Adult(
            name=str(entry["name"]).strip(),
            requires_subject=bool(entry.get("requires_subject", True)),
        )
        for entry in section.get("adults", [])
        if isinstance(entry, dict) and str(entry.get("name", "")).strip()
    )
    return FamilyConfig(subjects=tuple(subjects), adults=adults)


def _parse_contribute(raw: dict[str, Any]) -> ContributeConfig:
    section = raw.get("contribute")
    if not isinstance(section, dict):
        raise ConfigError("config.toml is missing a [contribute] section.")
    if "cutoff_date" not in section:
        raise ConfigError(
            "[contribute] has no cutoff_date. This is the heart of the copy "
            "contract and has no safe default."
        )

    destination = section.get("destination")
    months = section.get("untagged_report_months", 18)
    if not isinstance(months, int) or months < 0:
        raise ConfigError("[contribute] untagged_report_months must be a non-negative integer.")

    return ContributeConfig(
        cutoff_date=_as_date(section["cutoff_date"], "[contribute] cutoff_date"),
        destination=Path(str(destination)).expanduser() if destination else None,
        exclude_screenshots_after_cutoff=bool(
            section.get("exclude_screenshots_after_cutoff", True)
        ),
        exclude_albums=tuple(
            str(a) for a in section.get("exclude_albums", ["Hidden", "Recently Deleted"])
        ),
        exclude_not_owned=bool(section.get("exclude_not_owned", True)),
        untagged_report_months=months,
    )


def _optional_path(section: dict[str, Any], key: str) -> Path | None:
    value = section.get(key)
    return Path(str(value)).expanduser() if value else None


def _parse_index(raw: dict[str, Any], config_dir: Path) -> IndexConfig:
    section = raw.get("index") or {}
    path = _optional_path(section, "path") or Path("index.db")
    # Resolve relative to config.toml, not the current directory, so the same
    # index is used no matter where a command is run from.
    return IndexConfig(path=path if path.is_absolute() else (config_dir / path))


def _parse_vault(raw: dict[str, Any]) -> VaultConfig:
    section = raw.get("vault") or {}

    layout = str(section.get("layout", "YYYY/MM"))
    if layout not in SUPPORTED_VAULT_LAYOUTS:
        raise ConfigError(
            f"[vault] layout {layout!r} is not supported. "
            f"Supported: {', '.join(SUPPORTED_VAULT_LAYOUTS)}."
        )

    days = section.get("verify_interval_days", 90)
    if not isinstance(days, int) or days < 0:
        raise ConfigError("[vault] verify_interval_days must be a non-negative integer.")

    path = _optional_path(section, "path")
    mirror = _optional_path(section, "mirror_path")
    if path and mirror and path.resolve() == mirror.resolve():
        raise ConfigError(
            "[vault] path and mirror_path are the same directory. A mirror on the "
            "same media is not a second copy."
        )

    return VaultConfig(path=path, mirror_path=mirror, layout=layout, verify_interval_days=days)


def _parse_ingest(raw: dict[str, Any]) -> IngestConfig:
    section = raw.get("ingest") or {}

    threshold = section.get("phash_threshold", 8)
    if not isinstance(threshold, int) or not 0 <= threshold <= 64:
        raise ConfigError(
            "[ingest] phash_threshold must be an integer between 0 and 64 "
            "(it is a Hamming distance over a 64-bit hash)."
        )

    # Refuse rather than ignore. Honouring this would violate ground rule 4,
    # and a safety flag that is silently dropped is worse than no flag at all.
    if section.get("guess_missing_dates"):
        raise ConfigError(
            "[ingest] guess_missing_dates = true is not supported. Dates are never "
            "inferred from filenames or file timestamps; undated items go to the "
            "review queue for a human to decide."
        )

    return IngestConfig(inbox=_optional_path(section, "inbox"), phash_threshold=threshold)


def _parse_enrich(raw: dict[str, Any]) -> EnrichConfig:
    section = raw.get("enrich") or {}

    # Reject rather than ignore, for the same reason as guess_missing_dates:
    # a setting that appears to do something and doesn't is worse than absent.
    for retired in ("music_recognition", "infer_locations"):
        if retired in section:
            raise ConfigError(
                f"[enrich] {retired} is not supported. Location inference is an "
                f"explicit command (`enrich locations`) and music identification "
                f"is not implemented. Remove the setting so this file doesn't "
                f"describe behaviour the tools don't have."
            )

    hours = section.get("location_window_hours", 6.0)
    if not isinstance(hours, (int, float)) or isinstance(hours, bool) or hours <= 0:
        raise ConfigError("[enrich] location_window_hours must be a positive number.")
    return EnrichConfig(location_window_hours=float(hours))


def find_config(start: Path | None = None) -> Path:
    """Locate config.toml, walking up from ``start`` to the filesystem root."""
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        candidate = directory / DEFAULT_CONFIG_NAME
        if candidate.is_file():
            return candidate
    raise ConfigError(
        f"No {DEFAULT_CONFIG_NAME} found in {current} or any parent directory. "
        f"Copy {EXAMPLE_CONFIG_NAME} to {DEFAULT_CONFIG_NAME} and edit it."
    )


def load_config(path: Path | None = None) -> Config:
    """Read and validate config.toml.

    Raises ConfigError on anything malformed. There is no partial-success path:
    these tools act on irreplaceable files, so an ambiguous config stops the run.
    """
    resolved = Path(path).expanduser() if path else find_config()
    if not resolved.is_file():
        raise ConfigError(f"Config file not found: {resolved}")

    try:
        raw = tomllib.loads(resolved.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{resolved} is not valid TOML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read {resolved}: {exc}") from exc

    return Config(
        family=_parse_family(raw),
        contribute=_parse_contribute(raw),
        index=_parse_index(raw, resolved.parent),
        vault=_parse_vault(raw),
        ingest=_parse_ingest(raw),
        enrich=_parse_enrich(raw),
        path=resolved,
        raw=raw,
    )
