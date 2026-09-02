"""`public.prices` reads/writes (KCH-58 / AA-21).

`prices` is shared reference data, not a user table (migration 0001: RLS is
on only because Supabase exposes every public table by default, its one
policy is a read-only `using (true)` for `authenticated`). Reads here work
on either connection kind (RLS-scoped or service_role); `upsert_price` only
ever succeeds on the worker's service_role connection — no insert/update
policy exists for `authenticated`, same write/read split as
`app.uploads.blob.BlobStorage` vs. the RLS-scoped download path.

Both prices and FX rates live in the same table and row shape: `worker
.prices.refresh_prices` writes FX rows under `app.domain.prices
.fx_symbol_for_currency(currency)` as their `ticker`, so a caller reading a
currency's CAD rate calls `latest_price_on_or_before` exactly the same way
it would for an equity ticker.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import asyncpg

_LATEST_PRICE_ON_OR_BEFORE_SQL = """
    select ticker, date, close, source, created_at
    from public.prices
    where ticker = $1 and date <= $2
    order by date desc
    limit 1
"""

_UPSERT_PRICE_SQL = """
    insert into public.prices (ticker, date, close, source)
    values ($1, $2, $3, $4)
    on conflict (ticker, date, source) do update
        set close = excluded.close
"""


async def latest_price_on_or_before(
    conn: asyncpg.Connection, *, ticker: str, as_of: date
) -> asyncpg.Record | None:
    """Most recent `prices` row for `ticker` at or before `as_of` (any
    source) — end-of-day data means "today's" price may not exist yet
    depending on refresh timing, so callers always look backward rather than
    requiring an exact-date match."""
    return await conn.fetchrow(_LATEST_PRICE_ON_OR_BEFORE_SQL, ticker, as_of)


async def upsert_price(
    conn: asyncpg.Connection,
    *,
    ticker: str,
    price_date: date,
    close: Decimal,
    source: str,
) -> None:
    await conn.execute(_UPSERT_PRICE_SQL, ticker, price_date, close, source)


__all__ = ["latest_price_on_or_before", "upsert_price"]
