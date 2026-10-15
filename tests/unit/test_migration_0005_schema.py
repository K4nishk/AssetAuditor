"""Structural checks for migration 0005 (AA-63, #46).

Behaviour is proven live in tests/db/test_etl_jobs_waiting.py.
"""

from pathlib import Path

SQL = (
    Path(__file__).resolve().parents[2] / "app/db/migrations/0005_etl_jobs_waiting.sql"
).read_text()

REASONS = (
    "local_not_ready",
    "local_unavailable",
    "not_codable",
    "check_failed",
    "egress_unavailable",
    "coded_result_invalid",
)


def test_adds_the_three_waiting_columns() -> None:
    for column in (
        "add column wait_reason text",
        "add column not_before timestamptz",
        "add column defer_count integer not null default 0",
    ):
        assert column in SQL


def test_constrains_wait_reason_to_the_six_reasons() -> None:
    for reason in REASONS:
        assert f"'{reason}'" in SQL


def test_wait_reason_only_on_pending_rows() -> None:
    assert "wait_reason is null or status = 'pending'" in SQL


def test_changes_no_grants_or_policies() -> None:
    lowered = SQL.lower()
    assert (
        "grant " not in lowered and "policy" not in lowered and "row level security" not in lowered
    )
