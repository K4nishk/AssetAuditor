"""Manual-entry form -> `StagedRowDraft` builders (KCH-55 / AA-20).

The no-PDF path: portfolio and account-balance forms write the same silver
shapes the CSV/PDF adapters do (`worker.adapters.base.StagedRowDraft`), just
built from typed form fields instead of parsed bronze bytes. No I/O here —
`app.routes.manual_entry` is the only caller, and it stages every draft this
module returns through the same `staged_rows` -> confirm path AA-17 already
built (`method="manual_entry"`, reviewable/editable on the same parse-confirm
screen before it ever reaches silver).

Every account still gets a masked identifier (`worker.masking`) — a manually
typed account number is exactly the kind of raw identifier CLAUDE.md's
masking rule exists for, even though it never touches an LLM prompt here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from worker.adapters.base import Method, StagedRowDraft, normalize_account_mask
from worker.adapters.yahoo_finance import YahooFinanceLot

METHOD: Method = "manual_entry"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


class ManualEntryValidationError(ValueError):
    """A manual-entry form's field values can't be built into staged rows —
    the route surfaces this as a 422, same treatment
    `app.db.queries.silver.SilverResolutionError` gets at confirm time."""


@dataclass(frozen=True)
class AccountInput:
    institution: str
    account_type: str
    account_number: str
    currency: str = "CAD"


@dataclass(frozen=True)
class LotInput:
    quantity: Decimal
    unit_cost: Decimal | None = None
    currency: str | None = None
    acquired_at: str | None = None
    vested: bool | None = None


@dataclass(frozen=True)
class PortfolioEntryInput:
    account: AccountInput
    ticker: str
    quantity: Decimal
    avg_cost: Decimal | None = None
    currency: str = "CAD"
    lots: list[LotInput] = field(default_factory=list)


@dataclass(frozen=True)
class AccountBalanceInput:
    account: AccountInput
    balance: Decimal
    currency: str = "CAD"


def _institution_slug(institution: str) -> str:
    slug = _SLUG_RE.sub("-", institution.strip().lower()).strip("-")
    if not slug:
        raise ManualEntryValidationError(
            "institution must contain at least one alphanumeric character"
        )
    return slug


def _account_draft(account: AccountInput) -> tuple[StagedRowDraft, str]:
    slug = _institution_slug(account.institution)
    try:
        mask = normalize_account_mask(account.account_number.strip(), slug)
    except ValueError as exc:
        raise ManualEntryValidationError(str(exc)) from exc

    draft = StagedRowDraft(
        entity="account",
        payload={
            "masked_identifier": mask,
            "institution": account.institution.strip(),
            "account_type": account.account_type.strip().lower(),
            "currency": account.currency,
        },
        method=METHOD,
    )
    return draft, mask


def build_portfolio_drafts(entry: PortfolioEntryInput) -> list[StagedRowDraft]:
    """One `account` + one `holding` (+ one `lot` per typed lot) draft.

    A holding needs a value: either an explicit `avg_cost` or lots that a
    weighted-average cost can actually be derived from (`app.domain.gold.
    value_holding` treats a holding with neither as worthless, per its own
    docstring).

    "At least one lot" is not the same requirement. `_weighted_avg_cost` skips
    lots with no `unit_cost`, so a lot list carrying only quantities derives to
    `None` and used to produce a holding with `avg_cost: None` — silently
    worthless rather than rejected. The check is therefore on derivability, not
    on the collection being non-empty.
    """
    if entry.quantity <= 0:
        raise ManualEntryValidationError("quantity must be positive")

    derived_avg_cost: Decimal | None = None
    if entry.avg_cost is None:
        derived_avg_cost = _weighted_avg_cost(entry.lots)
        if derived_avg_cost is None:
            raise ManualEntryValidationError(
                "provide avg_cost, or at least one lot with a unit_cost to derive it from"
            )

    account_draft, mask = _account_draft(entry.account)
    ticker = entry.ticker.strip().upper()
    if not ticker:
        raise ManualEntryValidationError("ticker must not be empty")

    avg_cost = entry.avg_cost if entry.avg_cost is not None else derived_avg_cost

    holding_draft = StagedRowDraft(
        entity="holding",
        payload={
            "account_mask": mask,
            "ticker": ticker,
            "quantity": entry.quantity,
            "avg_cost": avg_cost,
            "currency": entry.currency,
        },
        method=METHOD,
    )

    lot_drafts = [
        StagedRowDraft(
            entity="lot",
            payload={
                "account_mask": mask,
                "ticker": ticker,
                "quantity": lot.quantity,
                "unit_cost": lot.unit_cost,
                "currency": lot.currency or entry.currency,
                "acquired_at": lot.acquired_at,
                "vested": lot.vested,
            },
            method=METHOD,
        )
        for lot in entry.lots
    ]

    return [account_draft, holding_draft, *lot_drafts]


def _weighted_avg_cost(lots: list[LotInput]) -> Decimal | None:
    cost_total = Decimal("0")
    quantity_total = Decimal("0")
    for lot in lots:
        if lot.unit_cost is None:
            continue
        cost_total += lot.quantity * lot.unit_cost
        quantity_total += lot.quantity
    return (cost_total / quantity_total) if quantity_total else None


def build_account_balance_drafts(
    entry: AccountBalanceInput, *, occurred_at: datetime
) -> list[StagedRowDraft]:
    """One `account` + one `transaction` draft. A first balance entry has no
    prior running balance to reconcile against (silver has no opening-balance
    column, per `app.domain.gold.compute_cash_balance`'s docstring), so it's
    staged as a single `credit`/`debit` covering the whole stated amount —
    the same net-movement convention that function already assumes."""
    if entry.balance == 0:
        raise ManualEntryValidationError("balance must be non-zero")

    account_draft, mask = _account_draft(entry.account)
    kind = "credit" if entry.balance > 0 else "debit"
    transaction_draft = StagedRowDraft(
        entity="transaction",
        payload={
            "account_mask": mask,
            "occurred_at": occurred_at.isoformat(),
            "kind": kind,
            "amount": abs(entry.balance),
            "currency": entry.currency,
            "description": "manual account balance entry",
        },
        method=METHOD,
    )
    return [account_draft, transaction_draft]


def build_portfolio_drafts_from_yahoo(
    account: AccountInput, lots: list[YahooFinanceLot], *, currency: str
) -> list[StagedRowDraft]:
    """One `account` draft, plus one `holding` per distinct ticker
    (quantity summed, cost weighted-averaged across its lots — same
    aggregation `worker.adapters.questrade.parse` uses) and one `lot` draft
    per imported row."""
    if not lots:
        raise ManualEntryValidationError("no lots to import")

    account_draft, mask = _account_draft(account)

    grouped: dict[str, dict[str, Decimal]] = {}
    lot_drafts = []
    for lot in lots:
        ticker = lot.ticker.strip().upper()
        lot_drafts.append(
            StagedRowDraft(
                entity="lot",
                payload={
                    "account_mask": mask,
                    "ticker": ticker,
                    "quantity": lot.quantity,
                    "unit_cost": lot.unit_cost,
                    "currency": currency,
                    "acquired_at": lot.acquired_at,
                    "vested": None,
                },
                method=METHOD,
            )
        )
        bucket = grouped.setdefault(ticker, {"quantity": Decimal("0"), "cost_total": Decimal("0")})
        bucket["quantity"] += lot.quantity
        bucket["cost_total"] += lot.quantity * lot.unit_cost

    holding_drafts = [
        StagedRowDraft(
            entity="holding",
            payload={
                "account_mask": mask,
                "ticker": ticker,
                "quantity": bucket["quantity"],
                "avg_cost": (
                    bucket["cost_total"] / bucket["quantity"] if bucket["quantity"] else None
                ),
                "currency": currency,
            },
            method=METHOD,
        )
        for ticker, bucket in grouped.items()
    ]

    return [account_draft, *holding_drafts, *lot_drafts]
