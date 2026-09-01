"""Unit tests for `worker.lineage` (KCH-48 / AA-13).

Same fake-connection approach as tests/unit/test_worker_queue.py — proves the
query shape (parameterized, correct bind order) and the OpenLineage-shaped
envelope without a live Postgres. The `lineage_events` check constraint /
FK-to-etl_jobs guarantees can only be proven against real Postgres; that's
tests/db/test_lineage_events_live.py, which skips here per CLAUDE.md (no
local Postgres in this sandbox).
"""

from __future__ import annotations

import json

import pytest

from worker.lineage import LineageEmitter, LineageStateError, emit_lineage_event, new_run_id

pytestmark = pytest.mark.asyncio


class FakeConnection:
    def __init__(self, fetchrow_result=None):
        self.calls: list[tuple[str, tuple]] = []
        self._fetchrow_result = fetchrow_result

    async def fetchrow(self, query: str, *args):
        self.calls.append((query, args))
        return self._fetchrow_result


async def test_emit_lineage_event_binds_all_columns_as_parameters():
    conn = FakeConnection(fetchrow_result={"id": "event-1"})

    result = await emit_lineage_event(
        conn,
        user_id="user-1",
        run_id="run-1",
        job_id="job-1",
        step="extract",
        event_type="START",
        facets={"extraction_method": "pdfplumber"},
    )

    assert result == {"id": "event-1"}
    query, args = conn.calls[0]
    assert "$1" in query and "$6" in query
    assert "user-1" not in query  # CLAUDE.md hard rule #3: never interpolated
    user_id, run_id, job_id, event_type, facets_json, payload_json = args
    assert (user_id, run_id, job_id, event_type) == ("user-1", "run-1", "job-1", "START")
    assert json.loads(facets_json) == {"extraction_method": "pdfplumber"}
    payload = json.loads(payload_json)
    assert payload["eventType"] == "START"
    assert payload["job"] == {"namespace": "assetauditor", "name": "extract"}
    assert payload["run"] == {"runId": "run-1", "facets": {"extraction_method": "pdfplumber"}}


async def test_emit_lineage_event_defaults_facets_to_empty_dict():
    conn = FakeConnection(fetchrow_result={"id": "event-1"})

    await emit_lineage_event(
        conn, user_id="user-1", run_id="run-1", step="mask", event_type="COMPLETE"
    )

    _, args = conn.calls[0]
    assert args[2] is None  # job_id defaults to None
    assert json.loads(args[4]) == {}


async def test_emit_lineage_event_rejects_an_unknown_event_type():
    conn = FakeConnection()

    with pytest.raises(ValueError, match="invalid lineage event_type"):
        await emit_lineage_event(
            conn, user_id="user-1", run_id="run-1", step="extract", event_type="RUNNING"
        )

    assert conn.calls == []


async def test_new_run_id_returns_a_fresh_uuid_string_each_call():
    first = new_run_id()
    second = new_run_id()

    assert first != second
    assert len(first) == 36  # canonical uuid4 hyphenated form


async def test_lineage_emitter_reuses_run_id_across_steps():
    conn = FakeConnection(fetchrow_result={"id": "event-1"})
    emitter = LineageEmitter(conn, user_id="user-1", job_id="job-1")

    await emitter.start("extract", facets={"extraction_method": "pdfplumber"})
    await emitter.complete("extract", facets={"row_count": 12})

    first_args = conn.calls[0][1]
    second_args = conn.calls[1][1]
    assert first_args[1] == second_args[1] == emitter.run_id
    assert first_args[0] == second_args[0] == "user-1"
    assert first_args[2] == second_args[2] == "job-1"
    assert first_args[3] == "START"
    assert second_args[3] == "COMPLETE"


async def test_lineage_emitter_accepts_an_explicit_run_id():
    conn = FakeConnection(fetchrow_result={"id": "event-1"})
    emitter = LineageEmitter(conn, user_id="user-1", run_id="fixed-run")

    assert emitter.run_id == "fixed-run"
    await emitter.start("extract")
    assert conn.calls[0][1][1] == "fixed-run"


async def test_lineage_emitter_fail_folds_error_into_facets():
    conn = FakeConnection(fetchrow_result={"id": "event-1"})
    emitter = LineageEmitter(conn, user_id="user-1", job_id="job-1")

    await emitter.start("extract")
    await emitter.fail("extract", error="magic-byte mismatch", facets={"extraction_method": "llm"})

    _, args = conn.calls[1]
    assert args[3] == "FAIL"
    facets = json.loads(args[4])
    assert facets == {"extraction_method": "llm", "error": "magic-byte mismatch"}


async def test_lineage_emitter_rejects_complete_before_start():
    conn = FakeConnection(fetchrow_result={"id": "event-1"})
    emitter = LineageEmitter(conn, user_id="user-1", job_id="job-1")

    with pytest.raises(LineageStateError, match="COMPLETE.*before START"):
        await emitter.complete("extract")

    assert conn.calls == []


async def test_lineage_emitter_rejects_fail_before_start():
    conn = FakeConnection(fetchrow_result={"id": "event-1"})
    emitter = LineageEmitter(conn, user_id="user-1", job_id="job-1")

    with pytest.raises(LineageStateError, match="FAIL.*before START"):
        await emitter.fail("extract", error="boom")

    assert conn.calls == []


async def test_lineage_emitter_rejects_duplicate_start():
    conn = FakeConnection(fetchrow_result={"id": "event-1"})
    emitter = LineageEmitter(conn, user_id="user-1", job_id="job-1")

    await emitter.start("extract")
    with pytest.raises(LineageStateError, match="duplicate START"):
        await emitter.start("extract")

    assert len(conn.calls) == 1


async def test_lineage_emitter_rejects_complete_after_complete():
    conn = FakeConnection(fetchrow_result={"id": "event-1"})
    emitter = LineageEmitter(conn, user_id="user-1", job_id="job-1")

    await emitter.start("extract")
    await emitter.complete("extract")
    with pytest.raises(LineageStateError, match="after terminal event"):
        await emitter.complete("extract")

    assert len(conn.calls) == 2


async def test_lineage_emitter_rejects_start_after_fail():
    conn = FakeConnection(fetchrow_result={"id": "event-1"})
    emitter = LineageEmitter(conn, user_id="user-1", job_id="job-1")

    await emitter.start("extract")
    await emitter.fail("extract", error="boom")
    with pytest.raises(LineageStateError, match="duplicate START"):
        await emitter.start("extract")

    assert len(conn.calls) == 2


async def test_lineage_emitter_tracks_steps_independently():
    conn = FakeConnection(fetchrow_result={"id": "event-1"})
    emitter = LineageEmitter(conn, user_id="user-1", job_id="job-1")

    await emitter.start("extract")
    await emitter.start("mask")
    await emitter.complete("mask")
    await emitter.complete("extract")

    assert len(conn.calls) == 4


async def test_lineage_emitter_does_not_advance_state_when_insert_fails():
    class FailingConnection:
        async def fetchrow(self, query, *args):
            raise RuntimeError("db unavailable")

    emitter = LineageEmitter(FailingConnection(), user_id="user-1", job_id="job-1")

    with pytest.raises(RuntimeError):
        await emitter.start("extract")

    # state was never advanced, so a retried START is still treated as fresh
    conn = FakeConnection(fetchrow_result={"id": "event-1"})
    emitter._conn = conn
    await emitter.start("extract")
    assert len(conn.calls) == 1
