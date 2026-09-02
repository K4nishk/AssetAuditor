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
