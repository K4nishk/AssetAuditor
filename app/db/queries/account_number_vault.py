"""`account_number_vault` reads/writes via pgsodium column encryption (KCH-66 / AA-29).

Real account numbers never touch a bytea literal in this codebase — every
read/write goes through the two SECURITY DEFINER SQL functions migration 0004
defines (`vault_store_account_number`/`vault_reveal_account_number`), which
encrypt/decrypt inside Postgres via a single project-wide pgsodium key and
re-check `account_id` ownership against `auth.uid()` themselves (SECURITY
DEFINER bypasses RLS, so the ownership check is inline SQL in the function
body, not migration 0001's `account_number_vault_tenant_isolation` policy).
Call these on an RLS-scoped connection (`app.db.pool.rls_connection`) so
`auth.uid()` resolves to the caller — not on the worker's service_role
connection, which has no JWT claim to resolve.

No caller wires this in yet (nothing in the current adapters/parse-confirm
path ever extracts a real, unmasked account number — every adapter masks via
`worker.masking`/`normalize_account_mask` before a row is staged), same
deferred-integration convention several upstream issues already set. This
module exists so a future feature that legitimately needs the real number
(e.g. a "verify last 4 against statement" flow) has a masking-safe path
ready rather than reinventing bytea handling ad hoc.
"""

from __future__ import annotations

import asyncpg


async def store_account_number(
    conn: asyncpg.Connection, *, account_id: str, account_number: str
) -> None:
    await conn.execute(
        "select public.vault_store_account_number($1, $2)", account_id, account_number
    )


async def reveal_account_number(conn: asyncpg.Connection, *, account_id: str) -> str | None:
    return await conn.fetchval("select public.vault_reveal_account_number($1)", account_id)


__all__ = ["store_account_number", "reveal_account_number"]
