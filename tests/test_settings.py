"""Tests for config loading.

A misread config is how the wrong decade of someone's library gets copied, so
every failure mode here is fatal and loud rather than defaulted.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from settings import (
    Adult,
    ConfigError,
    FamilyConfig,
    Subject,
    find_config,
    load_config,
    normalize_tag,
)

MINIMAL = """
[family]
subjects = [{ name = "Subject One" }]

[contribute]
cutoff_date = "2015-01-01"
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


class TestNormalizeTag:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Subject One", "subject one"),
            ("  Subject   One  ", "subject one"),
            ("SUBJECT ONE", "subject one"),
            ("Subject\tOne", "subject one"),
        ],
    )
    def test_normalizes(self, raw, expected):
        assert normalize_tag(raw) == expected


class TestSubject:
    def test_own_name_always_matches(self):
        assert "subject one" in Subject(name="Subject One").match_tags

    def test_aliases_match(self):
        subject = Subject(name="Subject One", tags=("Sub One", "S1"))
        assert subject.match_tags == {"subject one", "sub one", "s1"}


class TestFamilyConfig:
    def test_subjects_in_returns_roster_order(self):
        family = FamilyConfig(
            subjects=(Subject(name="A"), Subject(name="B"), Subject(name="C"))
        )
        assert family.subjects_in(("C", "A")) == ("A", "C")

    def test_subjects_in_deduplicates(self):
        """A photo tagged with both a name and its alias counts the person once."""
        family = FamilyConfig(subjects=(Subject(name="A", tags=("Al",)),))
        assert family.subjects_in(("A", "Al")) == ("A",)

    def test_no_match_is_empty(self):
        family = FamilyConfig(subjects=(Subject(name="A"),))
        assert family.subjects_in(("Someone",)) == ()


class TestLoadConfig:
    def test_minimal(self, tmp_path):
        config = load_config(write(tmp_path, MINIMAL))
        assert config.contribute.cutoff_date == date(2015, 1, 1)
        assert config.family.subjects[0].name == "Subject One"

    def test_bare_toml_date_is_accepted(self, tmp_path):
        text = MINIMAL.replace('"2015-01-01"', "2015-01-01")
        assert load_config(write(tmp_path, text)).contribute.cutoff_date == date(2015, 1, 1)

    def test_defaults_are_the_safe_reading(self, tmp_path):
        contribute = load_config(write(tmp_path, MINIMAL)).contribute
        assert contribute.exclude_screenshots_after_cutoff is True
        assert contribute.exclude_not_owned is True
        assert "hidden" in contribute.excluded_album_set

    def test_adults_default_to_requiring_a_subject(self, tmp_path):
        text = MINIMAL + '\nadults = [{ name = "An Adult" }]\n'
        # adults belongs under [family]; append before [contribute] instead
        text = MINIMAL.replace(
            'subjects = [{ name = "Subject One" }]',
            'subjects = [{ name = "Subject One" }]\nadults = [{ name = "An Adult" }]',
        )
        config = load_config(write(tmp_path, text))
        assert config.family.adults == (Adult(name="An Adult", requires_subject=True),)

    def test_raw_keeps_unparsed_sections(self, tmp_path):
        text = MINIMAL + '\n[vault]\npath = "/tmp/vault"\n'
        assert load_config(write(tmp_path, text)).raw["vault"]["path"] == "/tmp/vault"

    def test_destination_expands_user(self, tmp_path):
        text = MINIMAL + '\ndestination = "~/drive"\n'
        config = load_config(write(tmp_path, text))
        assert "~" not in str(config.contribute.destination)


class TestConfigErrors:
    def test_missing_file(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "nope.toml")

    def test_invalid_toml(self, tmp_path):
        with pytest.raises(ConfigError, match="not valid TOML"):
            load_config(write(tmp_path, "this is not = = toml"))

    def test_missing_family_section(self, tmp_path):
        with pytest.raises(ConfigError, match=r"\[family\]"):
            load_config(write(tmp_path, '[contribute]\ncutoff_date = "2015-01-01"\n'))

    def test_no_subjects(self, tmp_path):
        text = '[family]\nsubjects = []\n[contribute]\ncutoff_date = "2015-01-01"\n'
        with pytest.raises(ConfigError, match="no subjects"):
            load_config(write(tmp_path, text))

    def test_missing_contribute_section(self, tmp_path):
        with pytest.raises(ConfigError, match=r"\[contribute\]"):
            load_config(write(tmp_path, '[family]\nsubjects = [{ name = "A" }]\n'))

    def test_missing_cutoff_date_is_fatal(self, tmp_path):
        """There is no safe default for the heart of the copy contract."""
        text = '[family]\nsubjects = [{ name = "A" }]\n[contribute]\ndestination = "/d"\n'
        with pytest.raises(ConfigError, match="no cutoff_date"):
            load_config(write(tmp_path, text))

    def test_unparseable_date(self, tmp_path):
        with pytest.raises(ConfigError, match="not a valid date"):
            load_config(write(tmp_path, MINIMAL.replace("2015-01-01", "last January")))

    def test_duplicate_subject(self, tmp_path):
        text = MINIMAL.replace(
            'subjects = [{ name = "Subject One" }]',
            'subjects = [{ name = "Subject One" }, { name = "subject one" }]',
        )
        with pytest.raises(ConfigError, match="twice"):
            load_config(write(tmp_path, text))

    def test_ambiguous_tag_between_two_people(self, tmp_path):
        """A tag claimed by two people makes every downstream count wrong, and
        does it silently. Caught at load instead."""
        text = MINIMAL.replace(
            'subjects = [{ name = "Subject One" }]',
            'subjects = [{ name = "A", tags = ["Sam"] }, { name = "B", tags = ["sam"] }]',
        )
        with pytest.raises(ConfigError, match="unambiguous"):
            load_config(write(tmp_path, text))

    def test_negative_quota(self, tmp_path):
        text = MINIMAL.replace('{ name = "Subject One" }', '{ name = "A", quota = -1 }')
        with pytest.raises(ConfigError, match="quota"):
            load_config(write(tmp_path, text))

    def test_subject_without_a_name(self, tmp_path):
        text = MINIMAL.replace('{ name = "Subject One" }', '{ tags = ["x"] }')
        with pytest.raises(ConfigError, match="no name"):
            load_config(write(tmp_path, text))


class TestFindConfig:
    def test_finds_in_parent_directory(self, tmp_path):
        write(tmp_path, MINIMAL)
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        assert find_config(nested) == tmp_path / "config.toml"

    def test_error_names_the_example_file(self, tmp_path):
        with pytest.raises(ConfigError, match="config.example.toml"):
            find_config(tmp_path)


class TestShippedExample:
    """config.example.toml must stay loadable — it is the starting point for
    every new user, and a broken example is a broken first impression."""

    def test_example_config_loads(self, tmp_path):
        source = Path(__file__).resolve().parent.parent / "config.example.toml"
        config = load_config(write(tmp_path, source.read_text(encoding="utf-8")))
        assert len(config.family.subjects) >= 1
        assert config.contribute.cutoff_date

    def test_example_contains_no_real_names(self):
        """Guardrail 1 in CLAUDE.md, enforced rather than trusted."""
        source = Path(__file__).resolve().parent.parent / "config.example.toml"
        text = source.read_text(encoding="utf-8").casefold()
        assert "child one" in text
