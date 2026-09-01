"""Golden-set fixtures for AA-16's LLM fallback tier eval.

Reuses the Scotiabank PDF/CSV pairing `tests/unit/test_scotiabank_pdf_adapter.py`
uses for the deterministic adapter's kill gate: the CSV's chequing rows are
the known-correct answer, the PDF is the same statement as text-layer bytes.
The deterministic adapter is expected to match it exactly; the LLM fallback
tier is only expected to come close, so `test_llm_golden_set.py` scores
against these rows rather than asserting equality.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "data" / "samples"
PDF_FIXTURE = SAMPLES_DIR / "scotiabank_sample_statement.pdf"
CSV_FIXTURE = SAMPLES_DIR / "scotiabank_chequing_savings.csv"


@dataclass(frozen=True)
class GoldenRow:
    date: str
    kind: str
    amount: Decimal
    balance_after: Decimal
    account_mask: str


def golden_chequing_rows() -> list[GoldenRow]:
    with CSV_FIXTURE.open(newline="", encoding="utf-8") as f:
        rows = [row for row in csv.DictReader(f) if row["account_type"] == "chequing"]

    golden: list[GoldenRow] = []
    for row in rows:
        if row["debit"]:
            kind, amount = "debit", Decimal(row["debit"])
        else:
            kind, amount = "credit", Decimal(row["credit"])
        golden.append(
            GoldenRow(
                date=row["date"],
                kind=kind,
                amount=amount,
                balance_after=Decimal(row["balance"]),
                account_mask=row["account_mask"],
            )
        )
    return golden
