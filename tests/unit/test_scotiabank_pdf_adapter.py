"""Scotiabank PDF adapter tests (KCH-50 / AA-15) — mvp.md's kill gate: a
text-layer bank statement must parse into the same rows as its CSV twin,
`data/samples/scotiabank_chequing_savings.csv`, with no LLM involved.

Covers: `detect()` true on the PDF fixture + false on every CSV/JSON fixture;
`parse()` row-for-row equivalence against the CSV's chequing rows (date,
kind/amount, running balance); Decimal-only money fields; masked (never raw)
account identifiers; deterministic/full-confidence provenance.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from worker.adapters import scotiabank
from worker.adapters.base import StagedRowDraft
from worker.masking import is_masked_account_token

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "data" / "samples"
PDF_FIXTURE = "scotiabank_sample_statement.pdf"
CSV_FIXTURE = "scotiabank_chequing_savings.csv"

OTHER_FIXTURES = sorted(
    p.name for p in SAMPLES_DIR.glob("*") if p.suffix in {".csv", ".json"}
)


def _raw(name: str) -> bytes:
    return (SAMPLES_DIR / name).read_bytes()


def _by_entity(drafts: list[StagedRowDraft], entity: str) -> list[StagedRowDraft]:
    return [d for d in drafts if d.entity == entity]


def _csv_chequing_rows() -> list[dict[str, str]]:
    with (SAMPLES_DIR / CSV_FIXTURE).open(newline="", encoding="utf-8") as f:
        return [row for row in csv.DictReader(f) if row["account_type"] == "chequing"]


# --- detect() -----------------------------------------------------------------


def test_detect_true_on_own_fixture() -> None:
    assert scotiabank.detect(_raw(PDF_FIXTURE)) is True


def test_detect_false_on_every_csv_json_fixture() -> None:
    for name in OTHER_FIXTURES:
        assert scotiabank.detect(_raw(name)) is False, f"false-positived on {name}"


def test_detect_false_on_non_pdf_garbage() -> None:
    assert scotiabank.detect(b"not a pdf at all") is False


# --- parse(): matches the CSV fixture's chequing rows exactly -----------------


def test_parse_produces_one_account_matching_csv() -> None:
    drafts = scotiabank.parse(_raw(PDF_FIXTURE))
    accounts = _by_entity(drafts, "account")

    assert len(accounts) == 1
    account = accounts[0]
    assert account.payload["account_type"] == "chequing"
    assert account.payload["masked_identifier"] == "scotia-...4821"
    assert account.payload["currency"] == "CAD"

    csv_rows = _csv_chequing_rows()
    assert {row["account_mask"] for row in csv_rows} == {account.payload["masked_identifier"]}


def test_parse_transactions_match_csv_rows_row_for_row() -> None:
    drafts = scotiabank.parse(_raw(PDF_FIXTURE))
    txns = _by_entity(drafts, "transaction")
    csv_rows = _csv_chequing_rows()

    assert len(txns) == len(csv_rows) == 8

    for txn, csv_row in zip(txns, csv_rows, strict=True):
        payload = txn.payload
        assert payload["occurred_at"].startswith(csv_row["date"])
        assert payload["account_mask"] == csv_row["account_mask"]
        assert payload["balance_after"] == Decimal(csv_row["balance"])

        if csv_row["debit"]:
            assert payload["kind"] == "debit"
            assert payload["amount"] == Decimal(csv_row["debit"])
        else:
            assert payload["kind"] == "credit"
            assert payload["amount"] == Decimal(csv_row["credit"])


def test_parse_ending_balance_matches_reference_total() -> None:
    # data/samples/README.md: chequing reference total is 4,200.
    drafts = scotiabank.parse(_raw(PDF_FIXTURE))
    txns = _by_entity(drafts, "transaction")
    assert txns[-1].payload["balance_after"] == Decimal("4200.00")


# --- cross-cutting invariants (mirrors tests/unit/test_adapters.py) ----------


def test_parse_never_uses_float_for_money() -> None:
    for draft in scotiabank.parse(_raw(PDF_FIXTURE)):
        for key in ("amount", "balance_after"):
            value = draft.payload.get(key)
            if value is not None:
                assert isinstance(value, Decimal), f"{key}={value!r} is {type(value)}"


def test_parse_masked_identifiers_are_never_raw() -> None:
    for draft in scotiabank.parse(_raw(PDF_FIXTURE)):
        mask = draft.payload.get("masked_identifier") or draft.payload.get("account_mask")
        if mask is not None:
            assert is_masked_account_token(mask), f"{mask!r} is not a canonical masked token"


def test_parse_is_deterministic_and_full_confidence() -> None:
    for draft in scotiabank.parse(_raw(PDF_FIXTURE)):
        assert draft.method == "deterministic"
        assert draft.confidence == 1.0


def test_parse_never_leaks_customer_pii() -> None:
    for draft in scotiabank.parse(_raw(PDF_FIXTURE)):
        blob = repr(draft.payload).lower()
        assert "alex" not in blob
        assert "mock" not in blob
        assert "sample st" not in blob
