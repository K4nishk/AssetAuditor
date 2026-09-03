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
    order by h.ticker, h.id
"""


async def fetch_mer_by_ticker(conn: asyncpg.Connection, *, user_id: str) -> dict[str, Decimal]:
    """Every current holding's disclosed MER, keyed by ticker.

    A ticker held at more than one institution is expected to carry the same
    disclosed MER everywhere — it's a per-fund rate, not a per-account one.
    If two rows for the same ticker actually disagree, there is no way to
    know which one applies, so that ticker is dropped rather than resolved
    by an arbitrary pick (`order by h.id` only makes the fetch itself
    deterministic; it does not decide which of two conflicting real MERs is
    correct). Same never-guessed-only-known posture as
    `app.domain.etf_classification`: excluded, not fabricated.
    """
    rows = await conn.fetch(_FETCH_MER_SQL, user_id)
    mer_by_ticker: dict[str, Decimal] = {}
    conflicting: set[str] = set()
    for row in rows:
        ticker, mer_pct = row["ticker"], row["mer_pct"]
        if ticker in mer_by_ticker and mer_by_ticker[ticker] != mer_pct:
            conflicting.add(ticker)
            continue
        mer_by_ticker[ticker] = mer_pct
    for ticker in conflicting:
        del mer_by_ticker[ticker]
    return mer_by_ticker


__all__ = ["fetch_mer_by_ticker"]
