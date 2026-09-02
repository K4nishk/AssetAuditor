"""Deactivate/reactivate + hard-purge row queries (KCH-45 / AA-10).

Every query here runs on an RLS-scoped connection (`app.db.pool.rls_connection`),
same convention as `app.db.queries.users_profile`/`bronze_files`/`etl_jobs` —
Postgres's own RLS policy (migration 0001) enforces the tenant boundary, and
because `DELETE /account` is the user deleting their *own* data (not an
admin/service-role sweep like `worker.retention`), the RLS-scoped connection
is the right one here, not `WORKER_DATABASE_URL`.

Purge order relies on migration 0001's FK cascades instead of re-deleting
every table by hand:
  - deleting `accounts` cascades `account_number_vault`, `holdings` (which
    cascades `lots`), and `transactions`.
  - deleting `bronze_files` cascades `etl_jobs` (which cascades `staged_rows`).
`liabilities`, `room_events`, `networth_snapshots`, `term_buckets`,
`diversification_cuts`, and `users_profile` FK straight to `auth.users` with
no child rows of their own, so each needs its own explicit delete.

`lineage_events` is deliberately never deleted here. Migration 0001's FK from
`etl_jobs` is `on delete set null`, so a lineage row survives its job being
purged with `job_id` nulled out — `redact_lineage_events` then scrubs the
row's own `payload`/`facets` down to a `{"redacted": true, "sha256": ...}`
stub in place, using pgcrypto's `digest()` (already enabled by migration
0001) so the hash is computed server-side rather than round-tripping the
original PII-bearing payload back into this process just to hash it. This is
`Data-Retention-and-Privacy.md`'s "lineage payloads (hashes may remain)" —
the same tombstone philosophy `worker.retention`'s bronze sweep already uses
for purged blobs, applied to the lineage table for a full account purge.
`keep_run_id` excludes the purge's own START/COMPLETE events so the audit
trail of the deletion itself survives unredacted.
"""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg

_DEACTIVATE_SQL = """
    update public.users_profile
    set deactivated_at = now()
    where id = $1 and deactivated_at is null
    returning id, deactivated_at
"""

_REACTIVATE_SQL = """
    update public.users_profile
    set deactivated_at = null
    where id = $1 and deactivated_at is not null
    returning id, deactivated_at
"""

_SELECT_DEACTIVATION_STATUS_SQL = """
    select id, deactivated_at
    from public.users_profile
    where id = $1
"""

_SELECT_UNPURGED_BRONZE_BLOB_URLS_SQL = """
    select blob_url
    from public.bronze_files
    where user_id = $1 and purged_at is null and blob_url != ''
"""

_REDACT_LINEAGE_EVENTS_SQL = """
    update public.lineage_events
    set payload = jsonb_build_object(
            'redacted', true, 'sha256', encode(digest(payload::text, 'sha256'), 'hex')
        ),
        facets = jsonb_build_object(
            'redacted', true, 'sha256', encode(digest(facets::text, 'sha256'), 'hex')
        )
    where user_id = $1
        and run_id != $2
        and coalesce(facets->>'redacted', 'false') != 'true'
    returning id
"""

# accounts cascades: account_number_vault, holdings -> lots, transactions.
_DELETE_ACCOUNTS_SQL = "delete from public.accounts where user_id = $1 returning id"
# bronze_files cascades: etl_jobs -> staged_rows.
_DELETE_BRONZE_FILES_SQL = "delete from public.bronze_files where user_id = $1 returning id"
_DELETE_LIABILITIES_SQL = "delete from public.liabilities where user_id = $1 returning id"
_DELETE_ROOM_EVENTS_SQL = "delete from public.room_events where user_id = $1 returning id"
_DELETE_NETWORTH_SNAPSHOTS_SQL = (
    "delete from public.networth_snapshots where user_id = $1 returning id"
)
_DELETE_TERM_BUCKETS_SQL = "delete from public.term_buckets where user_id = $1 returning id"
_DELETE_DIVERSIFICATION_CUTS_SQL = (
    "delete from public.diversification_cuts where user_id = $1 returning id"
)
_DELETE_USERS_PROFILE_SQL = "delete from public.users_profile where id = $1 returning id"


async def deactivate_account(conn: asyncpg.Connection, *, user_id: str) -> asyncpg.Record | None:
    """Set `users_profile.deactivated_at`. `None` if already deactivated (idempotent no-op)."""
    return await conn.fetchrow(_DEACTIVATE_SQL, user_id)


async def reactivate_account(conn: asyncpg.Connection, *, user_id: str) -> asyncpg.Record | None:
    """Clear `users_profile.deactivated_at`. `None` if already active (idempotent no-op)."""
    return await conn.fetchrow(_REACTIVATE_SQL, user_id)


async def get_deactivation_status(
    conn: asyncpg.Connection, *, user_id: str
) -> asyncpg.Record | None:
    """Unlike `app.db.queries.users_profile.get_profile`, does not filter out
    deactivated rows — the deactivate/reactivate routes use this to tell "no
    profile at all" (404) apart from "already in the requested state"
    (idempotent success) after a guarded update matches zero rows."""
    return await conn.fetchrow(_SELECT_DEACTIVATION_STATUS_SQL, user_id)


async def unpurged_bronze_blob_urls(conn: asyncpg.Connection, *, user_id: str) -> list[str]:
    """Blob URLs the retention sweeper hasn't already tombstoned — capture these
    *before* `purge_user_rows` deletes the `bronze_files` rows that name them."""
    rows = await conn.fetch(_SELECT_UNPURGED_BRONZE_BLOB_URLS_SQL, user_id)
    return [row["blob_url"] for row in rows]


async def redact_lineage_events(
    conn: asyncpg.Connection, *, user_id: str, keep_run_id: str
) -> int:
    rows = await conn.fetch(_REDACT_LINEAGE_EVENTS_SQL, user_id, keep_run_id)
    return len(rows)


@dataclass(frozen=True)
class PurgeRowCounts:
    accounts: int
    bronze_files: int
    liabilities: int
    room_events: int
    networth_snapshots: int
    term_buckets: int
    diversification_cuts: int
    profile: int


async def purge_user_rows(conn: asyncpg.Connection, *, user_id: str) -> PurgeRowCounts:
    """Delete every row this user owns except `lineage_events` (redacted, not
    deleted — see module docstring). Call `unpurged_bronze_blob_urls` first;
    once `_DELETE_BRONZE_FILES_SQL` runs those rows (and their `blob_url`s)
    are gone."""
    accounts = await conn.fetch(_DELETE_ACCOUNTS_SQL, user_id)
    bronze_files = await conn.fetch(_DELETE_BRONZE_FILES_SQL, user_id)
    liabilities = await conn.fetch(_DELETE_LIABILITIES_SQL, user_id)
    room_events = await conn.fetch(_DELETE_ROOM_EVENTS_SQL, user_id)
    networth_snapshots = await conn.fetch(_DELETE_NETWORTH_SNAPSHOTS_SQL, user_id)
    term_buckets = await conn.fetch(_DELETE_TERM_BUCKETS_SQL, user_id)
    diversification_cuts = await conn.fetch(_DELETE_DIVERSIFICATION_CUTS_SQL, user_id)
    profile = await conn.fetch(_DELETE_USERS_PROFILE_SQL, user_id)
    return PurgeRowCounts(
        accounts=len(accounts),
        bronze_files=len(bronze_files),
        liabilities=len(liabilities),
        room_events=len(room_events),
        networth_snapshots=len(networth_snapshots),
        term_buckets=len(term_buckets),
        diversification_cuts=len(diversification_cuts),
        profile=len(profile),
    )
