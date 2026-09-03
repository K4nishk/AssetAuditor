"""Prometheus `/metrics` scrape target for the ETL worker (AA-34, extended AA-27).

docs/vault/10-mental-models/Push-Dont-Scrape-Metrics.md: unlike the
serverless API (which must push, see `app.obs.metrics`), the worker is a
long-lived process, so it exposes a normal `/metrics` scrape target
directly. Grafana Alloy (`worker/observability/alloy-config.alloy`, AA-27)
scrapes this on the compose network; this module only needs to make the
endpoint exist and report the right numbers.

`worker_heartbeat_timestamp` and `etl_jobs_queued` are the paired gauges the
stale-while-queued alert reads (docs/vault/30-architecture/Observability.md,
worker/observability/stale_while_queued_alert.yaml).

`retention_sweeper_last_success_timestamp` (AA-19) is the same pattern for
`worker/retention.py`'s nightly sweep — read by
worker/observability/retention_sweeper_stale_alert.yaml.

`price_refresh_last_success_timestamp` (AA-21) is the same pattern again,
for `worker/prices.py`'s daily refresh; no alert yaml (a stale price feed
isn't a privacy incident like AA-19's sweeper).

`etl_jobs_total`/`etl_job_duration_seconds` (AA-27) are recorded by
`worker.queue.release_job` — the sole place an `etl_jobs` row changes
status — so they light up automatically once a future issue wires adapter/
pdfplumber/LLM dispatch into `worker/main.py`'s `job_poll_loop`; that
dispatch does not exist yet (see CONTRACT_OUT.md), same "instrument ahead of
the wiring" convention this module already used for the gauges above.
`worker/observability/etl_failure_rate_alert.yaml` reads `etl_jobs_total`.

`llm_requests_total`/`llm_tokens_total` (AA-27) are recorded directly by
`worker.extract.llm_tier.extract_transactions` and
`worker.commentary.request_commentary` — both already run on the worker
process today (even though the extraction tier itself isn't dispatched from
the job loop yet), so these counters are live now, not just wired-ahead.
`worker/observability/llm_error_rate_alert.yaml` reads `llm_requests_total`.
"""

from __future__ import annotations

import time

from prometheus_client import Counter, Gauge, Histogram, start_http_server

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
PRICE_REFRESH_LAST_SUCCESS_TIMESTAMP = Gauge(
    "price_refresh_last_success_timestamp",
    "Unix timestamp of worker.prices's last fully-successful refresh (AA-21).",
)
ETL_JOBS_TOTAL = Counter(
    "etl_jobs_total",
    "public.etl_jobs status transitions recorded by worker.queue.release_job.",
    ["status", "institution"],
)
ETL_JOB_DURATION_SECONDS = Histogram(
    "etl_job_duration_seconds",
    "Seconds between a job being claimed and its terminal release_job call.",
    buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1800, float("inf")),
)
LLM_REQUESTS_TOTAL = Counter(
    "llm_requests_total",
    "LiteLLM chat-completion requests issued by the worker.",
    ["outcome", "backend"],
)
LLM_TOKENS_TOTAL = Counter(
    "llm_tokens_total",
    "Tokens billed by LiteLLM for requests issued by the worker.",
    ["backend"],
)


def record_heartbeat(*, when: float | None = None) -> None:
    WORKER_HEARTBEAT_TIMESTAMP.set(when if when is not None else time.time())


def record_queue_depth(count: int) -> None:
    ETL_JOBS_QUEUED.set(count)


def record_sweeper_success(*, when: float | None = None) -> None:
    RETENTION_SWEEPER_LAST_SUCCESS_TIMESTAMP.set(when if when is not None else time.time())


def record_price_refresh_success(*, when: float | None = None) -> None:
    PRICE_REFRESH_LAST_SUCCESS_TIMESTAMP.set(when if when is not None else time.time())


def record_etl_job_outcome(*, status: str, institution: str | None) -> None:
    ETL_JOBS_TOTAL.labels(status=status, institution=institution or "unknown").inc()


def record_etl_job_duration(seconds: float) -> None:
    ETL_JOB_DURATION_SECONDS.observe(seconds)


def record_llm_request(*, outcome: str, backend: str) -> None:
    LLM_REQUESTS_TOTAL.labels(outcome=outcome, backend=backend).inc()


def record_llm_tokens(*, backend: str, count: int) -> None:
    LLM_TOKENS_TOTAL.labels(backend=backend).inc(count)


def start_metrics_server(port: int = DEFAULT_METRICS_PORT) -> None:
    """Start the Prometheus scrape server in a daemon thread (non-blocking)."""
    start_http_server(port)
