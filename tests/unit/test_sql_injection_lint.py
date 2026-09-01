"""Proves the CI SQL-injection lint (KCH-38 / AA-3) actually fails on f-string SQL.

CLAUDE.md hard rule #3: "Raw SQL, parameterized only — f-string SQL fails CI."
pyproject.toml's [tool.ruff.lint] select includes "S608" (flake8-bandit) for this.
This test runs the real ruff binary, using the project's own configuration
(discovered via cwd, not a CLI override), against throwaway files so a config
regression that silently disables the check would be caught.
"""

import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

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
        [sys.executable, "-m", "ruff", "check", "--no-cache", str(target)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
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


def test_pyproject_selects_the_sql_injection_rule(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text("x = 1\n")
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--show-settings", "--no-cache", str(probe)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr

    enabled_rules = re.search(r"linter\.rules\.enabled = \[(.*?)\]", result.stdout, re.DOTALL)
    assert enabled_rules, "could not find linter.rules.enabled in ruff --show-settings output"
    assert "S608" in enabled_rules.group(1)
