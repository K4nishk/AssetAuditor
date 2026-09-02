"""detect() + parse() for Questrade CSV exports (KCH-49 / AA-14).

Fixture: `data/samples/questrade_activity.csv` — one row per BUY fill,
columns `account,account_mask,trade_date,action,symbol,asset_class,
quantity,price,commission,currency`. Normalizes into `account`, `holding`
(aggregated per symbol), `lot` (one per fill), and `transaction` drafts —
the entities `data/samples/README.md` documents this fixture exercising.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from worker.adapters.base import (
    AdapterParseError,
    StagedRowDraft,
    csv_header,
    normalize_account_mask,
    read_csv_rows,
    require_decimal,
    to_datetime_utc,
    to_decimal,
)

INSTITUTION = "questrade"

_REQUIRED_COLUMNS = {
    "account",
    "account_mask",
    "trade_date",
    "action",
    "symbol",
    "asset_class",
    "quantity",
    "price",
    "commission",
    "currency",
}


def detect(raw: bytes) -> bool:
    return _REQUIRED_COLUMNS <= csv_header(raw)


def parse(raw: bytes) -> list[StagedRowDraft]:
    rows = read_csv_rows(raw)

    accounts_seen: dict[str, dict[str, Any]] = {}
    holdings: dict[tuple[str, str], dict[str, Any]] = {}
    drafts: list[StagedRowDraft] = []

    for row in rows:
        account_mask = normalize_account_mask(row["account_mask"].strip(), INSTITUTION)
        account_type = row["account"].strip().lower()
        trade_date = row["trade_date"].strip()

        action = row["action"].strip().lower()
        if action != "buy":
            raise AdapterParseError(
                f"unsupported Questrade action {action!r} for {row['symbol']!r} "
                f"on {trade_date}: only BUY fills are normalized by this adapter"
            )

        if account_mask not in accounts_seen:
            accounts_seen[account_mask] = {
                "institution": INSTITUTION,
                "account_type": account_type,
                "masked_identifier": account_mask,
                "currency": "CAD",
            }

        symbol = row["symbol"].strip()
        quantity = require_decimal(row["quantity"], field="quantity")
        price = require_decimal(row["price"], field="price")
        commission = to_decimal(row["commission"]) or Decimal("0")
        currency = row["currency"].strip()
        occurred_at = to_datetime_utc(trade_date)

        drafts.append(
            StagedRowDraft(
                entity="lot",
                payload={
                    "account_mask": account_mask,
                    "ticker": symbol,
                    "quantity": quantity,
                    "unit_cost": price,
                    "currency": currency,
                    "acquired_at": trade_date,
                    "vested": None,
                },
            )
        )

        drafts.append(
            StagedRowDraft(
                entity="transaction",
                payload={
                    "account_mask": account_mask,
                    "ticker": symbol,
                    "kind": action,
                    "amount": quantity * price + commission,
                    "currency": currency,
                    "occurred_at": occurred_at.isoformat(),
                    "commission": commission,
                    "asset_class": row["asset_class"].strip(),
                },
            )
        )

        key = (account_mask, symbol)
        holding = holdings.setdefault(
            key,
            {
                "account_mask": account_mask,
                "ticker": symbol,
                "asset_class": row["asset_class"].strip(),
                "currency": currency,
                "quantity": Decimal("0"),
                "_cost_total": Decimal("0"),
            },
        )
        holding["quantity"] += quantity
        holding["_cost_total"] += quantity * price

    account_drafts = [
        StagedRowDraft(entity="account", payload=payload)
        for payload in accounts_seen.values()
    ]

    holding_drafts = []
    for holding in holdings.values():
        cost_total = holding.pop("_cost_total")
        quantity = holding["quantity"]
        holding["avg_cost"] = (cost_total / quantity) if quantity else None
        holding_drafts.append(StagedRowDraft(entity="holding", payload=holding))

    return account_drafts + holding_drafts + drafts
