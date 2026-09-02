"""Prometheus `/metrics` scrape target for the ETL worker (AA-34).

docs/vault/10-mental-models/Push-Dont-Scrape-Metrics.md: unlike the
serverless API (which must push), the worker is a long-lived process, so it
exposes a normal `/metrics` scrape target directly. Grafana Alloy (AA-27)
scrapes this once it's wired up on the box; this module only needs to make
the endpoint exist and report the right numbers.

`worker_heartbeat_timestamp` and `etl_jobs_queued` are the paired gauges the
stale-while-queued alert reads (docs/vault/30-architecture/Observability.md,
worker/observability/stale_while_queued_alert.yaml).

`retention_sweeper_last_success_timestamp` (AA-19) is the same pattern for
`worker/retention.py`'s nightly sweep — read by
worker/observability/retention_sweeper_stale_alert.yaml.
"""

from __future__ import annotations

import time

from prometheus_client import Gauge, start_http_server

DEFAULT_METRICS_PORT = 9100

WORKER_HEARTBEAT_TIMESTAMP = Gauge(
    "worker_heartbeat_timestamp",
    "Unix timestamp of the worker's last successful worker_heartbeat upsert.",
)
ETL_JOBS_QUEUED = Gauge(
    "etl_jobs_queued",
    "Number of public.etl_jobs rows currently in status='pending'.",
)
RETENTION_SWEEPER_LAST_SUCCESS_TIMESTAMP = Gauge(
    "retention_sweeper_last_success_timestamp",
    "Unix timestamp of worker.retention's last fully-successful sweep (AA-19).",
)


def record_heartbeat(*, when: float | None = None) -> None:
    WORKER_HEARTBEAT_TIMESTAMP.set(when if when is not None else time.time())


def record_queue_depth(count: int) -> None:
    ETL_JOBS_QUEUED.set(count)


def record_sweeper_success(*, when: float | None = None) -> None:
    RETENTION_SWEEPER_LAST_SUCCESS_TIMESTAMP.set(when if when is not None else time.time())


def start_metrics_server(port: int = DEFAULT_METRICS_PORT) -> None:
    """Start the Prometheus scrape server in a daemon thread (non-blocking)."""
    start_http_server(port)
