"""Demo-mode fixture dispatch table (KCH-69 / AA-32).

`DEMO_FIXTURES` claims each listed fixture has an adapter that can parse it —
this proves that claim against the real bytes in `data/samples/`, the same
way `tests/unit/test_adapters.py`/`test_scotiabank_pdf_adapter.py` already
prove it per-adapter. Also locks the two-fixture skip list and the fixed
"Alex Mock" profile facts against silent drift.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.demo import (
    ALEX_MOCK_PROFILE,
    DEMO_FIXTURES,
    DEMO_SNAPSHOT_DATE,
    FIXTURES_SKIPPED,
)
from worker.adapters.base import StagedRowDraft

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "data" / "samples"


def _all_sample_filenames() -> set[str]:
    return {p.name for p in SAMPLES_DIR.glob("*") if p.suffix in {".csv", ".json", ".pdf"}}


def test_every_demo_fixture_file_exists() -> None:
    for fixture in DEMO_FIXTURES:
        assert (SAMPLES_DIR / fixture.filename).exists(), fixture.filename


@pytest.mark.parametrize("fixture", DEMO_FIXTURES, ids=lambda f: f.filename)
def test_each_demo_fixture_detects_and_parses_with_its_own_adapter(fixture) -> None:
    raw = (SAMPLES_DIR / fixture.filename).read_bytes()

    assert fixture.adapter.detect(raw) is True

    drafts = fixture.adapter.parse(raw)
    assert drafts, f"{fixture.filename} parsed to zero drafts"
    assert all(isinstance(draft, StagedRowDraft) for draft in drafts)


def test_demo_fixtures_and_skip_list_cover_every_sample_file() -> None:
    covered = {fixture.filename for fixture in DEMO_FIXTURES} | set(FIXTURES_SKIPPED)
    assert covered == _all_sample_filenames()


def test_demo_fixtures_has_no_duplicate_filenames() -> None:
    names = [fixture.filename for fixture in DEMO_FIXTURES]
    assert len(names) == len(set(names))


def test_demo_fixtures_never_reparses_the_scotiabank_csv_twin() -> None:
    # scotiabank_sample_statement.pdf and scotiabank_chequing_savings.csv are
    # the same chequing/savings data (data/samples/README.md) — loading both
    # would double the account balance, so the CSV twin must stay skipped.
    assert "scotiabank_chequing_savings.csv" in FIXTURES_SKIPPED
    assert "scotiabank_chequing_savings.csv" not in {f.filename for f in DEMO_FIXTURES}


def test_alex_mock_profile_matches_the_readme_facts() -> None:
    assert ALEX_MOCK_PROFILE == {
        "age": 29,
        "holdings_country": "CA",
        "year_in_canada": 2019,
        "fhsa_opened_year": 2024,
        "risk_profile": "medium",
        "prior_year_earned_income": Decimal("82000"),
    }


def test_demo_snapshot_date_matches_the_readme_reference_totals_date() -> None:
    assert DEMO_SNAPSHOT_DATE.isoformat() == "2026-07-31"
