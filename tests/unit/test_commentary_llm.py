"""Unit tests for the commentary LLM tier (KCH-62 / AA-25).

No network/LiteLLM/Groq involved — a fake OpenAI-SDK-shaped client stands in
for the real one, same convention as `tests/unit/test_llm_tier.py`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import openai
import pytest

from worker.commentary import (
    MODEL_GROUP,
    AuditCommentaryError,
    LlmEndpointNotApprovedError,
    _client,
    _resolve_client,
    _validate_base_url,
    request_commentary,
)

FACTS_TEXT = (
    "As of 2026-07-31:\n"
    "Total assets: $10,000.00 CAD\n"
    "Total liabilities: $2,000.00 CAD\n"
    "Net worth: $8,000.00 CAD\n"
)


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
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        assert self.response is not None
        return self.response


@dataclass
class _FakeChat:
    completions: _FakeCompletions


class FakeClient:
    def __init__(self, observations: list[str], *, model: str = "groq/llama-3.3-70b-versatile"):
        content = json.dumps({"observations": observations})
        response = _FakeResponse(choices=[_FakeChoice(_FakeMessage(content))], model=model)
        self.chat = _FakeChat(completions=_FakeCompletions(response=response))
        self.base_url = "http://litellm:4000"

    @property
    def last_call(self) -> dict[str, Any]:
        return self.chat.completions.calls[-1]


def test_calls_commentary_model_group_at_temperature_zero():
    client = FakeClient(["Tech is 48% of your holdings."])
    request_commentary(FACTS_TEXT, client=client)

    call = client.last_call
    assert call["model"] == MODEL_GROUP == "commentary"
    assert call["temperature"] == 0


def test_requests_strict_json_schema_output():
    client = FakeClient(["Tech is 48% of your holdings."])
    request_commentary(FACTS_TEXT, client=client)

    response_format = client.last_call["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True


def test_sends_the_rendered_facts_text_as_the_user_message():
    client = FakeClient(["Tech is 48% of your holdings."])
    request_commentary(FACTS_TEXT, client=client)

    sent = client.last_call["messages"][1]["content"]
    assert sent == FACTS_TEXT


def test_parses_valid_response_into_observations():
    client = FakeClient(["Tech is 48% of your holdings.", "Net worth grew this month."])
    result = request_commentary(FACTS_TEXT, client=client)
    assert result.observations == [
        "Tech is 48% of your holdings.",
        "Net worth grew this month.",
    ]
    assert result.backend == "groq"


def test_extraction_backend_reads_vllm_prefix():
    client = FakeClient(["obs"], model="hosted_vllm/some-local-model")
    result = request_commentary(FACTS_TEXT, client=client)
    assert result.backend == "vllm"


def test_raises_on_empty_content():
    client = FakeClient([])
    client.chat.completions.response.choices[0].message.content = ""
    with pytest.raises(AuditCommentaryError, match="no message content"):
        request_commentary(FACTS_TEXT, client=client)


def test_raises_on_invalid_json():
    client = FakeClient([])
    client.chat.completions.response.choices[0].message.content = "not json"
    with pytest.raises(AuditCommentaryError, match="not valid JSON"):
        request_commentary(FACTS_TEXT, client=client)


def test_raises_on_missing_observations_key():
    client = FakeClient([])
    client.chat.completions.response.choices[0].message.content = json.dumps({"foo": []})
    with pytest.raises(AuditCommentaryError, match="observations"):
        request_commentary(FACTS_TEXT, client=client)


def test_raises_on_non_string_observation_entry():
    client = FakeClient([])
    client.chat.completions.response.choices[0].message.content = json.dumps(
        {"observations": ["fine", 5]}
    )
    with pytest.raises(AuditCommentaryError, match="observations"):
        request_commentary(FACTS_TEXT, client=client)


@pytest.mark.parametrize(
    "base_url",
    ["http://litellm:4000", "http://localhost:4000", "http://127.0.0.1:4000"],
)
def test_validate_base_url_accepts_approved_hosts(base_url: str):
    assert _validate_base_url(base_url) == base_url


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.groq.com/openai/v1",
        "http://litellm.attacker.example:4000",
        "http://litellm:4000@attacker.example",
    ],
)
def test_validate_base_url_rejects_unapproved_hosts(base_url: str):
    with pytest.raises(LlmEndpointNotApprovedError):
        _validate_base_url(base_url)


def test_client_rejects_unapproved_env_base_url(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LITELLM_BASE_URL", "https://api.groq.com/openai/v1")
    with pytest.raises(LlmEndpointNotApprovedError):
        _client()


def test_injected_sdk_client_pointed_at_a_provider_is_rejected():
    direct = openai.OpenAI(base_url="https://api.groq.com/openai/v1", api_key="sk-test")
    with pytest.raises(LlmEndpointNotApprovedError):
        request_commentary(FACTS_TEXT, client=direct)


def test_injected_sdk_client_pointed_at_the_router_is_accepted():
    routed = openai.OpenAI(base_url="http://litellm:4000", api_key="sk-test")
    assert _resolve_client(routed) is routed


def test_injected_client_without_a_base_url_is_rejected():
    double = FakeClient([])
    del double.base_url
    with pytest.raises(LlmEndpointNotApprovedError):
        request_commentary(FACTS_TEXT, client=double)
