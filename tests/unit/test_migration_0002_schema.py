"""Structural checks for migration 0002 (KCH-62 / AA-25).

Pure text-based assertions — no database required, same convention as
`tests/unit/test_migration_0001_schema.py`. Live RLS proof (skipped where
Postgres tooling is unavailable) lives in `tests/db/test_audit_commentary_live.py`.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = REPO_ROOT / "app/db/migrations/0002_audit_commentary.sql"


def test_migration_file_exists():
    assert MIGRATION_PATH.is_file()


def test_table_present_with_user_id_deactivated_at_and_run_id():
    sql = MIGRATION_PATH.read_text()
    assert "create table public.audit_commentary (" in sql
    assert "user_id uuid not null references auth.users (id) on delete cascade" in sql
    assert "deactivated_at timestamptz" in sql
    assert "run_id uuid not null" in sql
    assert "unique (user_id, snapshot_date)" in sql


def test_rls_enabled_with_tenant_isolation_policy():
    sql = MIGRATION_PATH.read_text()
    assert "alter table public.audit_commentary enable row level security;" in sql
    assert "create policy audit_commentary_tenant_isolation on public.audit_commentary" in sql
    assert "using (user_id = auth.uid())" in sql
    assert "with check (user_id = auth.uid())" in sql


def test_authenticated_is_read_only_same_as_other_gold_tables():
    """Writes are worker-only (`worker.commentary.generate_audit_commentary`),
    same convention `test_migration_0001_schema.py` enforces for
    networth_snapshots/term_buckets/diversification_cuts."""
    sql = MIGRATION_PATH.read_text()
    assert "grant select on public.audit_commentary to authenticated;" in sql
    for verb in ("insert", "update", "delete"):
        assert f"grant {verb}" not in sql.lower()
