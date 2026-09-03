"""Demo-mode fixture dispatch table + the fixed "Alex Mock" profile facts
(KCH-69 / AA-32).

Pure metadata only — no I/O. `app.routes.demo` reads the actual fixture
bytes from `data/samples/` and drives the adapter/silver/gold pipeline; this
module just says which of the nine `data/samples/README.md` fixtures have an
adapter that can parse them today, and what content-type/institution each
needs when it lands as a `bronze_files` row.

Two of the nine fixtures are intentionally left out of `DEMO_FIXTURES`:

- `canadalife_rpp.json` — `worker/adapters/canadalife.py` is still AA-1's
  one-line stub docstring; no issue has implemented a Canada Life adapter
  yet, so there is nothing here to parse it with. Not this issue's gap to
  close (scope is the seed button, not a new adapter).
- `scotiabank_chequing_savings.csv` — never parsed by any adapter in this
  repo. It exists purely as the golden comparison
  `tests/unit/test_scotiabank_pdf_adapter.py` checks the PDF tier against
  (AA-15's kill gate); the *actual* Scotiabank chequing/savings data ships
  into silver via `scotiabank_sample_statement.pdf` below, through the real
  pdfplumber-tier adapter. Loading both would double the chequing account's
  balance.

`app.routes.demo`'s response names both as `fixtures_skipped`, so the gap is
visible rather than silently swallowed (CLAUDE.md: no silent truncation).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from types import ModuleType
from typing import Any

from worker.adapters import equateaccess, kraken, moomoo, questrade, scotiabank, td, wealthsimple


@dataclass(frozen=True)
class DemoFixture:
    filename: str
    content_type: str
    adapter: ModuleType


DEMO_FIXTURES: tuple[DemoFixture, ...] = (
    DemoFixture("scotiabank_sample_statement.pdf", "application/pdf", scotiabank),
    DemoFixture("questrade_activity.csv", "text/csv", questrade),
    DemoFixture("wealthsimple.json", "application/json", wealthsimple),
    DemoFixture("td_loc_mutualfunds.csv", "text/csv", td),
    DemoFixture("kraken_ledger.csv", "text/csv", kraken),
    DemoFixture("moomoo_crypto.csv", "text/csv", moomoo),
    DemoFixture("equateaccess_esop.csv", "text/csv", equateaccess),
)

FIXTURES_SKIPPED: tuple[str, ...] = ("canadalife_rpp.json", "scotiabank_chequing_savings.csv")

# data/samples/README.md's reference totals are dated 2026-07-31 — seeding
# the gold snapshot at that fixed date (rather than "today") is what makes
# those numbers reproducible from a fresh seed regardless of when the demo
# actually runs.
DEMO_SNAPSHOT_DATE = date(2026, 7, 31)

# data/samples/README.md: "Alex Mock", age 29, holdings_country=CA, in Canada
# since 2019, FHSA opened 2024, risk profile medium, prior-year earned income
# $82,000. Keys match `app.db.queries.users_profile.upsert_profile`'s kwargs.
ALEX_MOCK_PROFILE: dict[str, Any] = {
    "age": 29,
    "holdings_country": "CA",
    "year_in_canada": 2019,
    "fhsa_opened_year": 2024,
    "risk_profile": "medium",
    "prior_year_earned_income": Decimal("82000"),
}
