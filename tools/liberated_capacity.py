"""Liberated Capacity: what each PR cost to build and the human hours it freed.

Two commands, both stdlib only:

- `cost` prices the Claude Code transcripts (main session and sub-agents) at
  API list prices and groups the spend by the git branch that was checked out
  when each response was produced. Each API response is counted once (the
  transcript repeats its usage on every content block). An unknown model is
  an error, never a silent zero.
- `totals` adds up the ledger (`docs/metrics/liberated-capacity.csv`).

Definitions and the estimation rubric: docs/metrics/liberated-capacity.md.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

MILLION = Decimal(1_000_000)
PRICES_AS_OF = "2026-10-06"  # claude-api skill price table, Anthropic first-party list prices


@dataclass(frozen=True)
class Prices:
    """USD per million tokens."""

    input: Decimal
    output: Decimal
    cache_read: Decimal
    cache_write_5m: Decimal
    cache_write_1h: Decimal


PRICES = {
    # Cache writes cost 1.25x input (5-minute TTL) or 2x input (1-hour TTL).
    "claude-opus-5-5": Prices(
        Decimal("4.00"), Decimal("20.00"), Decimal("0.20"), Decimal("5.00"), Decimal("8.00")
    ),
}
LEDGER_STATUSES = frozenset({"estimate", "confirmed", "overhead", "not_measured"})


class UnknownModelError(ValueError):
    """A transcript names a model with no entry in PRICES."""


@dataclass(frozen=True)
class Call:
    """One billed API response."""

    branch: str
    timestamp: str
    model: str
    usage: dict


def price(call: Call) -> Decimal:
    prices = PRICES.get(call.model)
    if prices is None:
        raise UnknownModelError(f"no price for model {call.model!r}; add it to PRICES")
    u = call.usage
    split = u.get("cache_creation") or {}
    write_1h = split.get("ephemeral_1h_input_tokens") or 0
    write_5m = (
        split.get("ephemeral_5m_input_tokens") or 0
        if split
        else u.get("cache_creation_input_tokens") or 0
    )
    tokens = (
        Decimal(u.get("input_tokens") or 0) * prices.input
        + Decimal(u.get("output_tokens") or 0) * prices.output
        + Decimal(u.get("cache_read_input_tokens") or 0) * prices.cache_read
        + Decimal(write_5m) * prices.cache_write_5m
        + Decimal(write_1h) * prices.cache_write_1h
    )
    return tokens / MILLION


def iter_calls(paths: Iterable[Path]) -> Iterator[Call]:
    """Billed calls from transcript files, each API response once."""
    for path in paths:
        seen: set[str] = set()
        with path.open(encoding="utf-8") as lines:
            for line in lines:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                message = entry.get("message")
                if not isinstance(message, dict) or not message.get("usage"):
                    continue
                message_id = str(message.get("id"))
                if message.get("model") == "<synthetic>" or message_id in seen:
                    continue
                seen.add(message_id)
                yield Call(
                    branch=entry.get("gitBranch") or "(none)",
                    timestamp=entry.get("timestamp") or "",
                    model=message.get("model") or "",
                    usage=message["usage"],
                )


def transcript_files(roots: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        files.extend(sorted(root.rglob("*.jsonl")) if root.is_dir() else [root])
    return files


def cost_by_branch(
    calls: Iterable[Call], *, since: str = "", until: str = ""
) -> dict[str, tuple[int, Decimal]]:
    """branch -> (calls, USD), keeping calls with since <= timestamp < until (ISO strings)."""
    totals: dict[str, list] = defaultdict(lambda: [0, Decimal(0)])
    for call in calls:
        if (since and call.timestamp < since) or (until and call.timestamp >= until):
            continue
        totals[call.branch][0] += 1
        totals[call.branch][1] += price(call)
    return {branch: (n, usd) for branch, (n, usd) in totals.items()}


def ledger_totals(rows: Iterable[dict[str, str]]) -> dict[str, Decimal]:
    """Hours count only rows that carry both estimates; USD counts every row."""
    keys = ("baseline_h", "owner_h", "liberated_h", "pr_usd", "overhead_usd")
    out = {key: Decimal(0) for key in keys}
    for row in rows:
        if row["status"] not in LEDGER_STATUSES:
            raise ValueError(f"PR {row['pr']}: unknown status {row['status']!r}")
        usd = Decimal(row["ai_usd"] or 0) + Decimal(row["infra_usd"] or 0)
        out["overhead_usd" if row["status"] == "overhead" else "pr_usd"] += usd
        if row["baseline_h"] and row["owner_h"]:
            baseline, owner = Decimal(row["baseline_h"]), Decimal(row["owner_h"])
            out["baseline_h"] += baseline
            out["owner_h"] += owner
            out["liberated_h"] += baseline - owner
    out["total_usd"] = out["pr_usd"] + out["overhead_usd"]
    return out


def _default_transcripts() -> Path:
    return Path.home() / ".claude" / "projects" / str(Path.cwd()).replace("/", "-")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.liberated_capacity")
    sub = parser.add_subparsers(dest="command", required=True)
    cost = sub.add_parser("cost", help="API-equivalent USD per git branch")
    cost.add_argument(
        "transcripts", nargs="*", type=Path, help="files or dirs (default: this repo's)"
    )
    cost.add_argument("--since", default="", help="ISO timestamp, inclusive")
    cost.add_argument("--until", default="", help="ISO timestamp, exclusive")
    totals = sub.add_parser("totals", help="sum the ledger")
    totals.add_argument(
        "ledger", nargs="?", type=Path, default=Path("docs/metrics/liberated-capacity.csv")
    )
    args = parser.parse_args(argv)

    if args.command == "cost":
        files = transcript_files(args.transcripts or [_default_transcripts()])
        by_branch = cost_by_branch(iter_calls(files), since=args.since, until=args.until)
        for branch, (n, usd) in sorted(by_branch.items(), key=lambda kv: -kv[1][1]):
            print(f"{branch:40s} {n:6d} calls  ${usd:,.2f}")
        grand = sum((usd for _, usd in by_branch.values()), Decimal(0))
        print(f"{'TOTAL':40s} {'':12s}  ${grand:,.2f}  (list prices as of {PRICES_AS_OF})")
        return 0

    with args.ledger.open(encoding="utf-8", newline="") as handle:
        result = ledger_totals(csv.DictReader(handle))
    for key, value in result.items():
        print(f"{key:14s} {value:,.2f}")
    if result["liberated_h"] > 0:
        print(f"{'usd_per_h':14s} {result['total_usd'] / result['liberated_h']:,.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
