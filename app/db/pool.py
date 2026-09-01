"""asyncpg pool + RLS-scoped role (KCH-41 / AA-6).

Migration 0001's RLS policies (`app/db/migrations/0001_init.sql`) all key off
`auth.uid()`, which resolves from the Postgres session setting
`request.jwt.claim.sub`. A pooled connection is reused across unrelated
requests, so both the `authenticated` role and that setting must be scoped
with `SET LOCAL` / `set_config(..., is_local=true)` inside one transaction —
never a session-wide `SET` — or one user's identity could leak onto the next
request that happens to reuse the same physical connection.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    return _pool


async def close_pool() -> None:
    """Close and drop the process-wide pool singleton. Test-only / shutdown hook."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def rls_connection(user_id: str) -> AsyncIterator[asyncpg.Connection]:
    """Yield a connection impersonating `authenticated`, scoped to `user_id`.

    The whole block runs inside one transaction so the role switch and the
    `request.jwt.claim.sub` setting are guaranteed to revert when the
    connection is released back to the pool, whatever the outcome.
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("set local role authenticated")
        await conn.execute("select set_config('request.jwt.claim.sub', $1, true)", user_id)
        yield conn
