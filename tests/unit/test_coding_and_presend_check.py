"""Coding + pre-send check (AA-64, #47; spec #40 decisions 4-5; owner decision R3.3).

Pure modules, no I/O. The must-block corpus is the sovereign-AI research's leak
cases plus the skeptic's bypasses (decision log, "Skeptic re-checks"); the
must-pass corpus is what the Coded extraction and commentary paths will send.
"""

from __future__ import annotations

import json
import pickle
from decimal import Decimal

import pytest

from tests.evals.golden_set import PDF_FIXTURE, golden_chequing_rows
from worker.coding import CodeError, CodeMap, code_text
from worker.extract.pdfplumber_tier import extract as extract_pdf
from worker.masking import mask_statement_text
from worker.presend_check import check_text

SLUG = "scotia"


def _masked_statement() -> str:
    return mask_statement_text(extract_pdf(PDF_FIXTURE.read_bytes()).text, SLUG)


def _two_amount_map() -> CodeMap:
    code_map = CodeMap()
    code_map.code_amount("3,450.00")
    code_map.code_amount("5,120.45")
    return code_map


# --- Coding round trip -------------------------------------------------------


def test_round_trip_is_lossless_on_the_fixture() -> None:
    masked = _masked_statement()
    coded, code_map = code_text(masked, institution_slug=SLUG)
    assert code_map.decode_text(coded) == masked


def test_every_fixture_amount_survives_as_an_exact_decimal() -> None:
    coded, code_map = code_text(_masked_statement(), institution_slug=SLUG)
    decoded = sorted(code_map.amounts().values())
    golden = sorted(
        [row.amount for row in golden_chequing_rows()]
        + [row.balance_after for row in golden_chequing_rows()]
    )
    assert decoded == golden


def test_coded_statement_keeps_no_amount_or_payee_in_plain_text() -> None:
    coded, _ = code_text(_masked_statement(), institution_slug=SLUG)
    for literal in ("3,450.00", "5,120.45", "ACME", "WEALTHSIMPLE", "METRO", "HYDRO"):
        assert literal not in coded
    assert "scotia-...4821" in coded  # the statement's own mask stays readable
    assert "Jul 02" in coded  # dates stay plain (ADR v1.2.0 §6)


def test_repeated_payees_get_different_codes() -> None:
    coded, _ = code_text(
        "Jul 02 PAYROLL DEPOSIT ACME CORP\nJul 16 PAYROLL DEPOSIT ACME CORP", institution_slug=SLUG
    )
    codes = [token for token in coded.split() if token.startswith("[P")]
    assert len(codes) == len(set(codes)) == 4  # PAYROLL and ACME CORP, twice, never reused


def test_decode_rejects_unknown_and_wrong_kind_codes() -> None:
    code_map = _two_amount_map()
    text_code = code_map.code_text_run("ACME CORP")
    with pytest.raises(CodeError):
        code_map.decode_text("[A99]")
    with pytest.raises(CodeError):
        code_map.decode_amount(text_code)
    assert code_map.decode_amount("[A01]") == Decimal("3450.00")


def test_code_map_refuses_serialization_and_prints_counts_only() -> None:
    code_map = _two_amount_map()
    code_map.code_text_run("ACME CORP")
    with pytest.raises(TypeError):
        json.dumps(code_map)
    with pytest.raises(TypeError):
        pickle.dumps(code_map)
    shown = repr(code_map) + str(code_map)
    assert "3,450.00" not in shown and "ACME" not in shown
    assert "2" in shown and "1" in shown


# --- Pre-send check: must block ----------------------------------------------

MUST_BLOCK = {
    "plain masked statement": None,  # filled from the fixture below
    "split amount": "WRAPS 1,234\n.56 [A01]",
    "amount without decimals": "E-TRANSFER $1200 [A01]",
    "fr-CA amount": "DEPOT 1 234,56 [A01]",
    "bare integer value": "PAYROLL 3450 [A01]",
    "transit-account number": "TRANSFER TO 00412-1234567 [A01]",
    "card number": "PAYMENT 4520 1234 5678 9012 [A01]",
    "unnormalised mask": "Account ****4821",
    "holder name": "E-TRANSFER FROM ALEX MOCK [A01]",
    "postal code": "Toronto ON M5V 2T6",
    "email": "contact alex.mock@example.com",
    "SIN": "SIN 046 454 286",
    "address line": "123 Sample St",
    "third-party name": "E-TRANSFER TO JANE DOE [A01]",
    "employer": "PAYROLL DEPOSIT ACME CORP [A01]",
    "spelled-out amount": "CHEQUE ONE THOUSAND TWO HUNDRED DOLLARS",
    # skeptic's bypasses of the research prototype
    "unmapped code": "TRANSFER [A99]",
    "code-shaped reference": "TRANSFER REF [T4520123456789012] [A01]",
    "fake ISO date": "REF 3450-00-12",
    "foreign institution mask": "PAYMENT visa-...9012",
    "superscript digits": "PAYMENT ³⁴⁵⁰",
    "wrapped description line": "ACME CORP",
}


@pytest.mark.parametrize("name", sorted(MUST_BLOCK))
def test_must_block(name: str) -> None:
    text = MUST_BLOCK[name] if MUST_BLOCK[name] is not None else _masked_statement()
    result = check_text(text, code_map=_two_amount_map(), institution_slug=SLUG)
    assert not result.passed, f"{name!r} was allowed out"


# --- Pre-send check: must pass ---------------------------------------------------


def test_coded_fixture_statement_passes() -> None:
    coded, code_map = code_text(_masked_statement(), institution_slug=SLUG)
    result = check_text(coded, code_map=code_map, institution_slug=SLUG)
    assert result.passed, result.counts


def test_coded_commentary_from_readme_totals_passes() -> None:
    code_map = CodeMap()
    total, liabilities, net = (
        code_map.code_amount(v) for v in ("618,259.00", "421,800.00", "196,459.00")
    )
    home, chequing = (code_map.code_text_run(v) for v in ("home estimate", "Scotiabank"))
    text = (
        "As of 2026-07-31:\n"
        f"Total assets: ${total} CAD\n"
        f"Total liabilities: ${liabilities} CAD\n"
        f"Net worth: ${net} CAD\n"
        "\nDiversification by institution:\n"
        f"- {home}: (84.1% of total assets)\n"
        f"- {chequing}: (2.2% of total assets)\n"
    )
    result = check_text(
        text, code_map=code_map, institution_slug=SLUG, percentages=("84.1%", "2.2%")
    )
    assert result.passed, result.counts


def test_check_reports_counts_only() -> None:
    result = check_text(
        "E-TRANSFER TO JANE DOE 1,234.56", code_map=CodeMap(), institution_slug=SLUG
    )
    assert not result.passed
    assert "JANE" not in repr(result) and "1,234.56" not in repr(result)
    assert all(isinstance(v, int) for v in result.counts.values())


def test_a_card_number_cannot_pass_as_a_date_year() -> None:
    # Security-review finding: `Mon D YYYY` accepted any 4-digit "year", so the
    # first group of a card number after a date stayed plain and passed the check.
    coded, code_map = code_text("May 1 4520 1234 5678 9012 PAYMENT", institution_slug=SLUG)
    assert "4520" not in coded
    assert check_text(coded, code_map=code_map, institution_slug=SLUG).passed
    leaked = check_text("May 1 4520", code_map=CodeMap(), institution_slug=SLUG)
    assert not leaked.passed
