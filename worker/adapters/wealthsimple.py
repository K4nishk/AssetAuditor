"""detect() + parse() for Wealthsimple JSON exports (KCH-49 / AA-14).

Fixture: `data/samples/wealthsimple.json` — HISA + FHSA-invest accounts plus
a mortgage liability. Normalizes into `account`, `holding`, `liability`, and
`transaction` drafts. FHSA contributions become `contribution`-kind
transactions rather than `room_events` rows directly: `staged_rows.entity`
(migration 0001) only allows `transaction|holding|lot|liability|account`, so
room-ledger derivation from these contribution transactions is AA-17/AA-18's
job, not this adapter's — `data/samples/README.md`'s "room_events (FHSA)"
note describes the source data this fixture ultimately feeds, not a
staged-row entity produced here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from worker.adapters.base import (
    AdapterParseError,
    StagedRowDraft,
    normalize_account_mask,
    read_json,
    to_decimal,
)

INSTITUTION = "wealthsimple"


def detect(raw: bytes) -> bool:
    try:
        data = read_json(raw)
    except (UnicodeDecodeError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    accounts = data.get("accounts")
    return (
        isinstance(accounts, list)
        and isinstance(data.get("liabilities"), list)
        and any(isinstance(a, dict) and "account_mask" in a for a in accounts)
    )


def parse(raw: bytes) -> list[StagedRowDraft]:
    data = read_json(raw)
    if not isinstance(data, dict):
        raise AdapterParseError("wealthsimple export must be a JSON object")

    drafts: list[StagedRowDraft] = []

    for account in data.get("accounts", []):
        account_mask = normalize_account_mask(account["account_mask"], INSTITUTION)
        drafts.append(_account_draft(account["type"], account_mask))

        for holding in account.get("holdings", []):
            drafts.append(
                StagedRowDraft(
                    entity="holding",
                    payload={
                        "account_mask": account_mask,
                        "ticker": holding["symbol"],
                        "asset_class": holding["asset_class"],
                        "quantity": to_decimal(holding["quantity"]),
                        "avg_cost": to_decimal(holding["avg_cost_cad"]),
                        "market_value_cad": to_decimal(holding["market_value_cad"]),
                        "currency": "CAD",
                    },
                )
            )

        for contribution in account.get("contributions", []):
            year = int(contribution["year"])
            drafts.append(
                StagedRowDraft(
                    entity="transaction",
                    payload={
                        "account_mask": account_mask,
                        "kind": "contribution",
                        "amount": to_decimal(contribution["amount_cad"]),
                        "currency": "CAD",
                        "occurred_at": datetime(year, 12, 31, tzinfo=UTC).isoformat(),
                        "year": year,
                        "room_account_type": "fhsa",
                    },
                )
            )

    for liability in data.get("liabilities", []):
        account_mask = normalize_account_mask(liability["account_mask"], INSTITUTION)
        linked_asset: dict[str, Any] = dict(liability.get("linked_asset") or {})
        if "user_estimated_value_cad" in linked_asset:
            linked_asset["user_estimated_value_cad"] = to_decimal(
                linked_asset["user_estimated_value_cad"]
            )

        drafts.append(_account_draft(liability["type"], account_mask))
        drafts.append(
            StagedRowDraft(
                entity="liability",
                payload={
                    "account_mask": account_mask,
                    "kind": liability["type"],
                    "balance": to_decimal(liability["principal_remaining_cad"]),
                    "currency": "CAD",
                    "interest_rate": to_decimal(liability["rate_pct"]),
                    "term_end": liability.get("term_end"),
                    "amortization_years_remaining": liability.get(
                        "amortization_years_remaining"
                    ),
                    "monthly_payment_cad": to_decimal(liability.get("monthly_payment_cad")),
                    "linked_asset": linked_asset,
                },
            )
        )

    return drafts


def _account_draft(account_type: str, account_mask: str) -> StagedRowDraft:
    return StagedRowDraft(
        entity="account",
        payload={
            "institution": INSTITUTION,
            "account_type": account_type,
            "masked_identifier": account_mask,
            "currency": "CAD",
        },
    )
