"""Structural checks for migration 0001 (KCH-37 / AA-2).

Pure text-based assertions — no database required — so this always runs in CI.
Behavioral RLS proof (cross-user isolation against a live Postgres) lives in
tests/db/test_migration_0001_rls.py and is skipped where Postgres tooling is
unavailable.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = REPO_ROOT / "app/db/migrations/0001_init.sql"

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

SILVER_GOLD_TABLES = [
    "accounts",
    "holdings",
    "lots",
    "transactions",
    "liabilities",
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


def _assert_no_authenticated_dml(block: str, table: str) -> None:
    for verb in ("insert", "update", "delete"):
        grant = (
            rf"grant\s+[^;]*\b{verb}\b[^;]*\bon\s+public\.{table}"
            rf"\s+to\s+authenticated\b"
        )
        assert re.search(grant, block) is None, f"authenticated has {verb} grant on {table}"


def test_migration_file_exists():
    assert MIGRATION_PATH.is_file()


def test_extensions_enabled():
    sql = MIGRATION_PATH.read_text()
    assert "create extension if not exists pgsodium;" in sql
    assert "create extension if not exists pgcrypto;" in sql


def test_every_documented_table_present():
    sql = MIGRATION_PATH.read_text()
    for table in ALL_TABLES:
        assert f"create table public.{table} (" in sql, f"missing documented table: {table}"


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
        if table in SILVER_GOLD_TABLES:
            assert f"grant select on public.{table} to authenticated;" in block, table
            _assert_no_authenticated_dml(block, table)
        else:
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
        assert "using (auth.uid() is not null)" in block, table
        assert f"grant select on public.{table} to authenticated;" in block, table
        _assert_no_authenticated_dml(block, table)


def test_account_number_vault_reserves_encryption_column():
    sql = MIGRATION_PATH.read_text()
    block = _table_blocks(sql)["account_number_vault"]
    assert "encrypted_account_number bytea not null" in block


def test_gold_tables_require_a_run_id():
    sql = MIGRATION_PATH.read_text()
    blocks = _table_blocks(sql)
    for table in ["networth_snapshots", "term_buckets", "diversification_cuts"]:
        assert "run_id uuid not null" in blocks[table], table
