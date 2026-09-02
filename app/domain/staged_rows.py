"""Pure helpers for the parse-confirm screen (KCH-52 / AA-17).

Low-confidence flagging and the Decimal-safe payload JSON codec shared by
`app.db.queries.staged_rows` and `app.db.queries.silver` — no I/O here.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

# Below this, a row is highlighted for review on the confirm screen
# (docs/vault/10-mental-models/LLM-as-Parser-not-Oracle.md: "rows below
# threshold highlighted"). The LLM tier's own prompt (worker/extract/llm_tier.py)
# promises 1.0 only when a field is unambiguous in the source text — anything
# meaningfully lower means the model had to interpret an unclear layout.
LOW_CONFIDENCE_THRESHOLD = 0.8


def is_low_confidence(confidence: float | None) -> bool:
    """`None` counts as low-confidence too: an absent signal is a reason to
    ask the user, not to assume the best, per CLAUDE.md's provenance-first
    priority. Deterministic-adapter and manual-correction rows always carry
    `confidence=1.0` (`worker.adapters.base.StagedRowDraft`'s default / this
    module's own edit path), so only `method="llm"` rows are ever flagged in
    practice.
    """
    return confidence is None or confidence < LOW_CONFIDENCE_THRESHOLD


def _json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"not JSON serializable: {value!r}")


def encode_payload(payload: dict[str, Any]) -> str:
    """Encode a staged-row payload for the `jsonb` column, without ever
    routing a `Decimal` money/quantity field through `float` (CLAUDE.md hard
    rule #4) — `json.dumps` has no native `Decimal` support, so it is encoded
    as its exact string form instead."""
    return json.dumps(payload, default=_json_default)


def decode_payload(raw: str | dict[str, Any]) -> dict[str, Any]:
    """Decode a payload read back from `jsonb`. asyncpg returns `jsonb`
    columns as plain `str` (no codec is registered on the pool), so this
    normally parses JSON; a plain `dict` (already decoded, e.g. in a test)
    passes through unchanged. Decimal-shaped fields come back as JSON
    strings/numbers, not `Decimal` — callers that need `Decimal` back run the
    relevant field through `worker.adapters.base.to_decimal`, same as every
    adapter already does for its own input parsing.
    """
    return json.loads(raw) if isinstance(raw, str) else raw
