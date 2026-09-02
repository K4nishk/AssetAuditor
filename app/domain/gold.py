"""Pure net-worth / term-bucket / diversification-cut math (KCH-53 / AA-18).

No I/O here — `worker.gold.rebuild_gold` fetches silver rows
(`app.db.queries.gold`) and turns them into the plain dataclasses below,
this module does the arithmetic, and the caller writes the result back.
Same domain/IO split as `app.domain.rooms.engine` / `app.db.queries.silver`.

**Known MVP limitation, not something this module invents**: there is no FX
or price layer yet (`AA-21 Price layer` depends on *this* issue, not the
other way around — `mvp.md`'s dependency spine), so every amount here is
taken at face value in whatever currency the silver row already carries
(`holdings.avg_cost`, `liabilities.balance`, ...) with no CAD conversion.
Net worth and diversification-by-currency are therefore approximate for any
non-CAD holding until AA-21 lands; that reconciliation is AA-22's dashboard
concern (`mvp.md`: "Numbers must match data/samples/README.md reference
totals", deps AA-18 **and** AA-21), not this one's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.domain.buckets import LIABILITY_BUCKET, TermBucket, classify_term_bucket

ZERO = Decimal("0")


@dataclass(frozen=True)
class LotForValuation:
    """A `lots` row, just the fields valuation needs."""

    quantity: Decimal
    unit_cost: Decimal | None
    vested: bool | None


@dataclass(frozen=True)
class AssetValuation:
    """One valued asset — a holding or a cash-like account balance — already
    resolved to a single CAD-face-value amount (see module limitation note)."""

    account_type: str
    institution: str
    currency: str
    amount_cad: Decimal


@dataclass(frozen=True)
class CashTransaction:
    """A `credit`/`debit` transaction feeding a cash-like account's balance."""

    kind: str
    amount: Decimal


@dataclass(frozen=True)
class LiabilityAmount:
    balance_cad: Decimal


@dataclass(frozen=True)
class GoldTotals:
    total_assets_cad: Decimal
    total_liabilities_cad: Decimal
    net_worth_cad: Decimal
    term_buckets: dict[str, Decimal] = field(default_factory=dict)
    # keyed by (cut, label) -> amount, e.g. ("institution", "questrade") -> Decimal
    diversification_cuts: dict[tuple[str, str], Decimal] = field(default_factory=dict)


def value_holding(
    quantity: Decimal, avg_cost: Decimal | None, lots: list[LotForValuation]
) -> Decimal:
    """Value one holding from its lots when lots exist, else `quantity * avg_cost`.

    `vested is False` lots are excluded (ESOP unvested tranches per
    `skills/e2e-testing/SKILL.md`'s net-worth TODO); `vested is None` lots
    (every non-ESOP adapter's per-fill lots) count normally. A lot missing
    `unit_cost` is dropped from the per-lot sum rather than treated as free.
    """
    countable = [
        (lot.quantity, lot.unit_cost)
        for lot in lots
        if lot.vested is not False and lot.unit_cost is not None
    ]
    if lots:
        # Lots are the record once they exist: a holding whose lots are all
        # unvested (or all missing unit_cost) is worth zero here, it must not
        # fall back to the full-quantity avg_cost figure.
        return sum((qty * unit_cost for qty, unit_cost in countable), ZERO)
    if avg_cost is None:
        return ZERO
    return quantity * avg_cost


def compute_cash_balance(transactions: list[CashTransaction]) -> Decimal:
    """Net signed balance of a cash-like account's `credit`/`debit` transactions.

    This is a *net-movement* total, not a reconstructed statement balance —
    the silver `transactions` table has no opening-balance/running-balance
    column (a statement's own `balance_after` column is not persisted by
    `app.db.queries.silver.write_confirmed_rows`), so a fixture missing its
    account's true opening balance will under- or over-state this figure.
    Documented MVP gap, not this function's bug.
    """
    total = ZERO
    for txn in transactions:
        if txn.kind == "credit":
            total += txn.amount
        elif txn.kind == "debit":
            total -= txn.amount
        else:
            raise ValueError(f"unexpected cash transaction kind: {txn.kind!r}")
    return total


def compute_gold_totals(
    assets: list[AssetValuation], liabilities: list[LiabilityAmount]
) -> GoldTotals:
    total_assets = sum((asset.amount_cad for asset in assets), ZERO)
    total_liabilities = sum((liability.balance_cad for liability in liabilities), ZERO)

    term_buckets: dict[str, Decimal] = {}
    for asset in assets:
        bucket: TermBucket = classify_term_bucket(asset.account_type)
        term_buckets[bucket] = term_buckets.get(bucket, ZERO) + asset.amount_cad
    if liabilities:
        term_buckets[LIABILITY_BUCKET] = total_liabilities

    cuts: dict[tuple[str, str], Decimal] = {}
    for asset in assets:
        for cut, label in (
            ("institution", asset.institution),
            ("account_type", asset.account_type),
            ("currency", asset.currency),
        ):
            key = (cut, label)
            cuts[key] = cuts.get(key, ZERO) + asset.amount_cad

    return GoldTotals(
        total_assets_cad=total_assets,
        total_liabilities_cad=total_liabilities,
        net_worth_cad=total_assets - total_liabilities,
        term_buckets=term_buckets,
        diversification_cuts=cuts,
    )
