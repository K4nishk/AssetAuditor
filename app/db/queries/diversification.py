"""Portfolio-holding reads for diversification flags (KCH-61 / AA-24).

Deliberately standalone from `app.db.queries.gold` (AA-18/AA-22's already-
published contract) even though the SQL overlaps: this module additionally
needs each holding's `ticker` (for `app.domain.etf_classification`'s
factsheet look-through), which AA-18's `HoldingRow`/`AssetValuation` shapes
don't carry — rather than widen that settled contract for one downstream
consumer, this keeps its own small fetch + FX-reconcile path, same
CAD-reconciliation idea `worker.gold.rebuild_gold` uses (AA-21's
`public.prices`), applied live against current silver rather than the gold
snapshot tables.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import asyncpg

from app.db.queries import prices as prices_queries
from app.domain.diversification_flags import PortfolioHolding
from app.domain.gold import LotForValuation, value_holding
from app.domain.prices import CAD, convert_to_cad, fx_symbol_for_currency

_FETCH_HOLDINGS_SQL = """
    select h.id, h.ticker, h.quantity, h.avg_cost, h.currency, a.institution
    from public.holdings h
    join public.accounts a on a.id = h.account_id and a.user_id = h.user_id
    where h.user_id = $1 and h.deactivated_at is null and a.deactivated_at is null
"""

_FETCH_LOTS_SQL = """
    select holding_id, quantity, unit_cost, vested
    from public.lots
    where user_id = $1 and deactivated_at is null
"""

_FETCH_CASH_TRANSACTIONS_SQL = """
    select a.id as account_id, a.institution, t.kind, t.amount, t.currency
    from public.transactions t
    join public.accounts a on a.id = t.account_id and a.user_id = t.user_id
    where t.user_id = $1 and t.deactivated_at is null and a.deactivated_at is null
      and t.kind in ('credit', 'debit')
"""


async def fetch_portfolio_holdings(
    conn: asyncpg.Connection, *, user_id: str, as_of: date
) -> list[PortfolioHolding]:
    """Every current holding (with `ticker`) plus each cash-like account's
    net balance, all CAD-reconciled via AA-21's `public.prices` FX rows.

    Raises `app.domain.prices.MissingFxRateError` (not caught here, same as
    `worker.gold.rebuild_gold`) if a non-CAD line has no matching FX rate —
    callers must not silently sum face value into a CAD total.
    """
    holding_rows = await conn.fetch(_FETCH_HOLDINGS_SQL, user_id)
    lot_rows = await conn.fetch(_FETCH_LOTS_SQL, user_id)
    cash_rows = await conn.fetch(_FETCH_CASH_TRANSACTIONS_SQL, user_id)

    lots_by_holding: dict[str, list[LotForValuation]] = {}
    for row in lot_rows:
        lots_by_holding.setdefault(str(row["holding_id"]), []).append(
            LotForValuation(
                quantity=row["quantity"], unit_cost=row["unit_cost"], vested=row["vested"]
            )
        )

    # (ticker, institution, currency, face-value amount) — cash lines carry ticker=None.
    face_values: list[tuple[str | None, str, str, Decimal]] = []
    for row in holding_rows:
        amount = value_holding(
            row["quantity"], row["avg_cost"], lots_by_holding.get(str(row["id"]), [])
        )
        face_values.append((row["ticker"], row["institution"], row["currency"], amount))

    cash_totals: dict[tuple[str, str], Decimal] = {}
    for row in cash_rows:
        key = (row["institution"], row["currency"])
        signed = row["amount"] if row["kind"] == "credit" else -row["amount"]
        cash_totals[key] = cash_totals.get(key, Decimal("0")) + signed
    for (institution, currency), amount in cash_totals.items():
        face_values.append((None, institution, currency, amount))

    currencies = {currency.strip().upper() for _, _, currency, _ in face_values} - {CAD}
    fx_rates: dict[str, Decimal] = {}
    for currency in currencies:
        rate_row = await prices_queries.latest_price_on_or_before(
            conn, ticker=fx_symbol_for_currency(currency), as_of=as_of
        )
        if rate_row is not None:
            fx_rates[currency] = rate_row["close"]

    return [
        PortfolioHolding(
            ticker=ticker,
            institution=institution,
            amount_cad=convert_to_cad(
                amount, currency=currency, fx_rate=fx_rates.get(currency.strip().upper())
            ),
        )
        for ticker, institution, currency, amount in face_values
    ]


__all__ = ["fetch_portfolio_holdings"]
