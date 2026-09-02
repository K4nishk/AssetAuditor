"""Gold-layer reads/writes for `worker.gold.rebuild_gold` (KCH-53 / AA-18).

Runs on whichever connection the caller passes — the RLS-scoped per-request
connection (`app.db.pool.rls_connection`) if triggered from the API, or the
worker's service_role connection if triggered from the job loop — every
query binds `user_id` explicitly rather than relying on either, same
convention as `app.db.queries.silver`.

Gold is fully rebuildable (`docs/vault/30-architecture/ETL-Pipeline.md`):
every write here deletes the current snapshot-date's rows for this user
before reinserting, so a bucket/cut that no longer has any value this
rebuild doesn't linger with a stale amount from a previous run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import asyncpg

from app.domain.gold import (
    AssetValuation,
    CashTransaction,
    LiabilityAmount,
    LotForValuation,
    compute_cash_balance,
    value_holding,
)
from app.domain.rooms.links import ContributionTransaction, DerivedRoomEvent

_FETCH_HOLDINGS_SQL = """
    select h.id, h.quantity, h.avg_cost, h.currency, a.account_type, a.institution
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
    select a.id as account_id, a.account_type, a.institution, a.currency, t.kind, t.amount
    from public.transactions t
    join public.accounts a on a.id = t.account_id and a.user_id = t.user_id
    where t.user_id = $1 and t.deactivated_at is null and a.deactivated_at is null
      and t.kind in ('credit', 'debit')
"""

_FETCH_LIABILITIES_SQL = """
    select balance
    from public.liabilities
    where user_id = $1 and deactivated_at is null
"""

_FETCH_CONTRIBUTION_TRANSACTIONS_SQL = """
    select t.id, a.account_type, t.occurred_at, t.amount
    from public.transactions t
    join public.accounts a on a.id = t.account_id and a.user_id = t.user_id
    where t.user_id = $1 and t.deactivated_at is null and a.deactivated_at is null
      and t.kind = 'contribution'
"""

_FETCH_ACCOUNTS_FOR_EXPORT_SQL = """
    select id, institution, account_type, masked_identifier, currency, created_at
    from public.accounts
    where user_id = $1 and deactivated_at is null
"""
_FETCH_HOLDINGS_FOR_EXPORT_SQL = """
    select id, account_id, ticker, quantity, avg_cost, currency, created_at
    from public.holdings
    where user_id = $1 and deactivated_at is null
"""
_FETCH_LOTS_FOR_EXPORT_SQL = """
    select id, holding_id, quantity, unit_cost, currency, acquired_at, vested, created_at
    from public.lots
    where user_id = $1 and deactivated_at is null
"""
_FETCH_TRANSACTIONS_FOR_EXPORT_SQL = """
    select id, account_id, holding_id, occurred_at, kind, amount, currency,
           amount_cad, fx_rate, fx_date, description, created_at
    from public.transactions
    where user_id = $1 and deactivated_at is null
"""
_FETCH_LIABILITIES_FOR_EXPORT_SQL = """
    select id, account_id, kind, balance, currency, interest_rate, created_at
    from public.liabilities
    where user_id = $1 and deactivated_at is null
"""

_DELETE_DERIVED_ROOM_EVENTS_SQL = """
    delete from public.room_events
    where user_id = $1 and kind = 'contribution' and source_ref is not null
"""
_INSERT_ROOM_EVENT_SQL = """
    insert into public.room_events (user_id, account_type, year, kind, amount, source_ref)
    values ($1, $2, $3, 'contribution', $4, $5)
"""

_DELETE_SNAPSHOT_SQL = """
    delete from public.networth_snapshots where user_id = $1 and snapshot_date = $2
"""
_INSERT_SNAPSHOT_SQL = """
    insert into public.networth_snapshots
        (user_id, snapshot_date, total_assets_cad, total_liabilities_cad, net_worth_cad, run_id)
    values ($1, $2, $3, $4, $5, $6)
"""

_DELETE_TERM_BUCKETS_SQL = """
    delete from public.term_buckets where user_id = $1 and snapshot_date = $2
"""
_INSERT_TERM_BUCKET_SQL = """
    insert into public.term_buckets (user_id, snapshot_date, bucket, amount_cad, run_id)
    values ($1, $2, $3, $4, $5)
"""

_DELETE_DIVERSIFICATION_CUTS_SQL = """
    delete from public.diversification_cuts where user_id = $1 and snapshot_date = $2
"""
_INSERT_DIVERSIFICATION_CUT_SQL = """
    insert into public.diversification_cuts (user_id, snapshot_date, cut, label, amount_cad, run_id)
    values ($1, $2, $3, $4, $5, $6)
"""


@dataclass(frozen=True)
class HoldingRow:
    id: str
    quantity: Decimal
    avg_cost: Decimal | None
    currency: str
    account_type: str
    institution: str


@dataclass(frozen=True)
class CashTransactionRow:
    account_id: str
    account_type: str
    institution: str
    currency: str
    transaction: CashTransaction


@dataclass(frozen=True)
class SilverForValuation:
    """Everything `app.domain.gold.compute_gold_totals` needs, already
    shaped into its input dataclasses."""

    holdings: list[HoldingRow]
    lots_by_holding: dict[str, list[LotForValuation]]
    cash_transactions: list[CashTransactionRow]
    liabilities: list[LiabilityAmount]


async def fetch_silver_for_valuation(
    conn: asyncpg.Connection, *, user_id: str
) -> SilverForValuation:
    holding_rows = await conn.fetch(_FETCH_HOLDINGS_SQL, user_id)
    lot_rows = await conn.fetch(_FETCH_LOTS_SQL, user_id)
    cash_rows = await conn.fetch(_FETCH_CASH_TRANSACTIONS_SQL, user_id)
    liability_rows = await conn.fetch(_FETCH_LIABILITIES_SQL, user_id)

    lots_by_holding: dict[str, list[LotForValuation]] = {}
    for row in lot_rows:
        lot = LotForValuation(
            quantity=row["quantity"], unit_cost=row["unit_cost"], vested=row["vested"]
        )
        lots_by_holding.setdefault(str(row["holding_id"]), []).append(lot)

    holdings = [
        HoldingRow(
            id=str(row["id"]),
            quantity=row["quantity"],
            avg_cost=row["avg_cost"],
            currency=row["currency"],
            account_type=row["account_type"],
            institution=row["institution"],
        )
        for row in holding_rows
    ]

    cash_transactions = [
        CashTransactionRow(
            account_id=str(row["account_id"]),
            account_type=row["account_type"],
            institution=row["institution"],
            currency=row["currency"],
            transaction=CashTransaction(kind=row["kind"], amount=row["amount"]),
        )
        for row in cash_rows
    ]

    liabilities = [LiabilityAmount(balance_cad=row["balance"]) for row in liability_rows]

    return SilverForValuation(
        holdings=holdings,
        lots_by_holding=lots_by_holding,
        cash_transactions=cash_transactions,
        liabilities=liabilities,
    )


def cash_balances_by_account(cash_transactions: list[CashTransactionRow]) -> list[AssetValuation]:
    """Group per-account cash transactions and net them into one
    `AssetValuation` per account (`app.domain.gold.compute_cash_balance`)."""
    grouped: dict[str, tuple[str, str, str, list[CashTransaction]]] = {}
    for row in cash_transactions:
        _, _, _, txns = grouped.setdefault(
            row.account_id, (row.account_type, row.institution, row.currency, [])
        )
        txns.append(row.transaction)

    return [
        AssetValuation(
            account_type=account_type,
            institution=institution,
            currency=currency,
            amount_cad=compute_cash_balance(txns),
        )
        for account_type, institution, currency, txns in grouped.values()
    ]


def holding_valuations(silver: SilverForValuation) -> list[AssetValuation]:
    """Value every fetched holding (`app.domain.gold.value_holding`) and pair
    it with its account's type/institution for bucket/cut classification."""
    return [
        AssetValuation(
            account_type=holding.account_type,
            institution=holding.institution,
            currency=holding.currency,
            amount_cad=value_holding(
                holding.quantity, holding.avg_cost, silver.lots_by_holding.get(holding.id, [])
            ),
        )
        for holding in silver.holdings
    ]


async def fetch_contribution_transactions(
    conn: asyncpg.Connection, *, user_id: str
) -> list[ContributionTransaction]:
    rows = await conn.fetch(_FETCH_CONTRIBUTION_TRANSACTIONS_SQL, user_id)
    return [
        ContributionTransaction(
            transaction_id=str(row["id"]),
            account_type=row["account_type"],
            occurred_at=row["occurred_at"],
            amount=row["amount"],
        )
        for row in rows
    ]


async def replace_derived_room_events(
    conn: asyncpg.Connection, *, user_id: str, events: list[DerivedRoomEvent]
) -> int:
    await conn.execute(_DELETE_DERIVED_ROOM_EVENTS_SQL, user_id)
    for event in events:
        await conn.execute(
            _INSERT_ROOM_EVENT_SQL,
            user_id,
            event.account_type,
            event.year,
            event.amount,
            event.source_ref,
        )
    return len(events)


async def write_gold_snapshot(
    conn: asyncpg.Connection,
    *,
    user_id: str,
    snapshot_date: date,
    run_id: str,
    total_assets_cad: Decimal,
    total_liabilities_cad: Decimal,
    net_worth_cad: Decimal,
    term_buckets: dict[str, Decimal],
    diversification_cuts: dict[tuple[str, str], Decimal],
) -> None:
    await conn.execute(_DELETE_SNAPSHOT_SQL, user_id, snapshot_date)
    await conn.execute(
        _INSERT_SNAPSHOT_SQL,
        user_id,
        snapshot_date,
        total_assets_cad,
        total_liabilities_cad,
        net_worth_cad,
        run_id,
    )

    await conn.execute(_DELETE_TERM_BUCKETS_SQL, user_id, snapshot_date)
    for bucket, amount in term_buckets.items():
        await conn.execute(
            _INSERT_TERM_BUCKET_SQL, user_id, snapshot_date, bucket, amount, run_id
        )

    await conn.execute(_DELETE_DIVERSIFICATION_CUTS_SQL, user_id, snapshot_date)
    for (cut, label), amount in diversification_cuts.items():
        await conn.execute(
            _INSERT_DIVERSIFICATION_CUT_SQL, user_id, snapshot_date, cut, label, amount, run_id
        )


@dataclass(frozen=True)
class SilverEntityRows:
    """Full current-state dump of every silver table, for
    `worker.gold.write_silver_parquet` — one list of records per entity."""

    accounts: list[asyncpg.Record]
    holdings: list[asyncpg.Record]
    lots: list[asyncpg.Record]
    transactions: list[asyncpg.Record]
    liabilities: list[asyncpg.Record]


async def fetch_silver_entity_rows(
    conn: asyncpg.Connection, *, user_id: str
) -> SilverEntityRows:
    return SilverEntityRows(
        accounts=await conn.fetch(_FETCH_ACCOUNTS_FOR_EXPORT_SQL, user_id),
        holdings=await conn.fetch(_FETCH_HOLDINGS_FOR_EXPORT_SQL, user_id),
        lots=await conn.fetch(_FETCH_LOTS_FOR_EXPORT_SQL, user_id),
        transactions=await conn.fetch(_FETCH_TRANSACTIONS_FOR_EXPORT_SQL, user_id),
        liabilities=await conn.fetch(_FETCH_LIABILITIES_FOR_EXPORT_SQL, user_id),
    )


__all__ = [
    "SilverForValuation",
    "SilverEntityRows",
    "fetch_silver_for_valuation",
    "cash_balances_by_account",
    "holding_valuations",
    "fetch_contribution_transactions",
    "replace_derived_room_events",
    "write_gold_snapshot",
    "fetch_silver_entity_rows",
]
