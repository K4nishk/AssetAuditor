"""Golden-set scorer (AA-59, #42; spec #41 decision 1): deterministic and strict.

Pure: golden rows and the model's raw rows in, a `Report` out; never raises on
row content. Observed fields are parsed with the extraction tier's own field
parsers (`worker.adapters.base`), so a value the worker would refuse to stage
(a comma amount, a year-less date) is scored as a `malformed` miss rather than
crashing the eval.

Rows pair one-to-one on (calendar date, Decimal amount) in statement order,
then on a date shared by exactly one leftover row on each side; never by
position, so one dropped row cannot misalign every row after it. Field
accuracy is correct / (golden + invented rows): missed and invented rows miss
every field. Numbers must be exact (owner decision R3.4); description may miss
one row of eight. The report carries indexes, field names and reasons only,
never values, so it is safe to print in CI logs.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, fields
from datetime import date
from decimal import Decimal
from fractions import Fraction
from typing import Any, Literal

from tests.evals.golden_set import GoldenRow
from worker.adapters.base import (
    AdapterParseError,
    normalize_account_mask,
    require_decimal,
    to_datetime_utc,
)

FIELDS = ("date", "kind", "amount", "balance_after", "description", "account_mask")
FLOORS: dict[str, Fraction] = {
    "date": Fraction(1),
    "kind": Fraction(1),
    "amount": Fraction(1),
    "balance_after": Fraction(1),
    "account_mask": Fraction(1),
    "description": Fraction(7, 8),
}
_PARSE_ERRORS = (AdapterParseError, ValueError, TypeError, ArithmeticError)

Reason = Literal["wrong", "malformed", "missing", "invented"]


@dataclass(frozen=True)
class ObservedRow:
    """One transaction as the model returned it: raw JSON values, unparsed."""

    occurred_at: Any = None
    kind: Any = None
    amount: Any = None
    balance_after: Any = None
    description: Any = None
    account_mask: Any = None


@dataclass(frozen=True)
class Finding:
    side: Literal["golden", "observed"]
    index: int
    field: str
    reason: Reason


@dataclass(frozen=True)
class FieldResult:
    correct: int
    total: int
    floor: Fraction

    @property
    def passed(self) -> bool:
        return self.total > 0 and Fraction(self.correct, self.total) >= self.floor


@dataclass(frozen=True)
class Report:
    fields: dict[str, FieldResult]
    paired: int
    missed: tuple[int, ...]
    invented: tuple[int, ...]
    chain_breaks: tuple[int, ...]
    findings: tuple[Finding, ...]
    production_accepted: bool

    @property
    def passed(self) -> bool:
        return not self.failures()

    def failures(self) -> list[str]:
        reasons = [
            f"{name}: {result.correct}/{result.total} correct, floor {result.floor}"
            for name, result in self.fields.items()
            if not result.passed
        ]
        if self.chain_breaks:
            reasons.append(f"balance chain breaks at observed rows {list(self.chain_breaks)}")
        if not self.production_accepted:
            reasons.append(
                "refused by the production parser: the worker would not stage this result"
            )
        return reasons

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "production_accepted": self.production_accepted,
            "paired": self.paired,
            "missed": list(self.missed),
            "invented": list(self.invented),
            "chain_breaks": list(self.chain_breaks),
            "fields": {
                name: {
                    "correct": r.correct,
                    "total": r.total,
                    "floor": str(r.floor),
                    "passed": r.passed,
                }
                for name, r in self.fields.items()
            },
            "findings": [asdict(f) for f in self.findings],
        }


def observed_rows_from_content(content: str | None) -> list[ObservedRow]:
    """Raw rows from the model's JSON content; unparseable content means no rows."""
    try:
        rows = json.loads(content or "")["transactions"]
    except (ValueError, TypeError, KeyError):
        return []
    if not isinstance(rows, list):
        return []
    names = {f.name for f in fields(ObservedRow)}
    return [
        ObservedRow(**{k: v for k, v in row.items() if k in names})
        if isinstance(row, dict)
        else ObservedRow()
        for row in rows
    ]


@dataclass(frozen=True)
class _Parsed:
    values: dict[str, Any]
    reasons: dict[str, Reason]


def _parse(row: ObservedRow, institution_slug: str) -> _Parsed:
    raw = {
        "date": row.occurred_at,
        "kind": row.kind,
        "amount": row.amount,
        "balance_after": row.balance_after,
        "description": row.description,
        "account_mask": row.account_mask,
    }
    parsers = {
        "date": lambda v: to_datetime_utc(str(v)).date(),
        "kind": lambda v: v if v in ("debit", "credit") else _reject(),
        "amount": lambda v: require_decimal(v, field="amount"),
        "balance_after": lambda v: require_decimal(v, field="balance_after"),
        "description": lambda v: str(v).strip().casefold(),
        "account_mask": lambda v: normalize_account_mask(str(v), institution_slug),
    }
    values: dict[str, Any] = {}
    reasons: dict[str, Reason] = {}
    for name in FIELDS:
        if raw[name] is None:
            reasons[name] = "missing"
            continue
        try:
            values[name] = parsers[name](raw[name])
        except _PARSE_ERRORS:
            reasons[name] = "malformed"
    return _Parsed(values, reasons)


def _reject() -> None:
    raise ValueError("not a transaction kind")


def _expected(row: GoldenRow) -> dict[str, Any]:
    return {
        "date": date.fromisoformat(row.date),
        "kind": row.kind,
        "amount": row.amount,
        "balance_after": row.balance_after,
        "description": row.description.strip().casefold(),
        "account_mask": row.account_mask,
    }


def _pair(golden: list[dict[str, Any]], observed: list[_Parsed]) -> dict[int, int]:
    pairs: dict[int, int] = {}
    used: set[int] = set()
    for g_idx, g in enumerate(golden):
        for o_idx, o in enumerate(observed):
            if o_idx in used:
                continue
            if o.values.get("date") == g["date"] and o.values.get("amount") == g["amount"]:
                pairs[g_idx] = o_idx
                used.add(o_idx)
                break
    left_g = [i for i in range(len(golden)) if i not in pairs]
    left_o = [i for i in range(len(observed)) if i not in used and "date" in observed[i].values]
    g_dates = Counter(golden[i]["date"] for i in left_g)
    o_dates = Counter(observed[i].values["date"] for i in left_o)
    for g_idx in left_g:
        day = golden[g_idx]["date"]
        if g_dates[day] == 1 and o_dates[day] == 1:
            pairs[g_idx] = next(i for i in left_o if observed[i].values["date"] == day)
    return pairs


def _chain_breaks(observed: list[_Parsed]) -> tuple[int, ...]:
    breaks = []
    for i in range(1, len(observed)):
        prev, cur = observed[i - 1].values, observed[i].values
        if (
            not {"balance_after"} <= prev.keys()
            or not {"balance_after", "amount", "kind"} <= cur.keys()
        ):
            continue
        sign = Decimal(1) if cur["kind"] == "credit" else Decimal(-1)
        if prev["balance_after"] + sign * cur["amount"] != cur["balance_after"]:
            breaks.append(i)
    return tuple(breaks)


def score(
    golden: list[GoldenRow],
    observed: list[ObservedRow],
    *,
    institution_slug: str,
    production_accepted: bool,
) -> Report:
    expected = [_expected(row) for row in golden]
    parsed = [_parse(row, institution_slug) for row in observed]
    pairs = _pair(expected, parsed)
    paired_obs = set(pairs.values())
    missed = tuple(i for i in range(len(golden)) if i not in pairs)
    invented = tuple(i for i in range(len(observed)) if i not in paired_obs)

    findings: list[Finding] = []
    correct = Counter[str]()
    for g_idx, o_idx in sorted(pairs.items()):
        obs = parsed[o_idx]
        for name in FIELDS:
            if name in obs.reasons:
                findings.append(Finding("observed", o_idx, name, obs.reasons[name]))
            elif obs.values[name] == expected[g_idx][name]:
                correct[name] += 1
            else:
                findings.append(Finding("observed", o_idx, name, "wrong"))
    for g_idx in missed:
        findings.extend(Finding("golden", g_idx, name, "missing") for name in FIELDS)
    for o_idx in invented:
        obs = parsed[o_idx]
        findings.extend(
            Finding("observed", o_idx, name, obs.reasons.get(name, "invented")) for name in FIELDS
        )

    total = len(golden) + len(invented)
    return Report(
        fields={name: FieldResult(correct[name], total, FLOORS[name]) for name in FIELDS},
        paired=len(pairs),
        missed=missed,
        invented=invented,
        chain_breaks=_chain_breaks(parsed),
        findings=tuple(findings),
        production_accepted=production_accepted,
    )
