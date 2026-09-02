"""detect() + parse() for TD CSV exports (KCH-49 / AA-14).

Fixture: `data/samples/td_loc_mutualfunds.csv` — a pivoted metric-per-row
format (`product,account_mask,as_of,metric,value,currency`), one group of
rows per `(product, account_mask)`. Normalizes into `account`, `liability`
(line of credit), and `holding` (mutual fund) drafts.
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
    to_decimal,
)

INSTITUTION = "td"

_REQUIRED_COLUMNS = {"product", "account_mask", "as_of", "metric", "value", "currency"}


def detect(raw: bytes) -> bool:
    return _REQUIRED_COLUMNS <= csv_header(raw)


def _require_metric(metrics: dict[str, str], name: str) -> str:
    """Like `require_decimal`, but for a metric pulled out of the pivoted
    `metric,value` rows: a missing or blank metric is a hard parse failure
    rather than a `KeyError` leaking out of adapter internals."""
    value = metrics.get(name)
    if not value:
        raise AdapterParseError(f"missing required TD metric {name!r}")
    return value


def _require_metric_decimal(metrics: dict[str, str], name: str) -> Decimal:
    return require_decimal(_require_metric(metrics, name), field=name)


def parse(raw: bytes) -> list[StagedRowDraft]:
    rows = read_csv_rows(raw)

    groups: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    for row in rows:
        key = (row["product"].strip(), row["account_mask"].strip())
        if key not in groups:
            groups[key] = {"as_of": row["as_of"].strip(), "currency": None, "metrics": {}}
            order.append(key)
        group = groups[key]
        group["metrics"][row["metric"].strip()] = row["value"].strip()
        currency = row["currency"].strip()
        if currency and group["currency"] is None:
            group["currency"] = currency

    drafts: list[StagedRowDraft] = []
    for product, raw_mask in order:
        group = groups[(product, raw_mask)]
        account_mask = normalize_account_mask(raw_mask, INSTITUTION)
        currency = group["currency"] or "CAD"
        metrics = group["metrics"]

        drafts.append(
            StagedRowDraft(
                entity="account",
                payload={
                    "institution": INSTITUTION,
                    "account_type": product,
                    "masked_identifier": account_mask,
                    "currency": currency,
                },
            )
        )

        if product == "line_of_credit":
            drafts.append(
                StagedRowDraft(
                    entity="liability",
                    payload={
                        "account_mask": account_mask,
                        "kind": product,
                        "balance": _require_metric_decimal(metrics, "balance_owing"),
                        "currency": currency,
                        "interest_rate": _require_metric_decimal(metrics, "interest_rate_pct"),
                        "credit_limit": to_decimal(metrics.get("credit_limit")),
                        "min_payment": to_decimal(metrics.get("min_payment")),
                        "as_of": group["as_of"],
                    },
                )
            )
        elif product == "mutual_fund":
            drafts.append(
                StagedRowDraft(
                    entity="holding",
                    payload={
                        "account_mask": account_mask,
                        "ticker": _require_metric(metrics, "fund_name"),
                        "quantity": _require_metric_decimal(metrics, "units"),
                        "avg_cost": to_decimal(metrics.get("nav_per_unit")),
                        "currency": currency,
                        "market_value": to_decimal(metrics.get("market_value")),
                        "mer_pct": to_decimal(metrics.get("mer_pct")),
                        "as_of": group["as_of"],
                    },
                )
            )
        else:
            raise AdapterParseError(f"unrecognized TD product: {product!r}")

    return drafts
