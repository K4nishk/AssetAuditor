"""Audit commentary: gold facts -> LLM observations -> `audit_commentary` (KCH-62 / AA-25).

Model group `commentary` (`llm/litellm.config.yaml`), same LiteLLM router as
`worker/extract/llm_tier.py`'s `extractor` group — ADR v1.1.0 §3: every LLM
call originates on the worker, next to LiteLLM's localhost-only listener,
never from the Vercel-hosted API. `_validate_base_url`/`_resolve_client`
duplicate `worker.extract.llm_tier`'s allowlist check rather than importing
its private names: two independent request-issuing modules each enforcing
the zero-cost-contract host allowlist is defense in depth, not drift, since
the two call sites diverge in every other respect (schema, prompt, model
group).

`app.domain.audit_commentary` owns every deterministic piece — rendering
gold facts into the prompt, and filtering the model's response for
advice-shaped language (mvp.md's AA-25: "never advice-shaped"). This module
is the thin I/O shell around that: build the facts snapshot from gold
tables, call the model, filter, persist, emit lineage.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlparse

import asyncpg
import openai

from app.db.queries import audit_commentary as commentary_queries
from app.db.queries import dashboard as dashboard_queries
from app.domain.audit_commentary import (
    DISCLOSURE_TEXT,
    DiversificationSlice,
    build_gold_facts_snapshot,
    filter_advice_shaped,
    render_facts_for_prompt,
)
from worker.lineage import LineageEmitter

logger = logging.getLogger("worker.commentary")

MODEL_GROUP = "commentary"
DEFAULT_BASE_URL = "http://litellm:4000"

# Same allowlist `worker.extract.llm_tier._APPROVED_BASE_URL_HOSTS` enforces —
# CLAUDE.md hard rule #6: a request that reaches a provider host directly
# bypasses LiteLLM's RPM/TPM caps, the zero-cost contract's only enforcement.
_APPROVED_BASE_URL_HOSTS = frozenset({"litellm", "localhost", "127.0.0.1"})

_COMMENTARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "observations": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["observations"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = (
    "You summarize a personal finance dashboard's numbers into short, plain-"
    "language observations. You are not a financial advisor and must never "
    "give advice, recommendations, or directives — no 'you should', "
    "'consider', 'I recommend', and never tell the user to buy, sell, "
    "rebalance, or move money anywhere. State only what the numbers show "
    "(concentrations, splits, changes) using the figures given to you; never "
    "invent a number that isn't present in the input. Write between 1 and "
    "6 short observations, one per array entry."
)


class AuditCommentaryError(RuntimeError):
    """Raised when the LiteLLM response is missing, malformed, or fails schema validation."""


class LlmEndpointNotApprovedError(RuntimeError):
    """Raised when `LITELLM_BASE_URL` (or an explicit override) does not resolve
    to the self-hosted LiteLLM router — see `_APPROVED_BASE_URL_HOSTS`."""


class NoCompliantObservationsError(RuntimeError):
    """Raised when every observation the model returned was filtered out by
    `app.domain.audit_commentary.filter_advice_shaped` — a card with zero
    observations is a failed generation, not a valid empty result, so this
    is treated the same as any other malformed response."""


@dataclass(frozen=True)
class CommentaryLlmResult:
    observations: list[str]
    backend: str


@dataclass(frozen=True)
class CommentaryResult:
    observations: list[str]
    disclosure: str
    model_backend: str
    run_id: str


def _validate_base_url(base_url: str) -> str:
    host = urlparse(base_url).hostname
    if host not in _APPROVED_BASE_URL_HOSTS:
        raise LlmEndpointNotApprovedError(
            f"LITELLM_BASE_URL {base_url!r} does not resolve to an approved "
            f"self-hosted LiteLLM endpoint (host must be one of "
            f"{sorted(_APPROVED_BASE_URL_HOSTS)}); refusing to send commentary "
            "requests directly to a provider."
        )
    return base_url


def _client(*, base_url: str | None = None, api_key: str | None = None) -> openai.OpenAI:
    resolved_base_url = base_url or os.environ.get("LITELLM_BASE_URL", DEFAULT_BASE_URL)
    return openai.OpenAI(
        base_url=_validate_base_url(resolved_base_url),
        api_key=api_key or os.environ.get("LITELLM_API_KEY", "sk-litellm-placeholder"),
    )


def _resolve_client(client: openai.OpenAI | None) -> openai.OpenAI:
    """Same fail-closed shape as `worker.extract.llm_tier._resolve_client`:
    an injected client that cannot state an approved `base_url` is refused
    rather than trusted."""
    if client is None:
        return _client()
    base_url = getattr(client, "base_url", None)
    if base_url is None:
        raise LlmEndpointNotApprovedError(
            "injected commentary client exposes no base_url to validate; refusing "
            "to send gold facts to an unverified endpoint."
        )
    _validate_base_url(str(base_url))
    return client


def _extraction_backend(response: Any) -> str:
    """Same `vllm|groq` derivation as `worker.extract.llm_tier._extraction_backend`."""
    model = getattr(response, "model", None) or ""
    prefix = model.split("/", 1)[0].lower()
    if prefix in ("vllm", "hosted_vllm"):
        return "vllm"
    if prefix == "groq":
        return "groq"
    return prefix or "unknown"


def request_commentary(
    facts_text: str, *, client: openai.OpenAI | None = None
) -> CommentaryLlmResult:
    """Call the `commentary` model group with `facts_text` (already-rendered
    gold facts, `app.domain.audit_commentary.render_facts_for_prompt`) and
    return its raw observations, unfiltered — `generate_audit_commentary`
    below runs `filter_advice_shaped` on the result before anything is
    persisted or shown."""
    active_client = _resolve_client(client)

    response = active_client.chat.completions.create(
        model=MODEL_GROUP,
        temperature=0,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": facts_text},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "audit_commentary",
                "strict": True,
                "schema": _COMMENTARY_SCHEMA,
            },
        },
    )

    if not response.choices:
        raise AuditCommentaryError("LiteLLM response had no choices")

    message = response.choices[0].message
    if message is None:
        raise AuditCommentaryError("LiteLLM response choice had no message")

    content = message.content
    if not content:
        raise AuditCommentaryError("LiteLLM response had no message content")

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise AuditCommentaryError(
            f"LiteLLM response was not valid JSON ({len(content)} chars)"
        ) from exc

    if not isinstance(parsed, dict):
        raise AuditCommentaryError(
            f"LiteLLM response root was not a JSON object (got {type(parsed).__name__})"
        )

    observations = parsed.get("observations")
    if not isinstance(observations, list) or not all(
        isinstance(entry, str) for entry in observations
    ):
        raise AuditCommentaryError("LiteLLM response missing a string 'observations' array")

    return CommentaryLlmResult(
        observations=observations, backend=_extraction_backend(response)
    )


def _diversification_slices(rows: list[asyncpg.Record]) -> list[DiversificationSlice]:
    return [
        DiversificationSlice(label=row["label"], amount_cad=row["amount_cad"]) for row in rows
    ]


async def generate_audit_commentary(
    conn: asyncpg.Connection,
    *,
    user_id: str,
    lineage: LineageEmitter,
    client: openai.OpenAI | None = None,
) -> CommentaryResult:
    """Read this user's latest gold snapshot, generate its commentary card,
    and persist it — the full AA-25 pipeline for one user.

    Reuses `app.db.queries.dashboard`'s reads (same rows `GET /api/dashboard`
    serves, AA-22) rather than re-querying gold tables directly, so the
    commentary card can never describe a number the dashboard itself
    disagrees with. Raises `AuditCommentaryError`/`NoCompliantObservationsError`
    on any malformed or fully-filtered response — a bad generation must fail
    loudly rather than silently write an empty or partial card, same
    provenance-first posture `worker.gold.rebuild_gold` uses for a missing
    FX rate.
    """
    snapshot = await dashboard_queries.get_latest_snapshot(conn, user_id=user_id)
    if snapshot is None:
        raise ValueError(f"no gold snapshot yet for user {user_id!r}")

    snapshot_date: date = snapshot["snapshot_date"]
    term_bucket_rows = await dashboard_queries.list_term_buckets(
        conn, user_id=user_id, snapshot_date=snapshot_date
    )
    diversification_rows = await dashboard_queries.list_diversification_cuts(
        conn, user_id=user_id, snapshot_date=snapshot_date, cut="institution"
    )

    facts = build_gold_facts_snapshot(
        as_of=snapshot_date,
        total_assets_cad=snapshot["total_assets_cad"],
        total_liabilities_cad=snapshot["total_liabilities_cad"],
        net_worth_cad=snapshot["net_worth_cad"],
        term_buckets={row["bucket"]: row["amount_cad"] for row in term_bucket_rows},
        diversification_by_institution=_diversification_slices(diversification_rows),
    )
    facts_text = render_facts_for_prompt(facts)

    await lineage.start("audit_commentary")
    try:
        llm_result = request_commentary(facts_text, client=client)
        compliant = filter_advice_shaped(llm_result.observations)
        if not compliant:
            raise NoCompliantObservationsError(
                "every observation the model returned was advice-shaped or empty"
            )
    except Exception as exc:
        await lineage.fail("audit_commentary", error=str(exc))
        raise

    await commentary_queries.write_commentary(
        conn,
        user_id=user_id,
        snapshot_date=snapshot_date,
        observations=compliant,
        disclosure=DISCLOSURE_TEXT,
        model_backend=llm_result.backend,
        run_id=lineage.run_id,
    )
    await lineage.complete(
        "audit_commentary",
        facets={
            "model_backend": llm_result.backend,
            "observation_count": len(compliant),
            "filtered_count": len(llm_result.observations) - len(compliant),
        },
    )

    return CommentaryResult(
        observations=compliant,
        disclosure=DISCLOSURE_TEXT,
        model_backend=llm_result.backend,
        run_id=lineage.run_id,
    )


_USERS_WITH_SNAPSHOT_SQL = """
    select distinct user_id from public.networth_snapshots where deactivated_at is null
"""


@dataclass(frozen=True)
class CommentaryRefreshResult:
    users_updated: int
    users_failed: int
    refreshed_at: datetime


async def refresh_commentary_for_all_users(
    conn: asyncpg.Connection,
    *,
    client: openai.OpenAI | None = None,
    now: datetime | None = None,
) -> CommentaryRefreshResult:
    """Regenerate every user's commentary card, one user at a time.

    Mirrors `worker.retention.run_retention_sweep`'s per-item isolation: one
    user's malformed LLM response or filtered-empty result must not sink the
    rest of the run, so failures are logged and counted rather than raised —
    same convention `worker.prices.refresh_prices` uses per-symbol. Runs on
    the worker's service_role connection (bypasses RLS by design, same as
    `worker/retention.py`), since one sweep covers every user's snapshot.
    """
    refreshed_at = now if now is not None else datetime.now(UTC)
    rows = await conn.fetch(_USERS_WITH_SNAPSHOT_SQL)

    users_updated = 0
    users_failed = 0
    for row in rows:
        user_id = str(row["user_id"])
        lineage = LineageEmitter(conn, user_id=user_id)
        try:
            await generate_audit_commentary(conn, user_id=user_id, lineage=lineage, client=client)
            users_updated += 1
        except Exception:
            logger.exception("audit commentary refresh failed for user %s", user_id)
            users_failed += 1

    return CommentaryRefreshResult(
        users_updated=users_updated, users_failed=users_failed, refreshed_at=refreshed_at
    )


async def main() -> None:
    """Standalone entrypoint (`python -m worker.commentary`) for a one-off
    manual run — the primary schedule is `worker.main`'s `commentary_loop`,
    same split `worker/prices.py`'s `main()` documents for the price refresh."""
    logging.basicConfig(level=logging.INFO)
    database_url = os.environ["WORKER_DATABASE_URL"]
    conn = await asyncpg.connect(database_url)
    try:
        result = await refresh_commentary_for_all_users(conn)
        logger.info(
            "audit commentary refresh complete: users_updated=%s users_failed=%s",
            result.users_updated,
            result.users_failed,
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
