"""Call policy (AA-62, #45; spec #40 decision 1): failure classes and retry budget.

Seam 1a: a fake `send` with a fake clock and sleeper, so timings are exact.
Seam 1b: the real openai client against a stub HTTP server on 127.0.0.1, which
is what proves the SDK's own silent retries are off.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import openai
import pytest

from worker.call_policy import (
    FailureClass,
    Permanent,
    Success,
    Wait,
    classify,
    make_client,
    run,
    run_async,
)

CANARY = "8,765.43 ZZYZX PAYEE"


def _status_error(status: int, *, retry_after: str | None = None) -> openai.APIStatusError:
    headers = {"retry-after": retry_after} if retry_after else {}
    request = httpx.Request("POST", "http://litellm:4000/chat/completions")
    response = httpx.Response(status, headers=headers, request=request)
    return openai.APIStatusError(f"upstream said {CANARY}", response=response, body=None)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []
        self.attempt_starts: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _sender(clock: _Clock, outcomes: list[Any], *, attempt_seconds: float = 10.0):
    remaining = list(outcomes)

    def send() -> Any:
        clock.attempt_starts.append(clock.now)
        clock.now += attempt_seconds
        outcome = remaining.pop(0) if remaining else outcomes[-1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return send


# --- classification -----------------------------------------------------------


@pytest.mark.parametrize("status", [408, 429, 503, 504])
def test_busy_statuses(status: int) -> None:
    assert classify(_status_error(status)) is FailureClass.BUSY


@pytest.mark.parametrize("status", [500, 502])
def test_unreachable_statuses(status: int) -> None:
    assert classify(_status_error(status)) is FailureClass.UNREACHABLE


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_permanent_statuses(status: int) -> None:
    assert classify(_status_error(status)) is FailureClass.PERMANENT


def test_parse_failures_are_permanent() -> None:
    assert classify(ValueError("bad json")) is FailureClass.PERMANENT


# --- budget (seam 1a) ------------------------------------------------------------


def test_busy_backs_off_and_never_starts_after_120s() -> None:
    clock = _Clock()
    result = run(_sender(clock, [_status_error(503)]), clock=clock, sleep=clock.sleep)
    assert isinstance(result, Wait) and result.failure_class is FailureClass.BUSY
    assert clock.sleeps == [2, 4, 8, 16, 30]
    assert max(clock.attempt_starts) <= 120
    assert len(clock.attempt_starts) == 6


def test_unreachable_fails_fast() -> None:
    clock = _Clock()
    result = run(_sender(clock, [_status_error(502)]), clock=clock, sleep=clock.sleep)
    assert isinstance(result, Wait) and result.failure_class is FailureClass.UNREACHABLE
    assert clock.sleeps == [1, 3]


def test_permanent_is_never_retried() -> None:
    clock = _Clock()
    result = run(_sender(clock, [_status_error(400)]), clock=clock, sleep=clock.sleep)
    assert isinstance(result, Permanent)
    assert clock.sleeps == [] and len(clock.attempt_starts) == 1


def test_success_after_busy_returns_the_value() -> None:
    clock = _Clock()
    send = _sender(clock, [_status_error(503), _status_error(503), "ok"])
    result = run(send, clock=clock, sleep=clock.sleep)
    assert isinstance(result, Success) and result.value == "ok"
    assert clock.sleeps == [2, 4]
    assert result.attempts == ("busy", "busy", "success")


def test_retry_after_beyond_the_budget_stops_early() -> None:
    clock = _Clock()
    send = _sender(clock, [_status_error(429, retry_after="600")])
    result = run(send, clock=clock, sleep=clock.sleep)
    assert isinstance(result, Wait)
    assert clock.sleeps == [] and len(clock.attempt_starts) == 1


def test_outcomes_never_carry_the_error_text(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    for status in (503, 400):
        clock = _Clock()
        result = run(_sender(clock, [_status_error(status)]), clock=clock, sleep=clock.sleep)
        assert CANARY not in repr(result)
    assert CANARY not in caplog.text


def test_async_wrapper_keeps_the_event_loop_free() -> None:
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        for _ in range(20):
            ticks += 1
            await asyncio.sleep(0.005)

    ticks_while_sending: list[int] = []

    def slow_send() -> str:
        time.sleep(0.15)
        ticks_while_sending.append(ticks)
        return "ok"

    async def main() -> Any:
        result, _ = await asyncio.gather(run_async(slow_send), ticker())
        return result

    result = asyncio.run(main())
    assert isinstance(result, Success)
    assert ticks_while_sending[0] > 0, "the event loop was blocked while the call ran"


# --- real client against a stub server (seam 1b) --------------------------------


class _Stub(ThreadingHTTPServer):
    statuses: list[int]
    bodies: list[bytes]
    stall_seconds: float = 0.0


class _Handler(BaseHTTPRequestHandler):
    server: _Stub

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.bodies.append(body)
        status = self.server.statuses.pop(0) if self.server.statuses else 200
        if self.server.stall_seconds:
            time.sleep(self.server.stall_seconds)
        payload = {
            "id": "x",
            "object": "chat.completion",
            "created": 0,
            "model": "extractor",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "{}"},
                }
            ],
        }
        data = json.dumps(payload if status == 200 else {"error": {"message": "x"}}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def stub() -> Iterator[_Stub]:
    server = _Stub(("127.0.0.1", 0), _Handler)
    server.statuses, server.bodies = [], []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


def _client(port: int, *, read_timeout_s: float = 5.0) -> openai.OpenAI:
    return make_client(
        base_url=f"http://127.0.0.1:{port}", api_key="sk-test", read_timeout_s=read_timeout_s
    )


def _send(client: openai.OpenAI) -> Any:
    return lambda: client.chat.completions.create(
        model="extractor", messages=[{"role": "user", "content": "masked text"}]
    )


def test_sdk_retries_are_off(stub: _Stub) -> None:
    stub.statuses = [503, 503, 503]
    clock = _Clock()
    result = run(
        _send(_client(stub.server_address[1])), clock=clock, sleep=clock.sleep, busy_delays=()
    )
    assert len(stub.bodies) == 1, "the SDK retried on its own"
    assert isinstance(result, Wait)


def test_503_503_200_succeeds_on_the_third_request(stub: _Stub) -> None:
    stub.statuses = [503, 503, 200]
    clock = _Clock()
    result = run(_send(_client(stub.server_address[1])), clock=clock, sleep=clock.sleep)
    assert isinstance(result, Success)
    assert len(stub.bodies) == 3 and clock.sleeps == [2, 4]
    assert len(set(stub.bodies)) == 1  # retries resend identical bytes


def test_closed_port_is_unreachable() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    clock = _Clock()
    result = run(_send(_client(port)), clock=clock, sleep=clock.sleep)
    assert isinstance(result, Wait) and result.failure_class is FailureClass.UNREACHABLE


def test_400_is_permanent_with_one_request(stub: _Stub) -> None:
    stub.statuses = [400]
    clock = _Clock()
    result = run(_send(_client(stub.server_address[1])), clock=clock, sleep=clock.sleep)
    assert isinstance(result, Permanent) and len(stub.bodies) == 1


def test_read_timeout_is_busy(stub: _Stub) -> None:
    stub.stall_seconds = 0.5
    clock = _Clock()
    client = _client(stub.server_address[1], read_timeout_s=0.1)
    result = run(_send(client), clock=clock, sleep=clock.sleep, busy_delays=())
    assert isinstance(result, Wait) and result.failure_class is FailureClass.BUSY
