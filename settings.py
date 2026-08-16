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


@dataclass(frozen=True)
class Config:
    family: FamilyConfig
    contribute: ContributeConfig
    path: Path
    raw: dict[str, Any]
    """Unparsed sections (vault, index, ingest, wall, ...).

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
        path=resolved,
        raw=raw,
    )
