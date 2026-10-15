"""Liberated Capacity tooling: transcript pricing and ledger totals. Pure, no network."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from tools.liberated_capacity import (
    Call,
    UnknownModelError,
    cost_by_branch,
    iter_calls,
    ledger_totals,
    main,
    price,
)

MODEL = "claude-opus-5-5"
ONE_M = 1_000_000


def _usage(**tokens: int) -> dict:
    split = {
        "ephemeral_5m_input_tokens": tokens.pop("write_5m", 0),
        "ephemeral_1h_input_tokens": tokens.pop("write_1h", 0),
    }
    return {**tokens, "cache_creation": split}


def _line(
    msg_id: str,
    *,
    branch: str = "feature/x",
    ts: str = "2026-10-15T00:00:00Z",
    model: str = MODEL,
    output: int = ONE_M,
) -> str:
    message = {"id": msg_id, "model": model, "usage": _usage(output_tokens=output)}
    return json.dumps({"gitBranch": branch, "timestamp": ts, "message": message})


def test_price_uses_list_prices_for_every_token_kind() -> None:
    usage = _usage(
        input_tokens=ONE_M,
        output_tokens=ONE_M,
        cache_read_input_tokens=ONE_M,
        write_5m=ONE_M,
        write_1h=ONE_M,
    )
    assert price(Call("b", "t", MODEL, usage)) == Decimal("37.20")  # 4 + 20 + 0.20 + 5 + 8


def test_usage_without_a_ttl_split_prices_writes_at_the_5_minute_rate() -> None:
    usage = {"cache_creation_input_tokens": ONE_M}
    assert price(Call("b", "t", MODEL, usage)) == Decimal("5.00")


def test_an_unknown_model_is_an_error_not_a_free_call() -> None:
    with pytest.raises(UnknownModelError, match="claude-imaginary"):
        price(Call("b", "t", "claude-imaginary", {"output_tokens": 1}))


def test_each_response_is_counted_once_and_synthetic_entries_are_skipped(tmp_path: Path) -> None:
    transcript = tmp_path / "s.jsonl"
    transcript.write_text(
        "\n".join(
            [
                _line("msg_1"),
                _line("msg_1"),  # the same response repeated on its next content block
                _line("msg_2", model="<synthetic>"),
                "not json",
            ]
        )
    )
    calls = list(iter_calls([transcript]))
    assert [c.usage["output_tokens"] for c in calls] == [ONE_M]


def test_cost_groups_by_branch_within_a_half_open_window(tmp_path: Path) -> None:
    transcript = tmp_path / "s.jsonl"
    transcript.write_text(
        "\n".join(
            [
                _line("a", branch="feature/a", ts="2026-10-15T01:00:00Z"),
                _line("b", branch="feature/a", ts="2026-10-15T02:00:00Z"),  # == until: excluded
                _line("c", branch="feature/b", ts="2026-10-15T01:30:00Z"),
                _line("d", branch="feature/b", ts="2026-10-15T00:59:59Z"),  # before since
            ]
        )
    )
    result = cost_by_branch(
        iter_calls([transcript]), since="2026-10-15T01:00:00Z", until="2026-10-15T02:00:00Z"
    )
    assert result == {"feature/a": (1, Decimal("20")), "feature/b": (1, Decimal("20"))}


def _row(pr: str, status: str, baseline: str = "", owner: str = "", ai: str = "0") -> dict:
    return {
        "pr": pr,
        "status": status,
        "baseline_h": baseline,
        "owner_h": owner,
        "ai_usd": ai,
        "infra_usd": "0",
    }


def test_ledger_nets_owner_time_and_keeps_overhead_out_of_hours() -> None:
    totals = ledger_totals(
        [
            _row("61", "estimate", "8", "0.5", "3.50"),
            _row("62", "confirmed", "5", "1.25", "4.00"),
            _row("-", "overhead", ai="10.00"),
            _row("54", "not_measured"),
        ]
    )
    assert totals["liberated_h"] == Decimal("11.25")
    assert totals["owner_h"] == Decimal("1.75")
    assert totals["pr_usd"] == Decimal("7.50")
    assert totals["overhead_usd"] == Decimal("10.00")
    assert totals["total_usd"] == Decimal("17.50")


def test_ledger_rejects_an_unknown_status() -> None:
    with pytest.raises(ValueError, match="unknown status"):
        ledger_totals([_row("61", "guess", "8", "0.5")])


def test_totals_command_reports_cost_per_liberated_hour(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger.csv"
    ledger.write_text(
        "pr,title,branch,status,baseline_h,owner_h,ai_usd,infra_usd,cost_basis,estimate_basis\n"
        "61,t,b,estimate,10,2,16.00,0,src,why\n"
    )
    assert main(["totals", str(ledger)]) == 0
    assert "usd_per_h      2.00" in capsys.readouterr().out


def test_the_repo_ledger_parses_and_reconciles() -> None:
    import csv

    with Path("docs/metrics/liberated-capacity.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    totals = ledger_totals(rows)
    assert totals["total_usd"] == totals["pr_usd"] + totals["overhead_usd"]
    assert totals["liberated_h"] == totals["baseline_h"] - totals["owner_h"]
    assert {row["pr"] for row in rows if row["status"] != "overhead"} >= {"61", "62", "66"}
