"""Structural checks for migration 0004 (KCH-66 / AA-29).

Pure text-based assertions — no database required — so this always runs in
CI, same convention as tests/unit/test_migration_0001_schema.py. Behavioral
proof (real encrypt/decrypt round trip, AAD row-binding, revoked grants)
lives in tests/db/test_account_number_vault_live.py and skips wherever
pgsodium isn't installed (every environment except a real Supabase project —
see that file's docstring).
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = REPO_ROOT / "app/db/migrations/0004_account_number_vault_encryption.sql"


def _sql() -> str:
    return MIGRATION_PATH.read_text()


def test_creates_a_single_project_wide_key():
    sql = _sql()
    assert "pgsodium.create_key(name => 'account_number_vault')" in sql


def test_adds_the_unique_constraint_the_upsert_relies_on():
    sql = _sql()
    assert (
        "add constraint account_number_vault_user_account_unique"
        " unique (user_id, account_id);" in sql
    )


def test_store_and_reveal_functions_are_security_definer_with_pinned_search_path():
    sql = _sql()
    for fn in ("vault_store_account_number", "vault_reveal_account_number"):
        block_start = sql.index(f"create or replace function public.{fn}")
        block_end = sql.index("$$;", block_start)
        block = sql[block_start:block_end]
        assert "security definer" in block, fn
        assert "set search_path = ''" in block, fn


def test_account_id_is_folded_in_as_aead_associated_data():
    sql = _sql()
    assert sql.count("convert_to(p_account_id::text, 'utf8')") == 2


def test_raw_table_grants_are_revoked_from_authenticated():
    sql = _sql()
    assert (
        "revoke insert, update, select, delete on public.account_number_vault from authenticated;"
        in sql
    )


def test_execute_is_granted_on_both_functions_only():
    sql = _sql()
    assert (
        "grant execute on function public.vault_store_account_number(uuid, text)"
        " to authenticated;" in sql
    )
    assert (
        "grant execute on function public.vault_reveal_account_number(uuid)"
        " to authenticated;" in sql
    )
