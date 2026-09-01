"""Unit tests for the LiteLLM fallback extraction tier (KCH-51 / AA-16).

No network/LiteLLM/Groq involved here — a fake OpenAI-SDK-shaped client
stands in for the real one so these run offline, same as every other unit
test in this repo. The live golden-set eval against a real LiteLLM endpoint
lives in `tests/evals/test_llm_golden_set.py` and only runs in
`.github/workflows/llm-evals.yml`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest

from worker.extract.llm_tier import (
    MODEL_GROUP,
    LlmExtractionError,
    extract_transactions,
    lineage_facets,
)

RAW_TEXT = (
    "Scotiabank Statement\n"
    "Account Number: 4041234567894821\n"
    "Customer: Alex Mock\n"
    "Date       Description              Withdrawn  Deposited  Balance\n"
    "Jul 02     PAYROLL DEPOSIT ACME CORP            3450.00   5120.45\n"
    "Jul 03     E-TRANSFER TO WS HISA    1000.00                4120.45\n"
)


def _row(
    *,
    occurred_at: str = "2026-07-02",
    description: str = "PAYROLL DEPOSIT ACME CORP",
    kind: str = "credit",
    amount: str = "3450.00",
    balance_after: str | None = "5120.45",
    account_mask: str | None = "scotia-...4821",
    confidence: dict[str, float] | None = None,
) -> dict[str, Any]:
    return {
        "occurred_at": occurred_at,
        "description": description,
        "kind": kind,
        "amount": amount,
        "balance_after": balance_after,
        "account_mask": account_mask,
        "confidence": confidence
        or {
            "occurred_at": 1.0,
            "description": 1.0,
            "kind": 1.0,
            "amount": 1.0,
            "balance_after": 0.8,
            "account_mask": 1.0,
        },
    }


@dataclass
class _FakeMessage:
    content: str | None


@dataclass
class _FakeChoice:
    message: _FakeMessage


@dataclass
class _FakeResponse:
    choices: list[_FakeChoice]
    model: str = "groq/llama-3.3-70b-versatile"


@dataclass
class _FakeCompletions:
    response: _FakeResponse | None = None
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


@dataclass
class _FakeChat:
    completions: _FakeCompletions


class FakeClient:
    def __init__(
        self,
        transactions: list[dict[str, Any]],
        *,
        model: str = "groq/llama-3.3-70b-versatile",
    ):
        content = json.dumps({"transactions": transactions})
        response = _FakeResponse(choices=[_FakeChoice(_FakeMessage(content))], model=model)
        self.chat = _FakeChat(completions=_FakeCompletions(response=response))

    @property
    def last_call(self) -> dict[str, Any]:
        return self.chat.completions.calls[-1]


# --- request shape ------------------------------------------------------------


def test_masks_raw_text_before_sending() -> None:
    client = FakeClient([_row()])
    extract_transactions(RAW_TEXT, institution_slug="scotia", client=client)

    sent_user_message = client.last_call["messages"][1]["content"]
    assert "4041234567894821" not in sent_user_message
    assert "alex mock" not in sent_user_message.lower()
    assert "scotia-...4821" in sent_user_message


def test_calls_extractor_model_group_at_temperature_zero() -> None:
    client = FakeClient([_row()])
    extract_transactions(RAW_TEXT, institution_slug="scotia", client=client)

    call = client.last_call
    assert call["model"] == MODEL_GROUP == "extractor"
    assert call["temperature"] == 0


def test_requests_strict_json_schema_output() -> None:
    client = FakeClient([_row()])
    extract_transactions(RAW_TEXT, institution_slug="scotia", client=client)

    response_format = client.last_call["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True


# --- response parsing -----------------------------------------------------------


def test_parses_valid_response_into_transaction_drafts() -> None:
    client = FakeClient(
        [
            _row(),
            _row(
                occurred_at="2026-07-03",
                description="E-TRANSFER TO WS HISA",
                kind="debit",
                amount="1000.00",
                balance_after="4120.45",
            ),
        ]
    )
    result = extract_transactions(RAW_TEXT, institution_slug="scotia", client=client)

    assert len(result.drafts) == 2
    first = result.drafts[0]
    assert first.entity == "transaction"
    assert first.method == "llm"
    assert first.payload["amount"] == Decimal("3450.00")
    assert first.payload["balance_after"] == Decimal("5120.45")
    assert first.payload["kind"] == "credit"
    assert first.payload["account_mask"] == "scotia-...4821"
    assert first.payload["currency"] == "CAD"
    assert isinstance(first.payload["amount"], Decimal)


def test_row_confidence_is_min_of_field_confidences() -> None:
    confidence = {
        "occurred_at": 1.0,
        "description": 0.4,
        "kind": 1.0,
        "amount": 1.0,
        "balance_after": 1.0,
        "account_mask": 1.0,
    }
    client = FakeClient([_row(confidence=confidence)])
    result = extract_transactions(RAW_TEXT, institution_slug="scotia", client=client)

    assert result.drafts[0].confidence == pytest.approx(0.4)
    assert result.drafts[0].payload["field_confidence"]["description"] == pytest.approx(0.4)


def test_account_mask_override_wins_over_model_output() -> None:
    client = FakeClient([_row(account_mask="scotia-...9999")])
    result = extract_transactions(
        RAW_TEXT, institution_slug="scotia", account_mask="scotia-...4821", client=client
    )

    assert result.drafts[0].payload["account_mask"] == "scotia-...4821"


def test_null_account_mask_and_balance_after_are_preserved() -> None:
    client = FakeClient([_row(account_mask=None, balance_after=None)])
    result = extract_transactions(RAW_TEXT, institution_slug="scotia", client=client)

    assert result.drafts[0].payload["account_mask"] is None
    assert result.drafts[0].payload["balance_after"] is None


def test_extraction_backend_reads_groq_prefix() -> None:
    client = FakeClient([_row()], model="groq/llama-3.3-70b-versatile")
    result = extract_transactions(RAW_TEXT, institution_slug="scotia", client=client)
    assert result.extraction_backend == "groq"


def test_extraction_backend_reads_vllm_prefix() -> None:
    client = FakeClient([_row()], model="hosted_vllm/some-local-model")
    result = extract_transactions(RAW_TEXT, institution_slug="scotia", client=client)
    assert result.extraction_backend == "vllm"


def test_lineage_facets_carries_backend_and_method() -> None:
    client = FakeClient([_row()], model="groq/llama-3.3-70b-versatile")
    result = extract_transactions(RAW_TEXT, institution_slug="scotia", client=client)
    assert lineage_facets(result) == {"extraction_method": "llm", "extraction_backend": "groq"}


# --- malformed responses ---------------------------------------------------------


def test_raises_on_empty_content() -> None:
    client = FakeClient([])
    client.chat.completions.response.choices[0].message.content = ""
    with pytest.raises(LlmExtractionError, match="no message content"):
        extract_transactions(RAW_TEXT, institution_slug="scotia", client=client)


def test_raises_on_invalid_json() -> None:
    client = FakeClient([])
    client.chat.completions.response.choices[0].message.content = "not json"
    with pytest.raises(LlmExtractionError, match="not valid JSON"):
        extract_transactions(RAW_TEXT, institution_slug="scotia", client=client)


def test_raises_on_missing_transactions_key() -> None:
    client = FakeClient([])
    client.chat.completions.response.choices[0].message.content = json.dumps({"rows": []})
    with pytest.raises(LlmExtractionError, match="transactions"):
        extract_transactions(RAW_TEXT, institution_slug="scotia", client=client)


def test_raises_on_malformed_row_missing_field() -> None:
    bad_row = _row()
    del bad_row["amount"]
    client = FakeClient([bad_row])
    with pytest.raises(LlmExtractionError, match="malformed transaction row"):
        extract_transactions(RAW_TEXT, institution_slug="scotia", client=client)


def test_raises_on_non_decimal_amount() -> None:
    client = FakeClient([_row(amount="not-a-number")])
    with pytest.raises(LlmExtractionError, match="malformed transaction row"):
        extract_transactions(RAW_TEXT, institution_slug="scotia", client=client)


def test_raises_on_invalid_kind() -> None:
    client = FakeClient([_row(kind="transfer")])
    with pytest.raises(LlmExtractionError, match="invalid transaction kind"):
        extract_transactions(RAW_TEXT, institution_slug="scotia", client=client)
