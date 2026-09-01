"""Shared types + parsing helpers for the CSV/JSON adapters (KCH-49 / AA-14).

Every adapter (`questrade.py`, `wealthsimple.py`, `td.py`, `kraken.py`,
`moomoo.py`, `equateaccess.py`) exposes `detect(raw: bytes) -> bool` and
`parse(raw: bytes) -> list[StagedRowDraft]` per
`templates/backend/v1_fastapi_modular/README.md`. `parse()` only normalizes
bronze bytes into draft silver-shaped entities (the `StagedRow` pydantic
sketch, mirrored here without the FastAPI/pydantic dependency); it never
resolves foreign keys or writes to the DB — that is AA-17 (confirm) / AA-18
(silver write). References between drafts (e.g. a holding's account) use the
source file's own natural keys (`account_mask`, `ticker`) rather than UUIDs,
since no row IDs exist yet at this stage.

`Decimal` for every money/quantity field per CLAUDE.md hard rule #4 — never
route fixture values through `float`.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from worker.masking import is_masked_account_token, mask_account_number

Entity = Literal["transaction", "holding", "lot", "liability", "account"]
Method = Literal["deterministic", "llm", "manual_entry", "manual_correction"]


@dataclass(frozen=True)
class StagedRowDraft:
    """Mirrors the `staged_rows` table shape (migration 0001) pre-insert."""

    entity: Entity
    payload: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    method: Method = "deterministic"


class AdapterParseError(ValueError):
    """Raised when a file doesn't match the shape an adapter expects."""


def read_csv_rows(raw: bytes) -> list[dict[str, str]]:
    """Decode + parse CSV bytes, dropping fully-blank trailing rows."""
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    return [row for row in reader if any((v or "").strip() for v in row.values())]


def csv_header(raw: bytes) -> set[str]:
    """Column names only — cheap enough to use from `detect()`."""
    text = raw.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    try:
        return set(next(reader))
    except StopIteration:
        return set()


def read_json(raw: bytes) -> Any:
    """Decode JSON bytes, parsing every float literal as `Decimal` (never binary
    `float`) so money/quantity fields never lose precision. JSON integers are
    already exact as Python `int` and are left alone — callers that need a
    `Decimal` for an int-valued field (e.g. a whole-share quantity) should run
    it through `to_decimal`."""
    return json.loads(raw.decode("utf-8-sig"), parse_float=Decimal)


def to_decimal(value: str | float | int | Decimal | None) -> Decimal | None:
    """Convert a fixture scalar to `Decimal`; blank/None stays None."""
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise AdapterParseError(f"not a decimal: {value!r}") from exc


def require_decimal(value: str | float | int | Decimal | None, *, field: str) -> Decimal:
    """Like `to_decimal`, but a missing/blank value is a hard parse failure —
    use for fields a row can never omit, instead of an `assert` a `-O` run
    could strip and let a silent `None` slip into a staged payload."""
    decimal_value = to_decimal(value)
    if decimal_value is None:
        raise AdapterParseError(f"missing required decimal field {field!r}")
    return decimal_value


def to_date(value: str) -> date:
    return date.fromisoformat(value.strip())


def to_datetime_utc(value: str) -> datetime:
    """Parse a `YYYY-MM-DD[ HH:MM:SS]` timestamp as UTC (no tz in source data)."""
    value = value.strip().replace(" ", "T")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def normalize_account_mask(value: str, institution_slug: str) -> str:
    """Canonicalize an account identifier, preserving an already-masked token's
    own prefix (per AA-12's idempotency contract) instead of relabeling it."""
    if is_masked_account_token(value):
        return value
    return mask_account_number(value, institution_slug)
