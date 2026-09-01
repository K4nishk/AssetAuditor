"""OpenLineage-shaped event emitter -> lineage_events (KCH-48 / AA-13).

Runs on the worker's `WORKER_DATABASE_URL` connection (service_role, bypasses
RLS by design — same convention as `worker/queue.py`), since one worker
process emits lineage across every user's jobs, not just one; `user_id` is
always bound explicitly rather than relied on from a session role.

Every ETL step (extract AA-14/15/16, mask AA-12, stage, confirm AA-17,
silver write / gold rebuild AA-18, retention purge AA-19) emits `START` at
the beginning and exactly one of `COMPLETE`/`FAIL` at the end, all sharing
one `run_id` per etl_jobs attempt. That shared `run_id` is what AA-23's
drill-down panel follows backward from a gold row to its bronze file (or
purge tombstone) — ADR v1.0.0 §7.

`payload` holds a minimal OpenLineage RunEvent envelope (eventType/producer/
job/run — migration 0001's "OpenLineage-shaped events" comment); `facets`
duplicates `run.facets` at the top level so a caller can query e.g.
`facets->>'extraction_method'` without reaching into the nested envelope.
`eventTime` is deliberately not duplicated into the envelope — the row's own
`occurred_at` (DB-assigned, migration 0001) is that timestamp of record.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any, Literal

import asyncpg

EventType = Literal["START", "COMPLETE", "FAIL"]
_EVENT_TYPES: frozenset[str] = frozenset({"START", "COMPLETE", "FAIL"})

_PRODUCER = "https://github.com/K4nishk/AssetAuditor/tree/main/worker"
_NAMESPACE = "assetauditor"

_INSERT_EVENT_SQL = """
    insert into public.lineage_events (user_id, run_id, job_id, event_type, facets, payload)
    values ($1, $2, $3, $4, $5::jsonb, $6::jsonb)
    returning id, user_id, run_id, job_id, event_type, facets, payload, occurred_at
"""


def new_run_id() -> str:
    """Mint a fresh `run_id` for one etl_jobs processing attempt."""
    return str(uuid.uuid4())


async def emit_lineage_event(
    conn: asyncpg.Connection,
    *,
    user_id: str,
    run_id: str,
    step: str,
    event_type: EventType,
    job_id: str | None = None,
    facets: Mapping[str, Any] | None = None,
) -> asyncpg.Record:
    """Insert one OpenLineage-shaped RunEvent row.

    `step` is the pipeline step name (OpenLineage's Job.name — "extract",
    "mask", "stage", "silver_write", "gold_rebuild", "retention_purge", ...).
    `job_id` is our own `etl_jobs` FK, kept distinct from `step`/`run_id`
    since one `etl_jobs` row spans several steps, and a retry gets a new
    `run_id` on the same `job_id`.
    """
    if event_type not in _EVENT_TYPES:
        raise ValueError(f"invalid lineage event_type: {event_type!r}")

    facets_dict = dict(facets or {})
    envelope = {
        "eventType": event_type,
        "producer": _PRODUCER,
        "job": {"namespace": _NAMESPACE, "name": step},
        "run": {"runId": run_id, "facets": facets_dict},
    }
    return await conn.fetchrow(
        _INSERT_EVENT_SQL,
        user_id,
        run_id,
        job_id,
        event_type,
        json.dumps(facets_dict),
        json.dumps(envelope),
    )


_StepState = Literal["started", "completed", "failed"]


class LineageStateError(ValueError):
    """Raised when a step's START/COMPLETE/FAIL calls are out of order."""


class LineageEmitter:
    """Binds one `(conn, user_id, run_id, job_id)` so a step only names itself.

    One instance per `etl_jobs` processing attempt — construct it once a job
    is claimed (`worker.queue.claim_next_job`) and reuse it across every step
    that job goes through, so all of that attempt's events share `run_id`.

    Tracks each step's own START -> {COMPLETE | FAIL} lifecycle in memory and
    rejects calls that would break it (COMPLETE/FAIL before START, a second
    START, or a second terminal event) — the drill-down panel (AA-23) walks
    `run_id` assuming exactly one START and one terminal event per step, so a
    caller bug here must fail loudly rather than write a row that violates
    that assumption.
    """

    def __init__(
        self,
        conn: asyncpg.Connection,
        *,
        user_id: str,
        job_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        self._conn = conn
        self._user_id = user_id
        self._job_id = job_id
        self.run_id = run_id or new_run_id()
        self._step_states: dict[str, _StepState] = {}

    async def start(
        self, step: str, *, facets: Mapping[str, Any] | None = None
    ) -> asyncpg.Record:
        prior = self._step_states.get(step)
        if prior is not None:
            raise LineageStateError(
                f"duplicate START for step {step!r} (already {prior})"
            )
        row = await self._emit("START", step, facets)
        self._step_states[step] = "started"
        return row

    async def complete(
        self, step: str, *, facets: Mapping[str, Any] | None = None
    ) -> asyncpg.Record:
        self._require_started(step, "COMPLETE")
        row = await self._emit("COMPLETE", step, facets)
        self._step_states[step] = "completed"
        return row

    async def fail(
        self, step: str, *, error: str, facets: Mapping[str, Any] | None = None
    ) -> asyncpg.Record:
        self._require_started(step, "FAIL")
        row = await self._emit("FAIL", step, {**(facets or {}), "error": error})
        self._step_states[step] = "failed"
        return row

    def _require_started(self, step: str, event_type: EventType) -> None:
        state = self._step_states.get(step)
        if state is None:
            raise LineageStateError(f"{event_type} for step {step!r} before START")
        if state != "started":
            raise LineageStateError(
                f"{event_type} for step {step!r} after terminal event ({state})"
            )

    async def _emit(
        self, event_type: EventType, step: str, facets: Mapping[str, Any] | None
    ) -> asyncpg.Record:
        return await emit_lineage_event(
            self._conn,
            user_id=self._user_id,
            run_id=self.run_id,
            job_id=self._job_id,
            step=step,
            event_type=event_type,
            facets=facets,
        )
