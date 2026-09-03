"""Read query for AA-26's fee-drag comparison — holdings carrying a disclosed
MER (KCH-63).

Standalone from `app.db.queries.gold`/`app.db.queries.diversification`'s
holding fetches (same "don't widen a settled contract for one extra column"
reasoning `app.db.queries.diversification`'s own docstring already gives):
this only needs `ticker`/`mer_pct`, keyed by ticker so the route can pair it
with `app.db.queries.diversification.fetch_portfolio_holdings`'s
CAD-reconciled market values without either module needing to know about
the other's full row shape.
"""

from __future__ import annotations

from decimal import Decimal

import asyncpg

_FETCH_MER_SQL = """
    select h.ticker, h.mer_pct
    from public.holdings h
    join public.accounts a on a.id = h.account_id and a.user_id = h.user_id
    where h.user_id = $1 and h.deactivated_at is null and a.deactivated_at is null
      and h.mer_pct is not null
"""


async def fetch_mer_by_ticker(conn: asyncpg.Connection, *, user_id: str) -> dict[str, Decimal]:
    """Every current holding's disclosed MER, keyed by ticker.

    A ticker held at more than one institution keeps whichever row the query
    returns last — this MVP has no notion of "the same fund's MER differs by
    account", and CLAUDE.md's provenance rule is about never fabricating a
    number, not about resolving that edge case.
    """
    rows = await conn.fetch(_FETCH_MER_SQL, user_id)
    return {row["ticker"]: row["mer_pct"] for row in rows}


__all__ = ["fetch_mer_by_ticker"]
