"""Golden-set scorer (AA-59, #42): deterministic, strict on numbers.

Exercises the known ways the old positional/0.75 scorer let a bad model pass
(docs/vault/50-decisions-log/2026-10-09-sovereign-ai-interview.md, "Facts
gathered before round 2") plus the cases spec #41 adds. Offline: no model,
no network; observed rows are built from the fabricated golden fixture.
"""

from __future__ import annotations

import json
from decimal import Decimal

from tests.evals.golden_set import GoldenRow, golden_chequing_rows
from tests.evals.scorer import ObservedRow, observed_rows_from_content, score

SLUG = "scotia"


def _observed(row: GoldenRow, **overrides: object) -> ObservedRow:
    values: dict[str, object] = {
        "occurred_at": row.date,
        "kind": row.kind,
        "amount": f"{row.amount:.2f}",
        "balance_after": f"{row.balance_after:.2f}",
        "description": row.description,
        "account_mask": row.account_mask,
    }
    values.update(overrides)
    return ObservedRow(**values)


def _perfect() -> list[ObservedRow]:
    return [_observed(row) for row in golden_chequing_rows()]


def _run(
    observed: list[ObservedRow], *, accepted: bool = True, golden: list[GoldenRow] | None = None
):
    return score(
        golden if golden is not None else golden_chequing_rows(),
        observed,
        institution_slug=SLUG,
        production_accepted=accepted,
    )


def test_perfect_extraction_passes() -> None:
    report = _run(_perfect())
    assert report.passed, report.failures()
    assert report.chain_breaks == ()


def test_every_kind_flipped_fails_kind_and_chain() -> None:
    flip = {"debit": "credit", "credit": "debit"}
    observed = [_observed(row, kind=flip[row.kind]) for row in golden_chequing_rows()]
    report = _run(observed)
    assert not report.passed
    assert not report.fields["kind"].passed
    assert report.chain_breaks


def test_every_amount_off_by_one_dollar_fails_amount() -> None:
    observed = [_observed(row, amount=f"{row.amount + 1:.2f}") for row in golden_chequing_rows()]
    report = _run(observed)
    assert not report.passed
    assert not report.fields["amount"].passed


def test_one_invented_row_fails() -> None:
    golden = golden_chequing_rows()
    extra = _observed(
        golden[-1], occurred_at="2026-07-31", amount="12.34", description="NOT ON STATEMENT"
    )
    report = _run(_perfect() + [extra])
    assert not report.passed
    assert report.invented == (len(golden),)


def test_dropped_first_row_misses_only_that_row() -> None:
    report = _run(_perfect()[1:])
    assert not report.passed
    assert report.missed == (0,)
    assert {f.index for f in report.findings if f.side == "golden"} == {0}
    assert report.paired == len(golden_chequing_rows()) - 1


def test_comma_amount_is_a_malformed_miss_not_a_crash() -> None:
    observed = _perfect()
    observed[0] = _observed(golden_chequing_rows()[0], amount="3,450.00")
    report = _run(observed)
    assert not report.fields["amount"].passed
    assert any(f.field == "amount" and f.reason == "malformed" for f in report.findings)


def test_yearless_date_is_malformed_and_cannot_pair() -> None:
    observed = _perfect()
    observed[0] = _observed(golden_chequing_rows()[0], occurred_at="Jul 02")
    report = _run(observed)
    assert not report.fields["date"].passed
    assert report.missed == (0,)
    assert report.invented == (0,)
    assert any(
        f.side == "observed" and f.field == "date" and f.reason == "malformed"
        for f in report.findings
    )


def test_same_day_rows_pair_by_amount_not_position() -> None:
    golden = [
        GoldenRow(
            "2026-07-02", "credit", Decimal("100.00"), Decimal("100.00"), "scotia-...4821", "A"
        ),
        GoldenRow("2026-07-02", "debit", Decimal("40.00"), Decimal("60.00"), "scotia-...4821", "B"),
    ]
    observed = [_observed(golden[1]), _observed(golden[0])]
    report = _run(observed, golden=golden)
    assert report.paired == 2
    assert all(result.correct == result.total for result in report.fields.values())


def test_mid_statement_amount_balance_swap_fails_amount_and_chain() -> None:
    golden = golden_chequing_rows()
    observed = _perfect()
    row = golden[3]
    observed[3] = _observed(
        row, amount=f"{row.balance_after:.2f}", balance_after=f"{row.amount:.2f}"
    )
    report = _run(observed)
    assert not report.fields["amount"].passed
    assert not report.fields["balance_after"].passed
    assert report.chain_breaks


def test_refused_by_production_fails_even_with_correct_fields() -> None:
    report = _run(_perfect(), accepted=False)
    assert all(result.passed for result in report.fields.values())
    assert not report.passed
    assert any("production" in reason for reason in report.failures())


def test_description_may_miss_one_row() -> None:
    observed = _perfect()
    observed[2] = _observed(golden_chequing_rows()[2], description="MORTGAGE")
    report = _run(observed)
    assert report.fields["description"].passed
    assert report.passed


def test_report_contains_no_fixture_values() -> None:
    golden = golden_chequing_rows()
    observed = _perfect()
    observed[0] = _observed(golden[0], amount="3,450.00", description="WRONG TEXT")
    observed[1] = _observed(
        golden[1], amount=f"{golden[2].amount:.2f}"
    )  # wrong, but a real fixture amount
    observed[4] = _observed(golden[4], kind="debit")
    report = _run(observed[:-1])
    text = (json.dumps(report.to_dict()) + "\n".join(report.failures())).casefold()
    for row in golden:
        for value in (
            str(row.amount),
            f"{row.balance_after:.2f}",
            row.date,
            row.description,
            row.account_mask,
        ):
            assert value.casefold() not in text, f"report leaked a fixture value: {value!r}"
    assert "wrong text" not in text and "3,450.00" not in text


def test_observed_rows_from_unparseable_content_is_empty() -> None:
    assert observed_rows_from_content(None) == []
    assert observed_rows_from_content("not json") == []
    assert observed_rows_from_content(json.dumps({"rows": []})) == []
    rows = observed_rows_from_content(json.dumps({"transactions": [{"amount": "1.00"}, "junk"]}))
    assert len(rows) == 2 and rows[0].amount == "1.00" and rows[1].amount is None
