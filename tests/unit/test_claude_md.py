"""Structural checks for CLAUDE.md (KCH-40 / AA-5).

CLAUDE.md is binding project instruction, not just documentation: it defines
the tmux session playbook (scopes + budget rules) every agent works under and
a read-first map of paths the agent is expected to consult. Text-based
assertions, mirroring tests/unit/test_ci_workflow.py's approach — this locks
in the sections the issue requires and guards against the read-first map
drifting out of sync with the actual repo layout as later issues land.
"""

from pathlib import Path

CLAUDE_MD_PATH = Path("CLAUDE.md")

# Every path CLAUDE.md's read-first map and hard rules point an agent at.
REFERENCED_PATHS = [
    "mvp.md",
    "docs/adr/ADR_v1.1.0.md",
    "docs/vault/20-domain/Contribution-Rooms.md",
    "data/samples/README.md",
    "docs/vault/40-research/PDF-Statement-Parsing.md",
    "skills/e2e-testing/SKILL.md",
    "ops/orchestrator.sh",
]


def _text() -> str:
    return CLAUDE_MD_PATH.read_text()


def test_claude_md_exists():
    assert CLAUDE_MD_PATH.is_file()


def test_referenced_paths_exist():
    missing = [p for p in REFERENCED_PATHS if not Path(p).exists()]
    assert not missing, f"CLAUDE.md references paths that don't exist: {missing}"


def test_referenced_paths_are_actually_mentioned_in_claude_md():
    text = _text()
    unmentioned = [p for p in REFERENCED_PATHS if p not in text]
    assert not unmentioned, (
        f"REFERENCED_PATHS lists a path CLAUDE.md's own text never mentions "
        f"(so the read-first map and this list have drifted apart): {unmentioned}"
    )


def test_defines_priority_order():
    text = _text()
    assert "data provenance" in text
    assert "delivery timelines" in text


def test_defines_interactive_tmux_session_scopes():
    text = _text()
    assert "## Interactive tmux sessions" in text
    assert "One session = one `AA-n`/Linear issue" in text


def test_defines_overnight_run_budget_rules():
    text = _text()
    assert "## Unattended overnight runs" in text
    assert "no Supabase project, no Vercel token, no Groq key, no GPU box" in text
    assert "no network" in text


def test_defines_zero_cost_contract():
    text = _text()
    assert "Zero-cost contract" in text
    assert "never introduce a paid service or exceed a free tier" in text


def test_defines_branching_model():
    text = _text()
    assert "main                     production-deployment code" in text
    assert "development          demoable MVP" in text


def test_defines_coderabbit_review_protocol():
    text = _text()
    assert "## CodeRabbit review protocol" in text
    assert "coderabbit review --agent --committed" in text
