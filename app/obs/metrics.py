"""Batched Prometheus remote_write for the API (AA-27).

docs/vault/10-mental-models/Push-Dont-Scrape-Metrics.md: Vercel functions are
ephemeral, so there is nothing stable for Grafana Alloy to scrape (that's
`worker/metrics.py`'s job for the long-lived worker process). The API instead
*pushes* — one `metrics_middleware` wraps every request, accumulates the
request's samples into a `MetricsBatch`, and flushes them as a single
`remote_write` call before the response returns. Batching per-request (not
per-metric) is what keeps this from being "chatty" per the mental model's own
gotcha; awaiting the push before returning (rather than firing a background
task) is deliberate too — a serverless function may freeze immediately after
its response is sent, so anything not awaited first may simply never happen.

There is no official Python remote_write client, and the exact wire format
(`prometheus/prompb.WriteRequest`: repeated `TimeSeries{labels, samples}`) is
small and stable enough to hand-encode rather than vendor generated protobuf
bindings for one message shape. Snappy block-compression (required by the
remote_write spec) comes from `pyarrow.Codec`, already a repo dependency
(AA-18's parquet writes) — no new dependency needed.

**Unverified**: no `GRAFANA_CLOUD_*` credentials or network exist in this
sandbox. `push_samples` no-ops (logs at debug) whenever `RemoteWriteConfig`
can't be built from the environment, which is exactly what happens here and
in CI — the wire encoding itself is covered by `tests/unit/test_api_metrics.py`
using an injected transport, but the real HTTP round trip against Grafana
Cloud has never run.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import struct
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import pyarrow as pa
from fastapi import Request, Response
from starlette.middleware.base import RequestResponseEndpoint

logger = logging.getLogger("app.obs.metrics")

_SNAPPY_CODEC = pa.Codec("snappy")
_UNMATCHED_ROUTE_LABEL = "unmatched"


@dataclass(frozen=True)
class MetricSample:
    name: str
    value: float
    labels: dict[str, str] = field(default_factory=dict)
    timestamp_ms: int | None = None


class MetricsBatch:
    """Accumulates samples for one push. One `push_samples` call per batch,
    not one per `counter()` — that's the "batched" half of AA-27's mandate."""

    def __init__(self) -> None:
        self._samples: list[MetricSample] = []

    def counter(self, name: str, value: float = 1.0, **labels: str) -> None:
        self._samples.append(MetricSample(name=name, value=value, labels=labels))

    @property
    def samples(self) -> list[MetricSample]:
        return list(self._samples)


# --- wire encoding: prometheus/prompb.WriteRequest, hand-rolled -------------
#
#   message WriteRequest { repeated TimeSeries timeseries = 1; }
#   message TimeSeries { repeated Label labels = 1; repeated Sample samples = 2; }
#   message Label { string name = 1; string value = 2; }
#   message Sample { double value = 1; int64 timestamp = 2; }
#
# Every value here is non-negative (counts, millisecond timestamps), so plain
# base-128 varints are enough — no zigzag encoding needed.


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _tag(field_number: int, wire_type: int) -> bytes:
    return _varint((field_number << 3) | wire_type)


def _length_delimited_field(field_number: int, payload: bytes) -> bytes:
    return _tag(field_number, 2) + _varint(len(payload)) + payload


def _string_field(field_number: int, value: str) -> bytes:
    return _length_delimited_field(field_number, value.encode("utf-8"))


def _double_field(field_number: int, value: float) -> bytes:
    return _tag(field_number, 1) + struct.pack("<d", value)


def _varint_field(field_number: int, value: int) -> bytes:
    return _tag(field_number, 0) + _varint(value)


def _encode_label(name: str, value: str) -> bytes:
    return _string_field(1, name) + _string_field(2, value)


def _encode_sample(value: float, timestamp_ms: int) -> bytes:
    return _double_field(1, value) + _varint_field(2, timestamp_ms)


def _encode_timeseries(sample: MetricSample, *, default_timestamp_ms: int) -> bytes:
    # remote_write requires a `__name__` label and lexicographically sorted
    # label names; the caller's own labels must be enums only (CLAUDE.md
    # cardinality rule) — nothing here validates that, same trust boundary
    # as `worker/metrics.py`'s label arguments.
    all_labels = {"__name__": sample.name, **sample.labels}
    labels_bytes = b"".join(
        _length_delimited_field(1, _encode_label(name, value))
        for name, value in sorted(all_labels.items())
    )
    timestamp_ms = sample.timestamp_ms if sample.timestamp_ms is not None else default_timestamp_ms
    sample_bytes = _length_delimited_field(2, _encode_sample(sample.value, timestamp_ms))
    return labels_bytes + sample_bytes


def encode_write_request(
    samples: Iterable[MetricSample], *, timestamp_ms: int | None = None
) -> bytes:
    """Serialize `samples` into a `prompb.WriteRequest` protobuf payload
    (uncompressed — `push_samples` snappy-compresses it separately)."""
    default_timestamp_ms = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
    return b"".join(
        _length_delimited_field(
            1, _encode_timeseries(sample, default_timestamp_ms=default_timestamp_ms)
        )
        for sample in samples
    )


# --- transport ---------------------------------------------------------------


class RemoteWriteError(RuntimeError):
    """Raised when the HTTP POST to the remote_write endpoint fails."""


Transport = Callable[[str, bytes, dict[str, str]], None]


def _urllib_transport(url: str, body: bytes, headers: dict[str, str]) -> None:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            if response.status >= 300:
                raise RemoteWriteError(f"remote_write endpoint returned HTTP {response.status}")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RemoteWriteError(f"remote_write request failed: {exc}") from exc


@dataclass(frozen=True)
class RemoteWriteConfig:
    url: str
    username: str
    api_key: str

    @classmethod
    def from_env(cls) -> RemoteWriteConfig | None:
        url = os.environ.get("GRAFANA_CLOUD_PROMETHEUS_URL")
        username = os.environ.get("GRAFANA_CLOUD_PROMETHEUS_USER")
        api_key = os.environ.get("GRAFANA_CLOUD_API_KEY")
        if not url or not username or not api_key:
            return None
        return cls(url=url, username=username, api_key=api_key)


def push_samples(
    samples: Iterable[MetricSample],
    *,
    config: RemoteWriteConfig | None,
    transport: Transport = _urllib_transport,
) -> None:
    """Best-effort remote_write push — never raises. A metrics outage (no
    config, or the endpoint being unreachable) must never turn into an API
    outage, so every failure is logged and swallowed here rather than left
    for the caller to handle."""
    sample_list = list(samples)
    if not sample_list:
        return
    if config is None:
        logger.debug("remote_write skipped: GRAFANA_CLOUD_* env vars not set")
        return

    payload = encode_write_request(sample_list)
    compressed = _SNAPPY_CODEC.compress(payload, asbytes=True)
    credentials = base64.b64encode(f"{config.username}:{config.api_key}".encode()).decode("ascii")
    headers = {
        "Content-Type": "application/x-protobuf",
        "Content-Encoding": "snappy",
        "X-Prometheus-Remote-Write-Version": "0.1.0",
        "Authorization": f"Basic {credentials}",
    }
    try:
        transport(config.url, compressed, headers)
    except Exception:
        logger.warning("remote_write push failed", exc_info=True)


# --- FastAPI middleware --------------------------------------------------------


async def metrics_middleware(request: Request, call_next: RequestResponseEndpoint) -> Response:
    """`api_requests_total`/`api_request_duration_seconds_{sum,count}` per
    route — the "API: request count/latency per route (batched push)" line
    in docs/vault/30-architecture/Observability.md's metric set. Route label
    is the matched path *template* (e.g. `/api/staged/{job_id}/rows`), set by
    Starlette on `request.scope["route"]` once routing completes, never the
    raw path — an unmatched request (404, or a client probing garbage paths)
    is labeled `"unmatched"` instead, so cardinality stays bounded to the
    fixed set of registered routes (CLAUDE.md's enum-only label rule).
    """
    start = time.monotonic()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        duration_seconds = time.monotonic() - start
        route = request.scope.get("route")
        route_label = route.path if route is not None else _UNMATCHED_ROUTE_LABEL
        labels = {"route": route_label, "method": request.method, "status": str(status_code)}

        batch = MetricsBatch()
        batch.counter("api_requests_total", 1.0, **labels)
        batch.counter("api_request_duration_seconds_sum", duration_seconds, **labels)
        batch.counter("api_request_duration_seconds_count", 1.0, **labels)

        await asyncio.to_thread(push_samples, batch.samples, config=RemoteWriteConfig.from_env())
