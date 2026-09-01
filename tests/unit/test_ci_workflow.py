"""Structural checks for the CI pipeline (KCH-38 / AA-3).

Text-based assertions against the workflow file, not a YAML-semantics test —
mirrors tests/unit/test_migration_0001_schema.py's approach so this doesn't need
a new dependency (PyYAML) just to check that required jobs/steps exist.
"""

from pathlib import Path

WORKFLOW_PATH = Path(".github/workflows/ci.yml")


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text()


def test_workflow_file_exists():
    assert WORKFLOW_PATH.is_file()


def test_runs_on_pull_request_and_protected_branches():
    text = _workflow_text()
    assert "pull_request:" in text
    assert "development" in text
    assert "main" in text


def test_backend_job_runs_ruff_mypy_and_pytest():
    text = _workflow_text()
    assert "uv run ruff check" in text
    assert "uv run mypy" in text
    assert "uv run pytest" in text


def test_frontend_job_runs_lint_and_build():
    text = _workflow_text()
    assert "npm run lint" in text
    assert "npm run build" in text


def test_migration_dry_run_job_targets_a_supabase_branch_db():
    text = _workflow_text()
    assert "migration-dry-run" in text
    assert "SUPABASE_ACCESS_TOKEN" in text
    assert "SUPABASE_PROJECT_ID" in text
    assert "supabase db push --dry-run" in text
