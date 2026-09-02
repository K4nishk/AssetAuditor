"""Yahoo Finance portfolio-export CSV parser (KCH-55 / AA-20's "optional lots
incl. Yahoo Finance export import").

Not one of AA-14's institution adapters (`detect()`/`parse()` dispatched from
an uploaded bronze file) — the manual-entry portfolio form
(`app.routes.manual_entry`) calls `parse_lots` directly once the user picks
"import from Yahoo Finance" and pastes/uploads the export, supplying the
target account itself (the export carries no account identifier of its own).

Yahoo's classic portfolio-manager CSV export columns: `Symbol,Current
Price,Date,Time,Change,Open,High,Low,Volume,Trade Date,Purchase
Price,Quantity,Commission,High Limit,Low Limit,Comment`. Only `Symbol`,
`Trade Date`, `Purchase Price`, and `Quantity` are used here — the rest
(live market price, day change, free-text comment, ...) aren't silver-shaped
facts this importer writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from worker.adapters.base import AdapterParseError, csv_header, read_csv_rows, require_decimal

_REQUIRED_COLUMNS = {"Symbol", "Trade Date", "Purchase Price", "Quantity"}


@dataclass(frozen=True)
class YahooFinanceLot:
    """One imported lot — the caller (`app.domain.manual_entry`) attaches the
    account context this file doesn't carry."""

    ticker: str
    quantity: Decimal
    unit_cost: Decimal
    acquired_at: str  # ISO YYYY-MM-DD


def detect(raw: bytes) -> bool:
    try:
        return _REQUIRED_COLUMNS <= csv_header(raw)
    except UnicodeDecodeError:
        return False


def parse_lots(raw: bytes) -> list[YahooFinanceLot]:
    if not detect(raw):
        raise AdapterParseError(
            f"Yahoo Finance export missing required columns: {sorted(_REQUIRED_COLUMNS)}"
        )

    lots: list[YahooFinanceLot] = []
    for row in read_csv_rows(raw):
        symbol = row["Symbol"].strip()
        if not symbol:
            continue
        lots.append(
            YahooFinanceLot(
                ticker=symbol,
                quantity=require_decimal(row["Quantity"], field="Quantity"),
                unit_cost=require_decimal(row["Purchase Price"], field="Purchase Price"),
                acquired_at=_to_iso_date(row["Trade Date"].strip()),
            )
        )
    return lots


def _to_iso_date(value: str) -> str:
    """Yahoo's `Trade Date` is `M/D/YYYY` (no zero-padding), not ISO-8601."""
    try:
        return datetime.strptime(value, "%m/%d/%Y").date().isoformat()
    except ValueError as exc:
        raise AdapterParseError(f"unrecognized Trade Date format: {value!r}") from exc
