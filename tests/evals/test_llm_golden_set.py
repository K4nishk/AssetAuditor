"""Golden-set eval for the LLM fallback tier (KCH-51 / AA-16) — a real call
through LiteLLM (Groq today, vLLM once AA-33 promotes it).

Only runs when `LITELLM_BASE_URL` is set. `.github/workflows/ci.yml`'s
`pytest -v` never sets it, so this collects there and auto-skips instead of
failing every PR on a live network dependency; `.github/workflows/llm-evals.yml`
starts a real LiteLLM container against the `GROQ_API_KEY` repo secret and sets
it, so it actually runs there. Input is the fabricated `data/samples/` fixture
— never real user data.

Every run writes its verdict to `$GITHUB_STEP_SUMMARY` and to
`$EVAL_REPORT_FILE`, pass or fail. That is deliberate: Actions job logs need an
authenticated session with admin rights on the repo to read, while the run
page's summary and annotations do not, so a score that only ever reached stdout
left the one person most likely to triage this — anyone without repo admin —
unable to tell a real extraction regression from a rate limit. The workflow
turns `$EVAL_REPORT_FILE` into an `::error::` annotation on failure.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.evals.golden_set import PDF_FIXTURE, GoldenRow, golden_chequing_rows
from worker.extract.llm_tier import extract_transactions
from worker.extract.pdfplumber_tier import extract as extract_pdf

MIN_FIELD_ACCURACY = 0.75

# Fields scored per row. Keep in step with `_score_row` below — the
# missing/extra-row penalty multiplies by this, so drift here silently
# rescales the denominator.
FIELDS_PER_ROW = 6

pytestmark = [
    pytest.mark.llm_eval,
    pytest.mark.skipif(
        not os.environ.get("LITELLM_BASE_URL"),
        reason="LITELLM_BASE_URL not set — no live LiteLLM endpoint to eval against",
    ),
]


def _score_row(expected: GoldenRow, payload: dict[str, object]) -> dict[str, bool]:
    """Per-field verdicts for one row, keyed by field name.

    Keyed rather than a bare list so a failure can say *which* field the model
    lost. A uniform miss on one field is a different bug from scattered misses
    across all six: the first points at the prompt or the schema, the second at
    the model or the fixture's layout.
    """
    return {
        "occurred_at": str(payload.get("occurred_at", "")).startswith(expected.date),
        "kind": payload.get("kind") == expected.kind,
        "amount": payload.get("amount") == expected.amount,
        "balance_after": payload.get("balance_after") == expected.balance_after,
        # Case/whitespace are the model's to vary; the merchant text is not.
        "description": str(payload.get("description", "")).strip().casefold()
        == expected.description.strip().casefold(),
        # The mask is what ties a row to an account — a wrong one misfiles the
        # transaction entirely, so it is scored, not assumed.
        "account_mask": payload.get("account_mask") == expected.account_mask,
    }


def _publish(headline: str, detail: str = "") -> None:
    """Record the verdict everywhere a triager can read it without repo admin.

    `$GITHUB_STEP_SUMMARY` renders on the run page; `$EVAL_REPORT_FILE` is what
    the workflow re-emits as an annotation. Both are absent outside CI, where
    stdout is enough. Appends (never truncates) so a re-run under the same
    summary file does not erase the earlier attempt.
    """
    print(headline)
    if detail:
        print(detail)
    for var in ("GITHUB_STEP_SUMMARY", "EVAL_REPORT_FILE"):
        path = os.environ.get(var)
        if not path:
            continue
        body = f"{headline}\n\n{detail}\n" if detail else f"{headline}\n"
        try:
            with Path(path).open("a", encoding="utf-8") as handle:
                handle.write(body)
        except OSError as exc:
            # Never raise from here. This runs inside the `except` handler
            # below, so an unwritable path (typo, missing parent, read-only
            # mount) would otherwise discard the real failure — a Groq 429, a
            # decommissioned model — and surface a file error in its place.
            # That is the exact diagnostic blackout this helper exists to end.
            print(f"could not write the verdict to ${var}: {exc}")


def test_scotiabank_statement_golden_set() -> None:
    extracted = extract_pdf(PDF_FIXTURE.read_bytes())
    golden = golden_chequing_rows()

    # A tier that raises never reaches a score, and that is the failure mode
    # this job actually hits: a Groq 401/429, a decommissioned model, or the
    # model echoing the statement's own `'3,450.00'` into a Decimal field all
    # land here, not below. Publishing the exception means the run page says
    # which of those it was instead of only "exit code 1".
    try:
        result = extract_transactions(extracted.text, institution_slug="scotia")
    except Exception as exc:
        _publish(
            f"golden-set eval ERRORED before scoring: {type(exc).__name__}: {exc}",
            "No score was produced — this is an extraction/transport failure, "
            "not a threshold miss.",
        )
        raise

    matched = 0
    total_checks = 0
    per_field: dict[str, int] = {}
    for expected, draft in zip(golden, result.drafts, strict=False):
        checks = _score_row(expected, draft.payload)
        assert len(checks) == FIELDS_PER_ROW
        for name, ok in checks.items():
            per_field[name] = per_field.get(name, 0) + int(ok)
        matched += sum(checks.values())
        total_checks += len(checks)

    # Rows the model missed or invented entirely still count against accuracy,
    # rather than silently shrinking the denominator.
    row_delta = abs(len(golden) - len(result.drafts))
    total_checks += row_delta * FIELDS_PER_ROW
    accuracy = matched / total_checks if total_checks else 0.0

    scored_rows = min(len(golden), len(result.drafts))
    verdict = "PASSED" if accuracy >= MIN_FIELD_ACCURACY else "FAILED"
    headline = (
        f"golden-set eval {verdict}: {accuracy:.2%} field accuracy "
        f"(needed {MIN_FIELD_ACCURACY:.0%}) — {matched}/{total_checks} checks, "
        f"backend {result.extraction_backend}"
    )
    row_note = (
        f" ({row_delta} unmatched, penalised {row_delta * FIELDS_PER_ROW} checks)"
        if row_delta
        else ""
    )
    field_note = (
        ", ".join(f"{name} {per_field[name]}/{scored_rows}" for name in sorted(per_field))
        or "none — the model returned no rows to score"
    )
    detail_lines = [
        f"rows: {len(result.drafts)} extracted vs {len(golden)} golden{row_note}",
        f"per-field hits (of {scored_rows} scored rows): {field_note}",
    ]
    _publish(headline, "\n".join(detail_lines))

    assert accuracy >= MIN_FIELD_ACCURACY, headline
