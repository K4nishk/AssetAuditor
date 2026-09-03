"""Structural checks for migration 0003 (KCH-63 / AA-26).

Pure text-based assertions — no database required, same convention as
`tests/unit/test_migration_0001_schema.py`/`test_migration_0002_schema.py`.
Live proof that the column round-trips through `INSERT`/`SELECT` lives in
`tests/db/test_fees_query_live.py`.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = REPO_ROOT / "app/db/migrations/0003_holdings_fee_drag.sql"


def test_migration_file_exists():
    assert MIGRATION_PATH.is_file()


def test_adds_nullable_mer_pct_column_to_holdings():
    sql = MIGRATION_PATH.read_text()
    assert "alter table public.holdings" in sql
    assert "add column mer_pct numeric(6, 4);" in sql
    # Nullable (no `not null`) — every non-TD adapter never captures a MER.
    assert "mer_pct numeric(6, 4) not null" not in sql
