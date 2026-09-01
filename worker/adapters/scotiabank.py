"""detect() + parse() for Scotiabank text-layer PDF statements (KCH-50 / AA-15).

Fixture: `data/samples/scotiabank_sample_statement.pdf` — a one-page chequing
statement covering the same account and period as
`data/samples/scotiabank_chequing_savings.csv`'s chequing rows. Tier-1
pdfplumber extraction (`worker/extract/pdfplumber_tier.py`) turns the page
into text + a transaction table; this adapter normalizes that table into the
same `account`/`transaction` `StagedRowDraft` shapes the CSV fixture's rows
represent. This is mvp.md's kill gate: prove a text-layer bank statement
parses into the same rows as its CSV twin with no LLM involved, before AA-16's
fallback tier is worth building.

Customer name/address/masthead text is read only to locate the account header
and transaction table; it is never copied into a draft payload, so no PII
redaction step is needed here (mirrors AA-12's masking module, which likewise
leaves transaction descriptions untouched as non-PII).
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from worker.adapters.base import (
    AdapterParseError,
    StagedRowDraft,
    normalize_account_mask,
    to_datetime_utc,
)
from worker.extract.pdfplumber_tier import ExtractedPdf, extract, is_pdf

INSTITUTION = "scotia"

_STATEMENT_MARKER = "scotiabank"

# "Account: Preferred Package Chequing ****4821 Period: Jul 1 - Jul 31, 2026"
_ACCOUNT_HEADER_RE = re.compile(
    r"Account:\s*(?P<label>.+?)\s*(?P<mask>[*#]{2,}\d{4})\s*Period:.*?(?P<year>\d{4})",
    re.IGNORECASE | re.DOTALL,
)

_ACCOUNT_TYPES = {
    "chequing": "chequing",
    "checking": "chequing",
    "savings": "savings",
    "saving": "savings",
}

_TABLE_HEADER = ("Date", "Description", "Withdrawn", "Deposited", "Balance")


def detect(raw: bytes) -> bool:
    if not is_pdf(raw):
        return False
    try:
        extracted = extract(raw)
    except Exception:
        return False
    return _STATEMENT_MARKER in extracted.text.lower()


def parse(raw: bytes) -> list[StagedRowDraft]:
    extracted = extract(raw)
    if _STATEMENT_MARKER not in extracted.text.lower():
        raise AdapterParseError("not a Scotiabank statement: missing masthead marker")

    account_type, account_mask, year = _account_header(extracted)

    drafts: list[StagedRowDraft] = [
        StagedRowDraft(
            entity="account",
            payload={
                "institution": INSTITUTION,
                "account_type": account_type,
                "masked_identifier": account_mask,
                "currency": "CAD",
            },
        )
    ]

    rows = _transaction_rows(extracted.tables)
    if not rows:
        raise AdapterParseError("no transaction rows found in statement table")

    drafts.extend(_transaction_draft(row, account_mask=account_mask, year=year) for row in rows)
    return drafts


def _account_header(extracted: ExtractedPdf) -> tuple[str, str, int]:
    match = _ACCOUNT_HEADER_RE.search(extracted.text)
    if not match:
        raise AdapterParseError("could not find account header line")

    account_type = _account_type_from_label(match.group("label"))
    account_mask = normalize_account_mask(match.group("mask"), INSTITUTION)
    year = int(match.group("year"))
    return account_type, account_mask, year


def _account_type_from_label(label: str) -> str:
    for word in reversed(label.lower().split()):
        if word in _ACCOUNT_TYPES:
            return _ACCOUNT_TYPES[word]
    raise AdapterParseError(f"unrecognized account type in header: {label!r}")


def _transaction_rows(tables: list[list[list[str | None]]]) -> list[list[str | None]]:
    for table in tables:
        if not table:
            continue
        header = tuple((cell or "").strip() for cell in table[0])
        if header == _TABLE_HEADER:
            return table[1:]
    return []


def _transaction_draft(row: list[str | None], *, account_mask: str, year: int) -> StagedRowDraft:
    if len(row) != len(_TABLE_HEADER):
        raise AdapterParseError(f"transaction row has {len(row)} columns, expected 5: {row!r}")
    date_str, description, withdrawn, deposited, balance = ((cell or "").strip() for cell in row)

    withdrawn_amount = _parse_money(withdrawn)
    deposited_amount = _parse_money(deposited)
    if (withdrawn_amount is None) == (deposited_amount is None):
        raise AdapterParseError(f"row must have exactly one of withdrawn/deposited: {row!r}")

    if withdrawn_amount is not None:
        kind, amount = "debit", withdrawn_amount
    else:
        # deposited_amount is guaranteed non-None here by the exclusivity check above.
        assert deposited_amount is not None
        kind, amount = "credit", deposited_amount

    occurred_at = to_datetime_utc(_iso_date(date_str, year))

    return StagedRowDraft(
        entity="transaction",
        payload={
            "account_mask": account_mask,
            "kind": kind,
            "amount": amount,
            "currency": "CAD",
            "occurred_at": occurred_at.isoformat(),
            "description": description,
            "balance_after": _parse_money(balance),
        },
    )


def _parse_money(value: str) -> Decimal | None:
    if not value:
        return None
    try:
        return Decimal(value.replace(",", ""))
    except InvalidOperation as exc:
        raise AdapterParseError(f"not a decimal amount: {value!r}") from exc


def _iso_date(value: str, year: int) -> str:
    """`'Jul 02'` + statement year -> `'2026-07-02'`."""
    try:
        parsed = datetime.strptime(f"{value} {year}", "%b %d %Y")
    except ValueError as exc:
        raise AdapterParseError(f"unrecognized date {value!r}") from exc
    return parsed.strftime("%Y-%m-%d")
