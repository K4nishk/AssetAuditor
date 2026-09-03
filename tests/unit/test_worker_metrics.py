"""Unit tests for `worker.metrics` (KCH-57 / AA-34).

Checks the gauges' values directly via prometheus_client's internal sample
API rather than spinning up the HTTP server, which start_metrics_server
would bind a real port for.
"""

from __future__ import annotations

import time

from worker import metrics


def _gauge_value(gauge) -> float:
    return gauge.collect()[0].samples[0].value


def test_record_heartbeat_sets_the_gauge_to_the_given_timestamp():
    metrics.record_heartbeat(when=1_700_000_000.0)

    assert _gauge_value(metrics.WORKER_HEARTBEAT_TIMESTAMP) == 1_700_000_000.0


def test_record_heartbeat_defaults_to_the_current_time():
    before = time.time()

    metrics.record_heartbeat()

    after = time.time()
    value = _gauge_value(metrics.WORKER_HEARTBEAT_TIMESTAMP)
    assert before <= value <= after


def test_record_queue_depth_sets_the_gauge():
    metrics.record_queue_depth(3)

    assert _gauge_value(metrics.ETL_JOBS_QUEUED) == 3


def test_record_sweeper_success_sets_the_gauge_to_the_given_timestamp():
    metrics.record_sweeper_success(when=1_700_000_000.0)

    assert _gauge_value(metrics.RETENTION_SWEEPER_LAST_SUCCESS_TIMESTAMP) == 1_700_000_000.0


def test_record_sweeper_success_defaults_to_the_current_time():
    before = time.time()

    metrics.record_sweeper_success()

    after = time.time()
    value = _gauge_value(metrics.RETENTION_SWEEPER_LAST_SUCCESS_TIMESTAMP)
    assert before <= value <= after


def _counter_value(counter, **labels) -> float:
    return counter.labels(**labels)._value.get()


def test_record_etl_job_outcome_increments_the_labeled_counter():
    before = _counter_value(metrics.ETL_JOBS_TOTAL, status="done", institution="scotia")

    metrics.record_etl_job_outcome(status="done", institution="scotia")

    after = _counter_value(metrics.ETL_JOBS_TOTAL, status="done", institution="scotia")
    assert after == before + 1


def test_record_etl_job_outcome_defaults_a_missing_institution_to_unknown():
    before = _counter_value(metrics.ETL_JOBS_TOTAL, status="failed", institution="unknown")

    metrics.record_etl_job_outcome(status="failed", institution=None)

    after = _counter_value(metrics.ETL_JOBS_TOTAL, status="failed", institution="unknown")
    assert after == before + 1


def test_record_etl_job_duration_observes_the_histogram():
    before = metrics.ETL_JOB_DURATION_SECONDS._sum.get()

    metrics.record_etl_job_duration(42.0)

    after = metrics.ETL_JOB_DURATION_SECONDS._sum.get()
    assert after == before + 42.0


def test_record_llm_request_increments_the_labeled_counter():
    before = _counter_value(metrics.LLM_REQUESTS_TOTAL, outcome="success", backend="groq")

    metrics.record_llm_request(outcome="success", backend="groq")

    after = _counter_value(metrics.LLM_REQUESTS_TOTAL, outcome="success", backend="groq")
    assert after == before + 1


def test_record_llm_tokens_increments_the_labeled_counter_by_count():
    before = _counter_value(metrics.LLM_TOKENS_TOTAL, backend="groq")

    metrics.record_llm_tokens(backend="groq", count=123)

    after = _counter_value(metrics.LLM_TOKENS_TOTAL, backend="groq")
    assert after == before + 123


class _FakeUsage:
    def __init__(self, total_tokens):
        self.total_tokens = total_tokens


class _FakeResponse:
    def __init__(self, usage=None):
        self.usage = usage


def test_record_llm_tokens_from_response_coerces_a_numeric_string():
    before = _counter_value(metrics.LLM_TOKENS_TOTAL, backend="groq")

    metrics.record_llm_tokens_from_response(_FakeResponse(_FakeUsage("77")), backend="groq")

    after = _counter_value(metrics.LLM_TOKENS_TOTAL, backend="groq")
    assert after == before + 77


def test_record_llm_tokens_from_response_skips_a_non_numeric_total_tokens():
    before = _counter_value(metrics.LLM_TOKENS_TOTAL, backend="groq")

    metrics.record_llm_tokens_from_response(
        _FakeResponse(_FakeUsage("not-a-number")), backend="groq"
    )

    after = _counter_value(metrics.LLM_TOKENS_TOTAL, backend="groq")
    assert after == before


def test_record_llm_tokens_from_response_skips_when_usage_is_missing():
    before = _counter_value(metrics.LLM_TOKENS_TOTAL, backend="groq")

    metrics.record_llm_tokens_from_response(_FakeResponse(usage=None), backend="groq")

    after = _counter_value(metrics.LLM_TOKENS_TOTAL, backend="groq")
    assert after == before
