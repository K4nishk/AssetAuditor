"""Extraction tier 3: LiteLLM fallback (masked input, JSON-schema, temp=0) — AA-16.

Last-resort tier for text-layer bank statements no institution adapter
recognizes (docs/vault/40-research/PDF-Statement-Parsing.md's fallback
ladder; the Docling structuring step it also describes is a future add-on,
not built yet — this tier accepts already-extracted text directly, e.g. from
`worker/extract/pdfplumber_tier.py`). The model only ever sees
`worker.masking.mask_statement_text` output: raw account numbers and PII
never leave the worker process. Requests go through the self-hosted LiteLLM
router (ADR v1.1.0 Decision B) at the `extractor` model group, which resolves
to Groq today and will resolve to vLLM once AA-33 promotes it — this module
never talks to a provider directly. Output is JSON-schema-constrained,
temperature 0, and staged with the model's own per-field confidence.

Nothing here writes to silver or claims the row is correct: AA-17's
confirm screen and AA-18's silver write are still required for every row
this tier produces, same as every other extraction method (`method="llm"`
on `StagedRowDraft` is exactly the signal AA-17 uses to highlight it).
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from typing import Any

import openai

from worker.adapters.base import (
    AdapterParseError,
    StagedRowDraft,
    normalize_account_mask,
    require_decimal,
    to_datetime_utc,
)
from worker.masking import mask_statement_text

MODEL_GROUP = "extractor"
DEFAULT_BASE_URL = "http://litellm:4000"

_CONFIDENCE_FIELDS = (
    "occurred_at",
    "description",
    "kind",
    "amount",
    "balance_after",
    "account_mask",
)

_TRANSACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "transactions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "occurred_at": {
                        "type": "string",
                        "description": "ISO 8601 date or datetime the transaction occurred",
                    },
                    "description": {"type": "string"},
                    "kind": {"type": "string", "enum": ["debit", "credit"]},
                    "amount": {
                        "type": "string",
                        "description": "decimal string, always positive, e.g. '182.35'",
                    },
                    "balance_after": {
                        "type": ["string", "null"],
                        "description": "running balance after this transaction, decimal string",
                    },
                    "account_mask": {
                        "type": ["string", "null"],
                        "description": (
                            "the masked account token as it appears in the text "
                            "(e.g. 'scotia-...4821'), or null if none is present"
                        ),
                    },
                    "confidence": {
                        "type": "object",
                        "description": "0.0-1.0 confidence per field; 1.0 only when unambiguous",
                        "properties": {
                            name: {"type": "number", "minimum": 0.0, "maximum": 1.0}
                            for name in _CONFIDENCE_FIELDS
                        },
                        "required": list(_CONFIDENCE_FIELDS),
                        "additionalProperties": False,
                    },
                },
                "required": [
                    "occurred_at",
                    "description",
                    "kind",
                    "amount",
                    "balance_after",
                    "account_mask",
                    "confidence",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["transactions"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = (
    "You extract bank statement transactions into structured JSON. The text you "
    "are given has already had account numbers and personal information masked "
    "or redacted — never ask for, invent, or reconstruct unmasked account "
    "numbers or personal details. Extract only transactions that are literally "
    "present in the text; never infer, estimate, or hallucinate a transaction "
    "that isn't there. Report a 0.0-1.0 confidence for every field: 1.0 only "
    "when the value is unambiguous in the source text, lower when you had to "
    "interpret an unclear layout, 0.0 if you could not determine it at all."
)


class LlmExtractionError(RuntimeError):
    """Raised when the LiteLLM response is missing, malformed, or fails schema validation."""


@dataclass(frozen=True)
class LlmExtractionResult:
    drafts: list[StagedRowDraft]
    extraction_backend: str


def lineage_facets(result: LlmExtractionResult) -> dict[str, Any]:
    """Facet payload for `worker.lineage.LineageEmitter` calls wrapping this tier.

    `extraction_backend` is the provenance detail ADR v1.1.0 §3 calls for —
    which provider (`vllm`|`groq`) actually served the extraction, since
    LiteLLM's routing/fallback means the caller can't know that in advance.
    """
    return {"extraction_method": "llm", "extraction_backend": result.extraction_backend}


def _client(*, base_url: str | None = None, api_key: str | None = None) -> openai.OpenAI:
    return openai.OpenAI(
        base_url=base_url or os.environ.get("LITELLM_BASE_URL", DEFAULT_BASE_URL),
        api_key=api_key or os.environ.get("LITELLM_API_KEY", "sk-litellm-placeholder"),
    )


def extract_transactions(
    raw_text: str,
    *,
    institution_slug: str,
    account_mask: str | None = None,
    client: openai.OpenAI | None = None,
) -> LlmExtractionResult:
    """Mask `raw_text` and extract transaction rows via the LiteLLM `extractor` group.

    `account_mask`, when the caller already knows it (e.g. an adapter parsed
    the statement header deterministically but not the transaction table),
    overrides whatever the model reports per-row rather than trusting the
    model to copy it correctly. Returns `transaction` `StagedRowDraft`s
    (`method="llm"`) with each row's `confidence` set to the minimum of that
    row's per-field confidences — the parse-confirm screen (AA-17) only needs
    one number to decide whether to highlight a row, but the full per-field
    breakdown survives in `payload["field_confidence"]` for anyone who wants it.
    """
    masked_text = mask_statement_text(raw_text, institution_slug)
    active_client = client or _client()

    response = active_client.chat.completions.create(
        model=MODEL_GROUP,
        temperature=0,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": masked_text},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "statement_transactions",
                "strict": True,
                "schema": _TRANSACTION_SCHEMA,
            },
        },
    )

    if not response.choices:
        raise LlmExtractionError("LiteLLM response had no choices")

    message = response.choices[0].message
    if message is None:
        raise LlmExtractionError("LiteLLM response choice had no message")

    content = message.content
    if not content:
        raise LlmExtractionError("LiteLLM response had no message content")

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LlmExtractionError(f"LiteLLM response was not valid JSON: {content!r}") from exc

    rows = parsed.get("transactions")
    if not isinstance(rows, list):
        raise LlmExtractionError(f"LiteLLM response missing a 'transactions' array: {parsed!r}")

    drafts = [
        _to_draft(row, institution_slug=institution_slug, account_mask_override=account_mask)
        for row in rows
    ]
    return LlmExtractionResult(drafts=drafts, extraction_backend=_extraction_backend(response))


def _validate_confidence(name: str, value: Any) -> float:
    """Coerce a per-field confidence to `float`, rejecting NaN/inf and anything
    outside `[0.0, 1.0]` — the schema's `minimum`/`maximum` only constrain a
    well-behaved model; this is the backstop for a provider that ignores them."""
    result = float(value)
    if not math.isfinite(result) or not (0.0 <= result <= 1.0):
        raise ValueError(f"confidence field {name!r} out of range [0.0, 1.0]: {value!r}")
    return result


def _to_draft(
    row: dict[str, Any], *, institution_slug: str, account_mask_override: str | None
) -> StagedRowDraft:
    try:
        confidence = row["confidence"]
        field_confidence = {
            name: _validate_confidence(name, confidence[name]) for name in _CONFIDENCE_FIELDS
        }
        occurred_at = to_datetime_utc(str(row["occurred_at"]))
        kind = row["kind"]
        amount = require_decimal(row["amount"], field="amount")
        balance_after = row["balance_after"]
        balance_after_decimal = (
            require_decimal(balance_after, field="balance_after")
            if balance_after is not None
            else None
        )
        description = str(row["description"])
        raw_account_mask = row["account_mask"]
        account_mask = account_mask_override
        if account_mask is None and raw_account_mask is not None:
            account_mask = normalize_account_mask(str(raw_account_mask), institution_slug)
    except (KeyError, ValueError, AdapterParseError) as exc:
        raise LlmExtractionError(f"malformed transaction row from LiteLLM: {row!r}") from exc

    if kind not in ("debit", "credit"):
        raise LlmExtractionError(f"invalid transaction kind {kind!r}: {row!r}")

    row_confidence = min(field_confidence.values())

    payload: dict[str, Any] = {
        "account_mask": account_mask,
        "kind": kind,
        "amount": amount,
        "currency": "CAD",
        "occurred_at": occurred_at.isoformat(),
        "description": description,
        "balance_after": balance_after_decimal,
        "field_confidence": field_confidence,
    }
    return StagedRowDraft(
        entity="transaction",
        payload=payload,
        confidence=row_confidence,
        method="llm",
    )


def _extraction_backend(response: Any) -> str:
    """Derive `vllm|groq` from the underlying model LiteLLM actually routed to.

    LiteLLM echoes the resolved `litellm_params.model` (e.g. `groq/llama-...`
    or `hosted_vllm/...`) back in the response's `model` field rather than the
    requested alias (`extractor`) — its provider prefix is the routing
    decision ADR v1.1.0 §3 wants recorded in lineage.
    """
    model = getattr(response, "model", None) or ""
    prefix = model.split("/", 1)[0].lower()
    if prefix in ("vllm", "hosted_vllm"):
        return "vllm"
    if prefix == "groq":
        return "groq"
    return prefix or "unknown"
