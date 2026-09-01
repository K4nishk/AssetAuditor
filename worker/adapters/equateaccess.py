"""detect() + parse() for Equate/Solium ESOP CSV exports (KCH-49 / AA-14).

Fixture: `data/samples/equateaccess_esop.csv` — one grant tranche per row
(`plan,grant_date,shares,status,vest_date,fmv_per_share_cad,notes`). No
account-number column exists (ESOP exports are plan-scoped, not
account-scoped) — the plan name is not a real account number, so it is used
directly as the natural key rather than run through account-number masking.
Normalizes into `account`, `holding` (aggregated across vested + unvested
tranches), and `lot` (one per grant, carrying the `vested` flag so
downstream net-worth math can exclude unvested shares) drafts.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from worker.adapters.base import (
    StagedRowDraft,
    csv_header,
    read_csv_rows,
    require_decimal,
)

INSTITUTION = "equateaccess"

_REQUIRED_COLUMNS = {
    "plan",
    "grant_date",
    "shares",
    "status",
    "vest_date",
    "fmv_per_share_cad",
    "notes",
}


def detect(raw: bytes) -> bool:
    return _REQUIRED_COLUMNS <= csv_header(raw)


def parse(raw: bytes) -> list[StagedRowDraft]:
    rows = read_csv_rows(raw)

    drafts: list[StagedRowDraft] = []
    seen_accounts: set[str] = set()
    holdings: dict[str, dict[str, Any]] = {}

    for row in rows:
        plan = row["plan"].strip()
        account_mask = f"{INSTITUTION}-{plan}"

        if account_mask not in seen_accounts:
            seen_accounts.add(account_mask)
            drafts.append(
                StagedRowDraft(
                    entity="account",
                    payload={
                        "institution": INSTITUTION,
                        "account_type": "esop",
                        "masked_identifier": account_mask,
                        "currency": "CAD",
                    },
                )
            )

        shares = require_decimal(row["shares"], field="shares")
        fmv = require_decimal(row["fmv_per_share_cad"], field="fmv_per_share_cad")
        vested = row["status"].strip().lower() == "vested"

        drafts.append(
            StagedRowDraft(
                entity="lot",
                payload={
                    "account_mask": account_mask,
                    "ticker": plan,
                    "quantity": shares,
                    "unit_cost": fmv,
                    "currency": "CAD",
                    "acquired_at": row["grant_date"].strip(),
                    "vested": vested,
                    "vest_date": row["vest_date"].strip(),
                    "notes": row["notes"].strip(),
                },
            )
        )

        holding = holdings.setdefault(
            account_mask,
            {
                "account_mask": account_mask,
                "ticker": plan,
                "asset_class": "esop",
                "currency": "CAD",
                "quantity": Decimal("0"),
                "_cost_total": Decimal("0"),
            },
        )
        holding["quantity"] += shares
        holding["_cost_total"] += shares * fmv

    holding_drafts = []
    for holding in holdings.values():
        cost_total = holding.pop("_cost_total")
        quantity = holding["quantity"]
        holding["avg_cost"] = (cost_total / quantity) if quantity else None
        holding_drafts.append(StagedRowDraft(entity="holding", payload=holding))

    return drafts + holding_drafts
