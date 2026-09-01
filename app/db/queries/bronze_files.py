"""`bronze_files` reads/writes for the upload path (AA-11).

Every query here runs on a connection from `app.db.pool.rls_connection`, so
Postgres's own RLS policy (migration 0001) is what actually enforces the
tenant boundary — these wrappers only need to bind `user_id` as a normal
parameter, never build it into a WHERE clause by hand.
"""

from __future__ import annotations

import asyncpg

# A purged bronze row (AA-19's sweeper) no longer has real bytes behind its
# `blob_url` — excluding it from dedupe means a re-upload after purge lands a
# fresh blob instead of being told "duplicate" for content that's gone.
_FIND_BY_SHA256_SQL = """
    select id, user_id, sha256, institution, period, blob_url, created_at
    from public.bronze_files
    where user_id = $1 and sha256 = $2 and purged_at is null
"""

_INSERT_SQL = """
    insert into public.bronze_files (user_id, sha256, institution, period, blob_url)
    values ($1, $2, $3, $4, $5)
    on conflict (user_id, sha256) do nothing
    returning id, user_id, sha256, institution, period, blob_url, created_at
"""


async def find_by_sha256(
    conn: asyncpg.Connection, *, user_id: str, sha256_hex: str
) -> asyncpg.Record | None:
    return await conn.fetchrow(_FIND_BY_SHA256_SQL, user_id, sha256_hex)


async def insert_bronze_file(
    conn: asyncpg.Connection,
    *,
    user_id: str,
    sha256_hex: str,
    institution: str | None,
    period: str | None,
    blob_url: str,
) -> asyncpg.Record | None:
    """Insert a bronze row; returns `None` if a concurrent request won the race.

    `unique (user_id, sha256)` (migration 0001) is the actual dedupe
    guarantee — this is `ON CONFLICT DO NOTHING` rather than a prior
    `find_by_sha256` check-then-insert so two near-simultaneous uploads of the
    same file can never both succeed, closing the TOCTOU gap a plain
    check-then-insert would leave open.
    """
    return await conn.fetchrow(
        _INSERT_SQL, user_id, sha256_hex, institution, period, blob_url
    )
