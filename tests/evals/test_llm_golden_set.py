"""Golden-set eval for the LLM fallback tier (KCH-51 / AA-16, scorer AA-59) — a
real call through LiteLLM (Groq today, vLLM once AA-60 adds the local groups).

Only runs when `LITELLM_BASE_URL` is set. `.github/workflows/ci.yml`'s
`pytest -v` never sets it, so this collects there and auto-skips instead of
failing every PR on a live network dependency; `.github/workflows/llm-evals.yml`
starts a real LiteLLM container against the `GROQ_API_KEY`/`LITELLM_MASTER_KEY`
repo secrets and sets it, so it actually runs there. Input is the fabricated
`data/samples/` fixture — never real user data.

Scoring is `tests.evals.scorer` (deterministic, strict on numbers). The model's
raw JSON is recorded through a wrapping client, so a response the production
parser refuses still gets a per-field report instead of an opaque error, and
the run fails because the worker would not stage it.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Any

import openai
import pytest

from tests.evals.golden_set import PDF_FIXTURE, golden_chequing_rows
from tests.evals.scorer import observed_rows_from_content, score
from worker.extract.llm_tier import LlmExtractionError, extract_transactions
from worker.extract.pdfplumber_tier import extract as extract_pdf

pytestmark = [
    pytest.mark.llm_eval,
    pytest.mark.skipif(
        not os.environ.get("LITELLM_BASE_URL"),
        reason="LITELLM_BASE_URL not set — no live LiteLLM endpoint to eval against",
    ),
]


class _RecordingClient:
    """Delegates to a real OpenAI client and keeps each response's raw content.

    Exposes the inner `base_url`, so the tier's endpoint allowlist still checks
    it, and wraps both `create` and `with_raw_response.create` so it works
    whichever call style the tier uses (AA-61 reads the backend header through
    the raw-response API).
    """

    def __init__(self, inner: openai.OpenAI) -> None:
        self.base_url = inner.base_url
        self.contents: list[str | None] = []
        self._inner = inner
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=self._create,
                with_raw_response=SimpleNamespace(create=self._create_raw),
            )
        )

    def _record(self, response: Any) -> None:
        self.contents.append(response.choices[0].message.content if response.choices else None)

    def _create(self, **kwargs: Any) -> Any:
        response = self._inner.chat.completions.create(**kwargs)
        self._record(response)
        return response

    def _create_raw(self, **kwargs: Any) -> Any:
        raw = self._inner.chat.completions.with_raw_response.create(**kwargs)
        self._record(raw.parse())
        return raw


def test_scotiabank_statement_golden_set() -> None:
    client = _RecordingClient(
        openai.OpenAI(
            base_url=os.environ["LITELLM_BASE_URL"],
            api_key=os.environ.get("LITELLM_API_KEY", "sk-litellm-placeholder"),
        )
    )
    extracted = extract_pdf(PDF_FIXTURE.read_bytes())
    try:
        extract_transactions(extracted.text, institution_slug="scotia", client=client)
        accepted = True
    except LlmExtractionError:
        accepted = False

    observed = observed_rows_from_content(client.contents[-1] if client.contents else None)
    report = score(
        golden_chequing_rows(), observed, institution_slug="scotia", production_accepted=accepted
    )

    # Indexes, field names and reasons only — safe to print in a CI log.
    print("golden-set report:", json.dumps(report.to_dict(), sort_keys=True))
    assert report.passed, "golden set failed: " + "; ".join(report.failures())
