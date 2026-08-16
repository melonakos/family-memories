"""Shared fixtures.

Puts the repo root on sys.path so the tests import the packages in place,
without requiring an editable install first.
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from settings import ContributeConfig, FamilyConfig, Subject  # noqa: E402

CUTOFF = date(2015, 1, 1)


@pytest.fixture
def family() -> FamilyConfig:
    """A roster with a nickname and a name that needs normalizing."""
    return FamilyConfig(
        subjects=(
            Subject(name="Subject One", tags=("Subject 1", "Sub One"), quota=3),
            Subject(name="Subject Two", quota=3),
            Subject(name="Subject Three", tags=("Trey",), quota=2),
        )
    )


@pytest.fixture
def contribute_config() -> ContributeConfig:
    return ContributeConfig(cutoff_date=CUTOFF, untagged_report_months=18)


def at(year: int, month: int = 6, day: int = 15) -> datetime:
    return datetime(year, month, day, 12, 0, 0)
