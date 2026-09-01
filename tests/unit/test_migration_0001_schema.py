"""Structural checks for migration 0001 (KCH-37 / AA-2).

Pure text-based assertions — no database required — so this always runs in CI.
Behavioral RLS proof (cross-user isolation against a live Postgres) lives in
tests/db/test_migration_0001_rls.py and is skipped where Postgres tooling is
unavailable.
"""

from pathlib import Path

MIGRATION_PATH = Path("app/db/migrations/0001_init.sql")

# Every table from ADR v1.0.0 §7 plus worker_heartbeat (ADR v1.1.0 §2), scoped
# to a single user via `user_id` + RLS.
USER_ID_TABLES = [
    "bronze_files",
    "etl_jobs",
    "staged_rows",
    "accounts",
    "account_number_vault",
    "holdings",
    "lots",
    "transactions",
    "liabilities",
    "room_events",
    "lineage_events",
    "networth_snapshots",
    "term_buckets",
    "diversification_cuts",
]

# users_profile is scoped by its own primary key (id = auth.uid()), not a
# separate user_id column.
SPECIAL_TENANT_TABLES = ["users_profile"]

# Shared reference data — RLS on, but no per-user isolation.
REFERENCE_TABLES = ["prices", "worker_heartbeat"]

ALL_TABLES = USER_ID_TABLES + SPECIAL_TENANT_TABLES + REFERENCE_TABLES


def _table_blocks(sql: str) -> dict[str, str]:
    """Slice the migration into per-table chunks (table def through its RLS/grants)."""
    markers = [(name, sql.index(f"create table public.{name} (")) for name in ALL_TABLES]
    markers.sort(key=lambda pair: pair[1])
    blocks = {}
    for i, (name, start) in enumerate(markers):
        end = markers[i + 1][1] if i + 1 < len(markers) else len(sql)
        blocks[name] = sql[start:end]
    return blocks


def test_migration_file_exists():
    assert MIGRATION_PATH.is_file()


def test_extensions_enabled():
    sql = MIGRATION_PATH.read_text()
    assert "create extension if not exists pgsodium;" in sql
    assert "create extension if not exists pgcrypto;" in sql


def test_every_documented_table_present():
    sql = MIGRATION_PATH.read_text()
    blocks = _table_blocks(sql)
    assert set(blocks) == set(ALL_TABLES)


def test_user_id_tables_have_user_id_deactivated_at_and_isolation_policy():
    sql = MIGRATION_PATH.read_text()
    blocks = _table_blocks(sql)
    for table in USER_ID_TABLES:
        block = blocks[table]
        assert "user_id uuid not null references auth.users (id) on delete cascade" in block, table
        assert "deactivated_at timestamptz" in block, table
        assert f"alter table public.{table} enable row level security;" in block, table
        assert f"create policy {table}_tenant_isolation on public.{table}" in block, table
        assert "using (user_id = auth.uid())" in block, table
        assert "with check (user_id = auth.uid())" in block, table
        grant = f"grant select, insert, update, delete on public.{table} to authenticated;"
        assert grant in block, table


def test_users_profile_is_scoped_by_its_own_id():
    sql = MIGRATION_PATH.read_text()
    block = _table_blocks(sql)["users_profile"]
    assert "id uuid primary key references auth.users (id) on delete cascade" in block
    assert "deactivated_at timestamptz" in block
    assert "alter table public.users_profile enable row level security;" in block
    assert "create policy users_profile_tenant_isolation on public.users_profile" in block
    assert "using (id = auth.uid())" in block
    assert "with check (id = auth.uid())" in block


def test_reference_tables_are_read_only_for_authenticated_and_carry_no_user_id():
    sql = MIGRATION_PATH.read_text()
    blocks = _table_blocks(sql)
    for table in REFERENCE_TABLES:
        block = blocks[table]
        assert "user_id" not in block, table
        assert f"alter table public.{table} enable row level security;" in block, table
        assert f"create policy {table}_read on public.{table}" in block, table
        assert "for select" in block, table
        assert "using (true)" in block, table
        assert f"grant select on public.{table} to authenticated;" in block, table
        # read-only: no insert/update/delete grants to authenticated for these tables
        assert "insert, update, delete" not in block, table


def test_account_number_vault_reserves_encryption_column():
    sql = MIGRATION_PATH.read_text()
    block = _table_blocks(sql)["account_number_vault"]
    assert "encrypted_account_number bytea not null" in block
