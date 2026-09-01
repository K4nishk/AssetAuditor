"""Masking module tests (KCH-47 / AA-12) — run against every fixture in data/samples/.

Account numbers -> last-4 tokens, PII redaction, run before silver write and any
LLM call per CLAUDE.md hard rule #2 and docs/vault/20-domain/Data-Retention-and-Privacy.md.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pdfplumber
import pytest

from worker.masking import (
    is_masked_account_token,
    mask_account_number,
    mask_statement_text,
    redact_account_numbers,
    redact_pii,
)

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "data" / "samples"

CSV_FIXTURES = sorted(SAMPLES_DIR.glob("*.csv"))
JSON_FIXTURES = sorted(SAMPLES_DIR.glob("*.json"))


# --- mask_account_number -----------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "institution_slug", "expected"),
    [
        ("****4821", "scotia", "scotia-...4821"),
        ("XXXXXXXX4821", "scotia", "scotia-...4821"),
        ("#### 4821", "scotia", "scotia-...4821"),
        ("1234567894821", "scotia", "scotia-...4821"),
        ("scotia-...4821", "scotia", "scotia-...4821"),  # idempotent
        ("qt-...9033", "qt", "qt-...9033"),
    ],
)
def test_mask_account_number(raw: str, institution_slug: str, expected: str) -> None:
    assert mask_account_number(raw, institution_slug) == expected


def test_mask_account_number_rejects_too_few_digits() -> None:
    with pytest.raises(ValueError, match="last-4"):
        mask_account_number("n/a", "scotia")


def test_mask_account_number_never_leaks_full_number() -> None:
    masked = mask_account_number("4506123456784821", "visa")
    assert "4506123456784821" not in masked
    assert masked == "visa-...4821"


# --- redact_account_numbers ---------------------------------------------------


def test_redact_account_numbers_masks_bank_partial_mask() -> None:
    text = "Account: Preferred Package Chequing ****4821 Period: Jul 1 - Jul 31, 2026"
    result = redact_account_numbers(text, "scotia")
    assert "****4821" not in result
    assert "scotia-...4821" in result


def test_redact_account_numbers_masks_labeled_raw_number() -> None:
    text = "Account Number: 1234567890"
    result = redact_account_numbers(text, "scotia")
    assert "1234567890" not in result
    assert "scotia-...7890" in result


def test_redact_account_numbers_masks_labeled_partial_mask_four_digits() -> None:
    text = "Acct# 4821"
    result = redact_account_numbers(text, "scotia")
    assert "4821" in result  # last-4 survives, tokenized
    assert "scotia-...4821" in result
    assert result == "scotia-...4821"


def test_redact_account_numbers_leaves_dollar_amounts_alone() -> None:
    text = "MORTGAGE PMT WEALTHSIMPLE 1,890.00 2,230.45; home estimate 520000.00"
    result = redact_account_numbers(text, "ws")
    assert result == text


# --- redact_pii ----------------------------------------------------------------


def test_redact_pii_strips_labeled_customer_line() -> None:
    text = "Customer: ALEX MOCK 123 Sample St, Toronto ON"
    result = redact_pii(text)
    assert "ALEX MOCK" not in result
    assert "123 Sample St" not in result
    assert result == "Customer: [REDACTED]"


def test_redact_pii_strips_email_phone_sin() -> None:
    text = "Contact alex.mock@example.com or 416-555-0199, SIN 123-456-789."
    result = redact_pii(text)
    assert "alex.mock@example.com" not in result
    assert "416-555-0199" not in result
    assert "123-456-789" not in result
    assert "[REDACTED_EMAIL]" in result
    assert "[REDACTED_PHONE]" in result
    assert "[REDACTED_SIN]" in result


def test_redact_pii_strips_unformatted_sin() -> None:
    text = "SIN 123456789 on file."
    result = redact_pii(text)
    assert "123456789" not in result
    assert "[REDACTED_SIN]" in result


def test_redact_pii_leaves_transaction_descriptions_alone() -> None:
    text = "Jul 02 PAYROLL DEPOSIT ACME CORP 3,450.00 5,120.45"
    assert redact_pii(text) == text


def test_redact_pii_leaves_dates_alone() -> None:
    text = "Period: Jul 1 - Jul 31, 2026; as_of 2026-07-31"
    assert redact_pii(text) == text


# --- mask_statement_text (full pipeline) against the PDF fixture --------------


def _extract_pdf_text(path: Path) -> str:
    with pdfplumber.open(path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def test_mask_statement_text_scotiabank_pdf_fixture() -> None:
    raw_text = _extract_pdf_text(SAMPLES_DIR / "scotiabank_sample_statement.pdf")
    assert "****4821" in raw_text
    assert "ALEX MOCK" in raw_text

    masked = mask_statement_text(raw_text, "scotia")

    assert "****4821" not in masked
    assert "4821" in masked  # last-4 survives, tokenized
    assert "scotia-...4821" in masked
    assert "ALEX MOCK" not in masked
    assert "123 Sample St" not in masked
    # Non-PII statement content is preserved for downstream parsing.
    assert "PAYROLL DEPOSIT ACME CORP" in masked
    assert "5,120.45" in masked


def test_mask_statement_text_is_idempotent_on_scotiabank_pdf() -> None:
    raw_text = _extract_pdf_text(SAMPLES_DIR / "scotiabank_sample_statement.pdf")
    once = mask_statement_text(raw_text, "scotia")
    twice = mask_statement_text(once, "scotia")
    assert once == twice


# --- every fixture: account_mask columns are already canonical & idempotent ---


def _account_mask_values_from_csv(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or "account_mask" not in reader.fieldnames:
            return []
        return [row["account_mask"] for row in reader]


def _account_mask_values_from_json(data: object) -> list[str]:
    found: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            if key == "account_mask" and isinstance(value, str):
                found.append(value)
            else:
                found.extend(_account_mask_values_from_json(value))
    elif isinstance(data, list):
        for item in data:
            found.extend(_account_mask_values_from_json(item))
    return found


@pytest.mark.parametrize("csv_path", CSV_FIXTURES, ids=lambda p: p.name)
def test_csv_fixture_account_masks_are_canonical(csv_path: Path) -> None:
    for value in _account_mask_values_from_csv(csv_path):
        assert is_masked_account_token(value), f"{csv_path.name}: {value!r} not canonical"
        institution_slug = value.split("-...")[0]
        assert mask_account_number(value, institution_slug) == value


@pytest.mark.parametrize("json_path", JSON_FIXTURES, ids=lambda p: p.name)
def test_json_fixture_account_masks_are_canonical(json_path: Path) -> None:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    values = _account_mask_values_from_json(data)
    assert values, f"{json_path.name}: expected at least one account_mask field"
    for value in values:
        assert is_masked_account_token(value), f"{json_path.name}: {value!r} not canonical"
        institution_slug = value.split("-...")[0]
        assert mask_account_number(value, institution_slug) == value


_DATA_SUFFIXES = {".csv", ".json", ".pdf"}
ALL_DATA_FIXTURES = sorted(p for p in SAMPLES_DIR.glob("*") if p.suffix in _DATA_SUFFIXES)


@pytest.mark.parametrize("fixture_path", ALL_DATA_FIXTURES, ids=lambda p: p.name)
def test_redact_pii_is_safe_on_every_fixture(fixture_path: Path) -> None:
    """PII redaction must catch real PII and never mangle legitimate financial fields
    (fund names, tickers, dates, dollar amounts) in the rest of the fixture set."""
    if fixture_path.suffix == ".pdf":
        raw_text = _extract_pdf_text(fixture_path)
    else:
        raw_text = fixture_path.read_text(encoding="utf-8")

    redacted = redact_pii(raw_text)

    if fixture_path.name == "scotiabank_sample_statement.pdf":
        assert redacted != raw_text  # the one fixture that actually carries PII
    else:
        assert redacted == raw_text  # no labeled PII fields elsewhere -> untouched
