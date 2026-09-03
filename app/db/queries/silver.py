"""Silver-write resolver: confirmed `staged_rows` -> accounts/holdings/lots/
transactions/liabilities (KCH-52 / AA-17's "confirm-all -> silver" step).

Adapters (AA-14/15/16) stage every cross-entity reference as a natural key
(`account_mask`, `ticker`) rather than a UUID, since no silver row IDs exist
yet at staging time (`worker/adapters/base.py`'s module docstring). This is
where those natural keys finally resolve to real foreign keys, at the moment
the user confirms.

`accounts` is find-or-create by (`user_id`, `masked_identifier`): it's an
identity register, and re-uploading a later statement for the same account
must not mint a second `accounts` row. `holdings`/`lots`/`transactions`/
`liabilities` are plain inserts on every confirm —
docs/vault/30-architecture/ETL-Pipeline.md's "silver is append-only" call.
Deduping/superseding an earlier upload's holdings snapshot against a later
one (`superseded_by_run`) is explicitly still-open design work with no schema
column for it yet (migration 0001 doesn't have one) — not something this
resolver invents on its own; current-state aggregation is AA-18's
`rebuild_gold` job.
"""

from __future__ import annotations

from typing import Any

import asyncpg

from app.domain.staged_rows import decode_payload
from worker.adapters.base import to_date, to_datetime_utc, to_decimal

_ENTITIES = ("account", "holding", "lot", "transaction", "liability")

_FIND_ACCOUNTS_SQL = """
    select masked_identifier, id from public.accounts where user_id = $1
"""
_INSERT_ACCOUNT_SQL = """
    insert into public.accounts (user_id, institution, account_type, masked_identifier, currency)
    values ($1, $2, $3, $4, $5)
    returning id
"""
_INSERT_HOLDING_SQL = """
    insert into public.holdings (user_id, account_id, ticker, quantity, avg_cost, currency, mer_pct)
    values ($1, $2, $3, $4, $5, $6, $7)
    returning id
"""
_INSERT_LOT_SQL = """
    insert into public.lots
        (user_id, holding_id, quantity, unit_cost, currency, acquired_at, vested)
    values ($1, $2, $3, $4, $5, $6, $7)
"""
_INSERT_TRANSACTION_SQL = """
    insert into public.transactions
        (user_id, account_id, holding_id, occurred_at, kind, amount, currency, description)
    values ($1, $2, $3, $4, $5, $6, $7, $8)
"""
_INSERT_LIABILITY_SQL = """
    insert into public.liabilities (user_id, account_id, kind, balance, currency, interest_rate)
    values ($1, $2, $3, $4, $5, $6)
"""


class SilverResolutionError(ValueError):
    """A staged row referenced an `account_mask`/`ticker` that never resolved
    to an existing or newly-confirmed silver row, or was missing a field this
    resolver needs — the confirm request is rejected whole (422) rather than
    writing a partial, dangling silver state."""


async def write_confirmed_rows(
    conn: asyncpg.Connection, *, user_id: str, rows: list[asyncpg.Record]
) -> dict[str, int]:
    """Write every confirmed `staged_rows` row to its silver table.

    Runs inside the caller's transaction (`app.db.pool.rls_connection`) — a
    `SilverResolutionError` propagating out rolls the whole confirm back, so a
    row that can't resolve never leaves a half-written batch behind.
    """
    by_entity: dict[str, list[dict[str, Any]]] = {entity: [] for entity in _ENTITIES}
    for row in rows:
        by_entity[row["entity"]].append(decode_payload(row["payload"]))

    summary = dict.fromkeys(_ENTITIES, 0)

    account_by_mask = await _preload_accounts(conn, user_id=user_id)
    for payload in by_entity["account"]:
        mask = _require(payload, "masked_identifier", entity="account")
        if mask in account_by_mask:
            continue
        account_id = await conn.fetchval(
            _INSERT_ACCOUNT_SQL,
            user_id,
            _require(payload, "institution", entity="account"),
            _require(payload, "account_type", entity="account"),
            mask,
            payload.get("currency") or "CAD",
        )
        account_by_mask[mask] = account_id
        summary["account"] += 1

    holding_by_key: dict[tuple[str, str], Any] = {}
    for payload in by_entity["holding"]:
        mask = _require(payload, "account_mask", entity="holding")
        ticker = _require(payload, "ticker", entity="holding")
        account_id = _resolve_account(account_by_mask, mask, entity="holding")
        holding_id = await conn.fetchval(
            _INSERT_HOLDING_SQL,
            user_id,
            account_id,
            ticker,
            to_decimal(_require(payload, "quantity", entity="holding")),
            to_decimal(payload.get("avg_cost")),
            payload.get("currency") or "CAD",
            to_decimal(payload.get("mer_pct")),
        )
        holding_by_key[(mask, ticker)] = holding_id
        summary["holding"] += 1

    for payload in by_entity["lot"]:
        mask = _require(payload, "account_mask", entity="lot")
        ticker = _require(payload, "ticker", entity="lot")
        holding_id = holding_by_key.get((mask, ticker))
        if holding_id is None:
            raise SilverResolutionError(
                f"lot for account_mask={mask!r} ticker={ticker!r} has no matching "
                "confirmed holding in this batch"
            )
        acquired_at = payload.get("acquired_at")
        await conn.execute(
            _INSERT_LOT_SQL,
            user_id,
            holding_id,
            to_decimal(_require(payload, "quantity", entity="lot")),
            to_decimal(payload.get("unit_cost")),
            payload.get("currency") or "CAD",
            to_date(acquired_at) if acquired_at else None,
            payload.get("vested"),
        )
        summary["lot"] += 1

    for payload in by_entity["transaction"]:
        mask = _require(payload, "account_mask", entity="transaction")
        account_id = _resolve_account(account_by_mask, mask, entity="transaction")
        ticker = payload.get("ticker")
        holding_id = None
        if ticker:
            holding_id = holding_by_key.get((mask, ticker))
            if holding_id is None:
                raise SilverResolutionError(
                    f"transaction for account_mask={mask!r} ticker={ticker!r} has no "
                    "matching confirmed holding in this batch"
                )
        await conn.execute(
            _INSERT_TRANSACTION_SQL,
            user_id,
            account_id,
            holding_id,
            to_datetime_utc(_require(payload, "occurred_at", entity="transaction")),
            _require(payload, "kind", entity="transaction"),
            to_decimal(_require(payload, "amount", entity="transaction")),
            _require(payload, "currency", entity="transaction"),
            payload.get("description"),
        )
        summary["transaction"] += 1

    for payload in by_entity["liability"]:
        mask = payload.get("account_mask")
        account_id = account_by_mask.get(mask) if mask else None
        await conn.execute(
            _INSERT_LIABILITY_SQL,
            user_id,
            account_id,
            _require(payload, "kind", entity="liability"),
            to_decimal(_require(payload, "balance", entity="liability")),
            payload.get("currency") or "CAD",
            to_decimal(payload.get("interest_rate")),
        )
        summary["liability"] += 1

    return summary


async def _preload_accounts(conn: asyncpg.Connection, *, user_id: str) -> dict[str, Any]:
    rows = await conn.fetch(_FIND_ACCOUNTS_SQL, user_id)
    return {row["masked_identifier"]: row["id"] for row in rows}


def _resolve_account(account_by_mask: dict[str, Any], mask: str, *, entity: str) -> Any:
    account_id = account_by_mask.get(mask)
    if account_id is None:
        raise SilverResolutionError(
            f"{entity} references account_mask={mask!r} with no matching account row "
            "in this batch or on file"
        )
    return account_id


def _require(payload: dict[str, Any], field: str, *, entity: str) -> Any:
    value = payload.get(field)
    if value is None or value == "":
        raise SilverResolutionError(f"{entity} row missing required field {field!r}")
    return value
