"""Golden-set eval for the LLM fallback tier (KCH-51 / AA-16) — a real call
through LiteLLM (Groq today, vLLM once AA-33 promotes it).

Only runs when `LITELLM_BASE_URL` is set. `.github/workflows/ci.yml`'s
`pytest -v` never sets it, so this collects there and auto-skips instead of
failing every PR on a live network dependency; `.github/workflows/llm-evals.yml`
starts a real LiteLLM container against the `GROQ_API_KEY`/`LITELLM_MASTER_KEY`
repo secrets and sets it, so it actually runs there. Input is the fabricated
`data/samples/` fixture — never real user data.
"""

from __future__ import annotations

import os

import pytest

from tests.evals.golden_set import PDF_FIXTURE, golden_chequing_rows
from worker.extract.llm_tier import extract_transactions
from worker.extract.pdfplumber_tier import extract as extract_pdf

MIN_FIELD_ACCURACY = 0.75

# Fields scored per row. Keep in step with `checks` below — the missing/extra-row
# penalty multiplies by this, so drift here silently rescales the denominator.
FIELDS_PER_ROW = 6

pytestmark = [
    pytest.mark.llm_eval,
    pytest.mark.skipif(
        not os.environ.get("LITELLM_BASE_URL"),
        reason="LITELLM_BASE_URL not set — no live LiteLLM endpoint to eval against",
    ),
]


def test_scotiabank_statement_golden_set() -> None:
    extracted = extract_pdf(PDF_FIXTURE.read_bytes())
    result = extract_transactions(extracted.text, institution_slug="scotia")
    golden = golden_chequing_rows()

    assert result.drafts, "LLM tier returned no transaction drafts for the golden-set input"

    matched = 0
    total_checks = 0
    for expected, draft in zip(golden, result.drafts, strict=False):
        payload = draft.payload
        checks = [
            str(payload.get("occurred_at", "")).startswith(expected.date),
            payload.get("kind") == expected.kind,
            payload.get("amount") == expected.amount,
            payload.get("balance_after") == expected.balance_after,
            # Case/whitespace are the model's to vary; the merchant text is not.
            str(payload.get("description", "")).strip().casefold()
            == expected.description.strip().casefold(),
            # The mask is what ties a row to an account — a wrong one misfiles the
            # transaction entirely, so it is scored, not assumed.
            payload.get("account_mask") == expected.account_mask,
        ]
        assert len(checks) == FIELDS_PER_ROW
        matched += sum(checks)
        total_checks += len(checks)

    # Rows the model missed or invented entirely still count against accuracy,
    # rather than silently shrinking the denominator.
    total_checks += abs(len(golden) - len(result.drafts)) * FIELDS_PER_ROW
    accuracy = matched / total_checks if total_checks else 0.0

    print(f"golden-set field accuracy: {accuracy:.2%} ({matched}/{total_checks})")
    assert accuracy >= MIN_FIELD_ACCURACY, (
        f"LLM extraction field accuracy {accuracy:.2%} fell below the "
        f"{MIN_FIELD_ACCURACY:.0%} floor for the Scotiabank golden set"
    )
