"""detect() + parse() for moomoo CSV position exports (KCH-49 / AA-14).

Fixture: `data/samples/moomoo_crypto.csv` — one already-aggregated position
per row (`account_mask,as_of,asset,quantity,avg_cost_cad,market_value_cad`).
Normalizes into `account` and `holding` drafts.
"""

from __future__ import annotations

from worker.adapters.base import (
    StagedRowDraft,
    csv_header,
    normalize_account_mask,
    read_csv_rows,
    to_decimal,
)

INSTITUTION = "moomoo"

_REQUIRED_COLUMNS = {
    "account_mask",
    "as_of",
    "asset",
    "quantity",
    "avg_cost_cad",
    "market_value_cad",
}


def detect(raw: bytes) -> bool:
    return _REQUIRED_COLUMNS <= csv_header(raw)


def parse(raw: bytes) -> list[StagedRowDraft]:
    rows = read_csv_rows(raw)

    drafts: list[StagedRowDraft] = []
    seen_accounts: set[str] = set()

    for row in rows:
        account_mask = normalize_account_mask(row["account_mask"].strip(), INSTITUTION)

        if account_mask not in seen_accounts:
            seen_accounts.add(account_mask)
            drafts.append(
                StagedRowDraft(
                    entity="account",
                    payload={
                        "institution": INSTITUTION,
                        "account_type": "crypto_brokerage",
                        "masked_identifier": account_mask,
                        "currency": "CAD",
                    },
                )
            )

        drafts.append(
            StagedRowDraft(
                entity="holding",
                payload={
                    "account_mask": account_mask,
                    "ticker": row["asset"].strip(),
                    "asset_class": "crypto",
                    "quantity": to_decimal(row["quantity"]),
                    "avg_cost": to_decimal(row["avg_cost_cad"]),
                    "market_value_cad": to_decimal(row["market_value_cad"]),
                    "currency": "CAD",
                    "as_of": row["as_of"].strip(),
                },
            )
        )

    return drafts
