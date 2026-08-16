"""Shared fixtures.

Puts the repo root on sys.path so the tests import the packages in place,
without requiring an editable install first.
"""

from __future__ import annotations

import os
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from settings import ContributeConfig, FamilyConfig, Subject  # noqa: E402

CUTOFF = date(2015, 1, 1)

HAS_EXIFTOOL = shutil.which("exiftool") is not None

# exiftool is a required dependency, so most of the pipeline suite is useless
# without it. Locally it skips with a clear reason; in CI, REQUIRE_EXIFTOOL=1
# turns a missing binary into a failure. A silently skipped suite that reports
# green is how a real defect hid here once already.
REQUIRE_EXIFTOOL = os.environ.get("REQUIRE_EXIFTOOL") == "1"

requires_exiftool = pytest.mark.skipif(
    not HAS_EXIFTOOL and not REQUIRE_EXIFTOOL,
    reason="exiftool not installed (set REQUIRE_EXIFTOOL=1 to make this fatal)",
)


def test_exiftool_is_available_when_required() -> None:
    """Fails in CI if exiftool went missing, instead of skipping the suite."""
    if REQUIRE_EXIFTOOL:
        assert HAS_EXIFTOOL, (
            "REQUIRE_EXIFTOOL=1 but exiftool is not on PATH. The pipeline tests "
            "would have skipped silently and reported green."
        )


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
