"""detect() + parse() for Kraken CSV ledger exports (KCH-49 / AA-14).

Fixture: `data/samples/kraken_ledger.csv` — columns
`txid,refid,time,type,asset,amount,fee,balance`, 8-decimal-precision crypto
amounts. Every numeric field is parsed straight from the CSV string into
`Decimal` (never via `float`) — this is the adapter the issue title calls
out for it. No account-identifier column exists in this export (one ledger
per exchange account), so a stable synthetic mask (`kraken-default`, not a
real account number) stands in as the natural key linking transactions and
holdings.

Normalizes into `account`, `transaction` (one per ledger row), and `holding`
(one per asset, quantity = that asset's latest running `balance` — the
ledger's own authoritative running total, not a re-derived sum) drafts.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from worker.adapters.base import (
    StagedRowDraft,
    csv_header,
    read_csv_rows,
    require_decimal,
    to_datetime_utc,
    to_decimal,
)

INSTITUTION = "kraken"
_DEFAULT_ACCOUNT_MASK = "kraken-default"

_REQUIRED_COLUMNS = {"txid", "refid", "time", "type", "asset", "amount", "fee", "balance"}

# CAD in/out movements are cash flow, not a tradeable holding to report.
_NON_HOLDING_ASSETS = {"CAD", "USD"}


def detect(raw: bytes) -> bool:
    return _REQUIRED_COLUMNS <= csv_header(raw)


def parse(raw: bytes) -> list[StagedRowDraft]:
    rows = read_csv_rows(raw)

    drafts: list[StagedRowDraft] = [
        StagedRowDraft(
            entity="account",
            payload={
                "institution": INSTITUTION,
                "account_type": "crypto_exchange",
                "masked_identifier": _DEFAULT_ACCOUNT_MASK,
                "currency": "CAD",
            },
        )
    ]

    latest_balance: dict[str, tuple[Any, Decimal]] = {}

    for row in rows:
        asset = row["asset"].strip()
        amount = require_decimal(row["amount"], field="amount")
        fee = to_decimal(row["fee"]) or Decimal("0")
        balance = require_decimal(row["balance"], field="balance")
        occurred_at = to_datetime_utc(row["time"])

        drafts.append(
            StagedRowDraft(
                entity="transaction",
                payload={
                    "account_mask": _DEFAULT_ACCOUNT_MASK,
                    "ticker": asset,
                    "kind": row["type"].strip().lower(),
                    "amount": amount,
                    "currency": asset,
                    "occurred_at": occurred_at.isoformat(),
                    "fee": fee,
                    "txid": row["txid"].strip(),
                    "refid": row["refid"].strip(),
                },
            )
        )

        if asset in _NON_HOLDING_ASSETS:
            continue

        prior = latest_balance.get(asset)
        if prior is None or occurred_at > prior[0]:
            latest_balance[asset] = (occurred_at, balance)

    for asset, (_, balance) in latest_balance.items():
        drafts.append(
            StagedRowDraft(
                entity="holding",
                payload={
                    "account_mask": _DEFAULT_ACCOUNT_MASK,
                    "ticker": asset,
                    "asset_class": "crypto",
                    "quantity": balance,
                    "avg_cost": None,
                    "currency": asset,
                },
            )
        )

    return drafts
