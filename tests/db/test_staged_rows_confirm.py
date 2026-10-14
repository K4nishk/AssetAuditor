"""Live proof of the parse-confirm read/edit/confirm->silver path (KCH-52 / AA-17).

Same ephemeral-Postgres approach as tests/db/test_bronze_files_dedupe.py:
`app.db.queries.silver`'s natural-key FK resolution (`account_mask`, `ticker`
-> real UUIDs) and the RLS-scoped `staged_rows`/`etl_jobs` transitions are
real Postgres behaviour a mocked connection can't prove. Skips cleanly
wherever Postgres tooling is unavailable, per CLAUDE.md.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from app.db.queries import etl_jobs, staged_rows
from app.db.queries.silver import SilverResolutionError, write_confirmed_rows

MIGRATION_SQL = Path("app/db/migrations/0001_init.sql").read_text()
MIGRATION_SQL_LOCAL = MIGRATION_SQL.replace("create extension if not exists pgsodium;\n", "")
MIGRATION_0003_SQL = Path("app/db/migrations/0003_holdings_fee_drag.sql").read_text()

AUTH_STUB_SQL = """
create schema auth;

create table auth.users (
    id uuid primary key default gen_random_uuid(),
    email text
);

create function auth.uid() returns uuid
language sql stable
as $$
    select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid
$$;

grant usage on schema auth to authenticated;
grant execute on function auth.uid() to authenticated;
grant usage on schema public to authenticated;
"""

pytestmark = pytest.mark.asyncio


async def _authenticated_conn(pg_cluster, dbname):
    return await asyncpg.connect(
        host=pg_cluster["socket_dir"], user="authenticated", database=dbname
    )


@pytest_asyncio.fixture
async def seeded_db(pg_cluster, scratch_database):
    admin = await asyncpg.connect(
        host=pg_cluster["socket_dir"], user=pg_cluster["admin_user"], database=scratch_database
    )
    try:
        await admin.execute(AUTH_STUB_SQL)
        await admin.execute(MIGRATION_SQL_LOCAL)
        await admin.execute(MIGRATION_0003_SQL)

        user_id = uuid.uuid4()
        await admin.execute("insert into auth.users (id) values ($1)", user_id)

        bronze_id = uuid.uuid4()
        await admin.execute(
            """
            insert into public.bronze_files (id, user_id, sha256, blob_url)
            values ($1, $2, $3, 'https://blob.example/x')
            """,
            bronze_id,
            user_id,
            uuid.uuid4().hex + uuid.uuid4().hex,
        )

        job_id = uuid.uuid4()
        await admin.execute(
            """
            insert into public.etl_jobs (id, user_id, bronze_file_id, status)
            values ($1, $2, $3, 'needs_user')
            """,
            job_id,
            user_id,
            bronze_id,
        )

        yield {
            "conn": admin,
            "dbname": scratch_database,
            "user_id": str(user_id),
            "job_id": str(job_id),
        }
    finally:
        await admin.close()


async def _stage(seeded_db, entity, payload, *, confidence=1.0, method="deterministic"):
    return await staged_rows.insert_draft(
        seeded_db["conn"],
        user_id=seeded_db["user_id"],
        job_id=seeded_db["job_id"],
        entity=entity,
        payload=payload,
        confidence=confidence,
        method=method,
    )


# --- staged_rows reads/edits --------------------------------------------------


async def test_list_rows_for_job_excludes_deactivated_rows(seeded_db):
    conn = seeded_db["conn"]
    kept = await _stage(seeded_db, "account", {"masked_identifier": "questrade-...1234"})
    await _stage(seeded_db, "account", {"masked_identifier": "questrade-...5678"})
    await conn.execute(
        "update public.staged_rows set deactivated_at = now() where id != $1", kept["id"]
    )

    rows = await staged_rows.list_rows_for_job(
        conn, user_id=seeded_db["user_id"], job_id=seeded_db["job_id"]
    )

    assert [row["id"] for row in rows] == [kept["id"]]


async def test_update_row_payload_logs_a_manual_correction(seeded_db):
    conn = seeded_db["conn"]
    row = await _stage(
        seeded_db, "transaction", {"amount": "10.00"}, confidence=0.4, method="llm"
    )

    updated = await staged_rows.update_row_payload(
        conn,
        user_id=seeded_db["user_id"],
        job_id=seeded_db["job_id"],
        row_id=str(row["id"]),
        payload={"amount": "12.34"},
    )

    assert updated["method"] == "manual_correction"
    assert updated["confidence"] == 1.0


async def test_update_row_payload_is_a_noop_once_confirmed(seeded_db):
    conn = seeded_db["conn"]
    row = await _stage(seeded_db, "transaction", {"amount": "10.00"})
    await conn.execute(
        "update public.staged_rows set confirmed_at = now() where id = $1", row["id"]
    )

    updated = await staged_rows.update_row_payload(
        conn,
        user_id=seeded_db["user_id"],
        job_id=seeded_db["job_id"],
        row_id=str(row["id"]),
        payload={"amount": "999.99"},
    )

    assert updated is None


# --- RLS tenant boundary --------------------------------------------------------


async def test_list_rows_for_job_is_scoped_by_rls_not_just_its_where_clause(
    pg_cluster, seeded_db
):
    """`list_rows_for_job`'s SQL filters by `user_id` in its WHERE clause, but
    per the module docstring that's app-level trust in the caller's argument —
    Postgres's RLS policy (migration 0001) is what actually enforces the tenant
    boundary. Prove it the same way tests/db/test_migration_0001_rls.py does:
    call the real query function under an RLS-scoped `authenticated` connection
    impersonating a *different* user than the one whose id is passed in. Even a
    caller bug that hands this function the victim's own user_id/job_id must
    still come back empty.
    """
    admin = seeded_db["conn"]
    victim_row = await _stage(seeded_db, "account", {"masked_identifier": "questrade-...9999"})

    attacker_id = uuid.uuid4()
    await admin.execute("insert into auth.users (id) values ($1)", attacker_id)

    attacker_conn = await _authenticated_conn(pg_cluster, seeded_db["dbname"])
    try:
        async with attacker_conn.transaction():
            await attacker_conn.execute(
                "select set_config('request.jwt.claim.sub', $1, true)", str(attacker_id)
            )
            rows = await staged_rows.list_rows_for_job(
                attacker_conn, user_id=seeded_db["user_id"], job_id=seeded_db["job_id"]
            )
            assert rows == []
    finally:
        await attacker_conn.close()

    # Sanity check: RLS is scoping the read, not eating every row outright —
    # the row is genuinely there and visible to its own user.
    owner_conn = await _authenticated_conn(pg_cluster, seeded_db["dbname"])
    try:
        async with owner_conn.transaction():
            await owner_conn.execute(
                "select set_config('request.jwt.claim.sub', $1, true)", seeded_db["user_id"]
            )
            rows_for_owner = await staged_rows.list_rows_for_job(
                owner_conn, user_id=seeded_db["user_id"], job_id=seeded_db["job_id"]
            )
            assert [r["id"] for r in rows_for_owner] == [victim_row["id"]]
    finally:
        await owner_conn.close()


# --- confirm -> silver ---------------------------------------------------------


async def test_write_confirmed_rows_resolves_account_then_holding_then_lot(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]
    rows = [
        await _stage(
            seeded_db,
            "account",
            {
                "institution": "questrade",
                "account_type": "tfsa",
                "masked_identifier": "questrade-...1234",
                "currency": "CAD",
            },
        ),
        await _stage(
            seeded_db,
            "holding",
            {
                "account_mask": "questrade-...1234",
                "ticker": "AAPL",
                "quantity": Decimal("10"),
                "avg_cost": Decimal("311.00"),
                "currency": "USD",
            },
        ),
        await _stage(
            seeded_db,
            "lot",
            {
                "account_mask": "questrade-...1234",
                "ticker": "AAPL",
                "quantity": Decimal("10"),
                "unit_cost": Decimal("311.00"),
                "currency": "USD",
                "acquired_at": "2026-07-02",
                "vested": None,
            },
        ),
    ]

    summary = await write_confirmed_rows(conn, user_id=user_id, rows=rows)

    assert summary == {"account": 1, "holding": 1, "lot": 1, "transaction": 0, "liability": 0}
    account = await conn.fetchrow(
        "select * from public.accounts where user_id = $1", user_id
    )
    assert account["masked_identifier"] == "questrade-...1234"
    holding = await conn.fetchrow(
        "select * from public.holdings where user_id = $1", user_id
    )
    assert holding["account_id"] == account["id"]
    assert holding["quantity"] == Decimal("10.00000000")
    lot = await conn.fetchrow("select * from public.lots where user_id = $1", user_id)
    assert lot["holding_id"] == holding["id"]


async def test_write_confirmed_rows_reuses_an_existing_account_across_jobs(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]
    await conn.execute(
        """
        insert into public.accounts
            (user_id, institution, account_type, masked_identifier, currency)
        values ($1, 'scotia', 'chequing', 'scotia-...4821', 'CAD')
        """,
        user_id,
    )

    rows = [
        await _stage(
            seeded_db,
            "account",
            {
                "institution": "scotia",
                "account_type": "chequing",
                "masked_identifier": "scotia-...4821",
                "currency": "CAD",
            },
        ),
        await _stage(
            seeded_db,
            "transaction",
            {
                "account_mask": "scotia-...4821",
                "kind": "debit",
                "amount": Decimal("45.00"),
                "currency": "CAD",
                "occurred_at": "2026-07-02T00:00:00+00:00",
                "description": "groceries",
            },
        ),
    ]

    summary = await write_confirmed_rows(conn, user_id=user_id, rows=rows)

    assert summary["account"] == 0  # reused, not re-inserted
    assert summary["transaction"] == 1
    accounts = await conn.fetch("select id from public.accounts where user_id = $1", user_id)
    assert len(accounts) == 1
    txn = await conn.fetchrow("select * from public.transactions where user_id = $1", user_id)
    assert txn["account_id"] == accounts[0]["id"]
    assert txn["amount"] == Decimal("45.00000000")


async def test_write_confirmed_rows_raises_when_account_mask_never_resolves(seeded_db):
    conn = seeded_db["conn"]
    rows = [
        await _stage(
            seeded_db,
            "transaction",
            {
                "account_mask": "ghost-...9999",
                "kind": "debit",
                "amount": Decimal("1.00"),
                "currency": "CAD",
                "occurred_at": "2026-07-02T00:00:00+00:00",
            },
        )
    ]

    with pytest.raises(SilverResolutionError, match="ghost-...9999"):
        await write_confirmed_rows(conn, user_id=seeded_db["user_id"], rows=rows)


async def test_write_confirmed_rows_raises_when_a_lot_has_no_matching_holding(seeded_db):
    conn = seeded_db["conn"]
    rows = [
        await _stage(
            seeded_db,
            "account",
            {
                "institution": "questrade",
                "account_type": "tfsa",
                "masked_identifier": "questrade-...1234",
                "currency": "CAD",
            },
        ),
        await _stage(
            seeded_db,
            "lot",
            {
                "account_mask": "questrade-...1234",
                "ticker": "AAPL",
                "quantity": Decimal("10"),
                "unit_cost": Decimal("311.00"),
                "currency": "USD",
                "acquired_at": "2026-07-02",
            },
        ),
    ]

    with pytest.raises(SilverResolutionError, match="no matching confirmed holding"):
        await write_confirmed_rows(conn, user_id=seeded_db["user_id"], rows=rows)


# --- full HTTP-shaped flow via the query layer directly -----------------------


async def test_confirm_flow_marks_rows_confirmed_and_job_done(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]
    job_id = seeded_db["job_id"]

    row = await _stage(
        seeded_db,
        "account",
        {
            "institution": "kraken",
            "account_type": "crypto_exchange",
            "masked_identifier": "kraken-default",
            "currency": "CAD",
        },
    )

    rows = await staged_rows.list_rows_for_job(
        conn, user_id=user_id, job_id=job_id, unconfirmed_only=True
    )
    summary = await write_confirmed_rows(conn, user_id=user_id, rows=rows)
    await staged_rows.mark_confirmed(
        conn, user_id=user_id, job_id=job_id, row_ids=[str(row["id"])]
    )
    marked_job = await etl_jobs.mark_job_done(conn, user_id=user_id, job_id=job_id)

    assert summary["account"] == 1
    assert marked_job["status"] == "done"
    confirmed_row = await conn.fetchrow(
        "select confirmed_at from public.staged_rows where id = $1", row["id"]
    )
    assert confirmed_row["confirmed_at"] is not None

    # A second confirm attempt is rejected by the job's own status guard.
    again = await etl_jobs.mark_job_done(conn, user_id=user_id, job_id=job_id)
    assert again is None
