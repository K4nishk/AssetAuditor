"""Proves the CI SQL-injection lint (KCH-38 / AA-3) actually fails on f-string SQL.

CLAUDE.md hard rule #3: "Raw SQL, parameterized only — f-string SQL fails CI."
pyproject.toml's [tool.ruff.lint] select includes "S608" (flake8-bandit) for this.
This test runs the real ruff binary against throwaway files (not just checking
config) so a config regression that silently disables the check would be caught.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

F_STRING_SQL = textwrap.dedent(
    """
    async def get_user(conn, user_id):
        query = f"SELECT * FROM users WHERE id = '{user_id}'"
        return await conn.fetch(query)
    """
)

PARAMETERIZED_SQL = textwrap.dedent(
    """
    async def get_user(conn, user_id):
        return await conn.fetch("SELECT * FROM users WHERE id = $1", user_id)
    """
)


def _run_ruff(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    target = tmp_path / "sample.py"
    target.write_text(source)
    return subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "S608", "--no-cache", str(target)],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )


@pytest.mark.parametrize("source", [F_STRING_SQL], ids=["f-string"])
def test_f_string_sql_fails_lint(tmp_path, source):
    result = _run_ruff(tmp_path, source)
    assert result.returncode != 0
    assert "S608" in result.stdout


def test_parameterized_sql_passes_lint(tmp_path):
    result = _run_ruff(tmp_path, PARAMETERIZED_SQL)
    assert result.returncode == 0
    assert "S608" not in result.stdout


def test_pyproject_selects_the_sql_injection_rule():
    pyproject = Path("pyproject.toml").read_text()
    assert "S608" in pyproject
