"""Unit tests for the API's batched remote_write metrics (KCH-64 / AA-27).

No network/Grafana Cloud involved — `encode_write_request`'s hand-rolled
protobuf wire format is verified with an independent decoder written here
(production code only ever needs to encode; a real remote_write receiver is
the decoder in production), and `push_samples`/`metrics_middleware` are
exercised with an injected transport / monkeypatched push, same offline
convention as `tests/unit/test_llm_tier.py`.
"""

from __future__ import annotations

import struct

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.obs import metrics as obs_metrics
from app.obs.metrics import (
    MetricSample,
    MetricsBatch,
    RemoteWriteConfig,
    RemoteWriteError,
    encode_write_request,
    metrics_middleware,
    push_samples,
)

# --- a small independent decoder for the hand-rolled wire format ------------


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        byte = buf[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, i
        shift += 7


def _decode_fields(buf: bytes) -> list[tuple[int, int, object]]:
    fields: list[tuple[int, int, object]] = []
    i = 0
    while i < len(buf):
        tag, i = _read_varint(buf, i)
        field_number, wire_type = tag >> 3, tag & 0x7
        if wire_type == 0:
            value, i = _read_varint(buf, i)
        elif wire_type == 1:
            value = struct.unpack_from("<d", buf, i)[0]
            i += 8
        elif wire_type == 2:
            length, i = _read_varint(buf, i)
            value = buf[i : i + length]
            i += length
        else:
            raise ValueError(f"unsupported wire type {wire_type}")
        fields.append((field_number, wire_type, value))
    return fields


def _decode_write_request(buf: bytes) -> list[dict]:
    """Returns a list of `{"labels": {...}, "value": float, "timestamp": int}`."""
    series = []
    for field_number, _wire_type, timeseries_bytes in _decode_fields(buf):
        assert field_number == 1
        labels: dict[str, str] = {}
        value = None
        timestamp = None
        for sub_field, _wt, sub_value in _decode_fields(timeseries_bytes):
            if sub_field == 1:  # Label
                label_fields = dict(
                    (n, v) for n, _wt2, v in _decode_fields(sub_value)
                )
                labels[label_fields[1].decode("utf-8")] = label_fields[2].decode("utf-8")
            elif sub_field == 2:  # Sample
                sample_fields = {n: v for n, _wt2, v in _decode_fields(sub_value)}
                value = sample_fields[1]
                timestamp = sample_fields[2]
        series.append({"labels": labels, "value": value, "timestamp": timestamp})
    return series


# --- encode_write_request ----------------------------------------------------


def test_encode_write_request_round_trips_a_single_sample():
    samples = [MetricSample(name="api_requests_total", value=3.0, labels={"route": "/api/health"})]

    decoded = _decode_write_request(encode_write_request(samples, timestamp_ms=1_700_000_000_000))

    assert len(decoded) == 1
    assert decoded[0]["labels"] == {"__name__": "api_requests_total", "route": "/api/health"}
    assert decoded[0]["value"] == 3.0
    assert decoded[0]["timestamp"] == 1_700_000_000_000


def test_encode_write_request_round_trips_multiple_samples_and_labels():
    samples = [
        MetricSample(
            name="api_requests_total",
            value=1.0,
            labels={"route": "/api/dashboard", "method": "GET", "status": "200"},
        ),
        MetricSample(
            name="api_request_duration_seconds_sum",
            value=0.042,
            labels={"route": "/api/dashboard"},
        ),
    ]

    decoded = _decode_write_request(encode_write_request(samples, timestamp_ms=42))

    assert len(decoded) == 2
    assert decoded[0]["labels"] == {
        "__name__": "api_requests_total",
        "route": "/api/dashboard",
        "method": "GET",
        "status": "200",
    }
    assert decoded[1]["value"] == pytest.approx(0.042)


def test_encode_write_request_defaults_timestamp_to_now_ms():
    decoded = _decode_write_request(encode_write_request([MetricSample(name="x", value=1.0)]))
    # Sanity bound, not an exact-time assertion: any millisecond epoch timestamp
    # from this decade is well above 1_700_000_000_000 (2023-11) and below a
    # generous upper bound.
    assert 1_700_000_000_000 < decoded[0]["timestamp"] < 4_000_000_000_000


# --- MetricsBatch -------------------------------------------------------------


def test_metrics_batch_accumulates_samples_with_labels():
    batch = MetricsBatch()

    batch.counter("api_requests_total", route="/api/health", method="GET")
    batch.counter("api_requests_total", 2.0, route="/api/rooms", method="GET")

    assert batch.samples == [
        MetricSample(
            name="api_requests_total",
            value=1.0,
            labels={"route": "/api/health", "method": "GET"},
        ),
        MetricSample(
            name="api_requests_total",
            value=2.0,
            labels={"route": "/api/rooms", "method": "GET"},
        ),
    ]


# --- push_samples --------------------------------------------------------------


def test_push_samples_is_a_noop_without_config():
    calls = []
    push_samples(
        [MetricSample(name="x", value=1.0)],
        config=None,
        transport=lambda *args: calls.append(args),
    )
    assert calls == []


def test_push_samples_is_a_noop_for_an_empty_batch():
    calls = []
    config = RemoteWriteConfig(url="https://example.invalid/write", username="u", api_key="k")
    push_samples([], config=config, transport=lambda *args: calls.append(args))
    assert calls == []


def test_push_samples_sends_snappy_compressed_protobuf_with_basic_auth(
    monkeypatch: pytest.MonkeyPatch,
):
    import pyarrow as pa

    monkeypatch.setattr(obs_metrics.time, "time", lambda: 1_700_000_000.0)
    calls = []
    config = RemoteWriteConfig(url="https://example.invalid/write", username="u", api_key="k")
    samples = [MetricSample(name="api_requests_total", value=1.0, labels={"route": "/api/health"})]

    push_samples(samples, config=config, transport=lambda *args: calls.append(args))

    assert len(calls) == 1
    url, body, headers = calls[0]
    assert url == config.url
    assert headers["Content-Type"] == "application/x-protobuf"
    assert headers["Content-Encoding"] == "snappy"
    assert headers["Authorization"].startswith("Basic ")

    expected_raw = encode_write_request(samples, timestamp_ms=1_700_000_000_000)
    decompressed = pa.decompress(body, decompressed_size=len(expected_raw), codec="snappy")
    assert decompressed == expected_raw


def test_push_samples_swallows_transport_errors():
    def _failing_transport(url, body, headers):
        raise RemoteWriteError("boom")

    config = RemoteWriteConfig(url="https://example.invalid/write", username="u", api_key="k")

    push_samples([MetricSample(name="x", value=1.0)], config=config, transport=_failing_transport)


# --- RemoteWriteConfig.from_env ------------------------------------------------


def test_from_env_returns_none_when_any_var_is_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GRAFANA_CLOUD_PROMETHEUS_URL", raising=False)
    monkeypatch.delenv("GRAFANA_CLOUD_PROMETHEUS_USER", raising=False)
    monkeypatch.delenv("GRAFANA_CLOUD_API_KEY", raising=False)

    assert RemoteWriteConfig.from_env() is None


def test_from_env_builds_a_config_when_all_vars_are_set(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GRAFANA_CLOUD_PROMETHEUS_URL", "https://example.invalid/write")
    monkeypatch.setenv("GRAFANA_CLOUD_PROMETHEUS_USER", "12345")
    monkeypatch.setenv("GRAFANA_CLOUD_API_KEY", "sk-test")

    config = RemoteWriteConfig.from_env()

    assert config == RemoteWriteConfig(
        url="https://example.invalid/write", username="12345", api_key="sk-test"
    )


# --- metrics_middleware --------------------------------------------------------


def _build_app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(metrics_middleware)

    @app.get("/api/widgets/{widget_id}")
    def get_widget(widget_id: str) -> dict[str, str]:
        return {"id": widget_id}

    @app.get("/api/boom")
    def boom() -> dict[str, str]:
        raise RuntimeError("kaboom")

    return app


def test_middleware_pushes_a_single_batch_labeled_with_the_route_template(
    monkeypatch: pytest.MonkeyPatch,
):
    pushed = []
    monkeypatch.setattr(
        obs_metrics, "push_samples", lambda samples, **kwargs: pushed.append(list(samples))
    )
    client = TestClient(_build_app())

    response = client.get("/api/widgets/abc-123")

    assert response.status_code == 200
    assert len(pushed) == 1
    labels_by_name = {sample.name: sample.labels for sample in pushed[0]}
    assert labels_by_name["api_requests_total"] == {
        "route": "/api/widgets/{widget_id}",
        "method": "GET",
        "status": "200",
    }


def test_middleware_labels_an_unmatched_path_as_unmatched(monkeypatch: pytest.MonkeyPatch):
    pushed = []
    monkeypatch.setattr(
        obs_metrics, "push_samples", lambda samples, **kwargs: pushed.append(list(samples))
    )
    client = TestClient(_build_app())

    response = client.get("/api/does-not-exist")

    assert response.status_code == 404
    labels_by_name = {sample.name: sample.labels for sample in pushed[0]}
    assert labels_by_name["api_requests_total"]["route"] == "unmatched"
    assert labels_by_name["api_requests_total"]["status"] == "404"


def test_middleware_records_status_500_and_still_propagates_the_exception(
    monkeypatch: pytest.MonkeyPatch,
):
    pushed = []
    monkeypatch.setattr(
        obs_metrics, "push_samples", lambda samples, **kwargs: pushed.append(list(samples))
    )
    client = TestClient(_build_app(), raise_server_exceptions=False)

    response = client.get("/api/boom")

    assert response.status_code == 500
    labels_by_name = {sample.name: sample.labels for sample in pushed[0]}
    assert labels_by_name["api_requests_total"]["status"] == "500"
