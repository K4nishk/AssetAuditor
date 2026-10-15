"""Failure classes and the retry budget for model calls — AA-62 (#45), spec #40 decision 1.

The worker, not the openai SDK and not the LiteLLM router, decides whether a
failed model call is retried (ADR v1.2.0 §6). Every failure falls into one
class:

| class       | seen as                                        | retries after              |
|-------------|------------------------------------------------|----------------------------|
| busy        | 408, 429, 503, 504, read timeout               | 2, 4, 8, 16, 30, 30 s      |
| unreachable | no response, connect timeout, 500, 502         | 1 s, 3 s                   |
| permanent   | any other status; parse/decode/validation fail | never                      |

No new attempt starts more than `BUSY_BUDGET_S` after the first, and a
`Retry-After` longer than what is left of that budget stops retrying early.
`run` re-invokes the caller's zero-argument `send` on each retry, so the caller
freezes the request once and every retry resends the same bytes. Outcomes carry
classes and attempt labels only — never error text, request text or values —
so they are safe to log, to record in lineage and to turn into wait reasons.

`make_client` builds the SDK client this policy expects: SDK retries off and
fixed connect/read timeouts. Wiring the policy into the extraction and
commentary call sites, and choosing Local vs Egress, is AA-67.
"""

from __future__ import annotations

import asyncio
import enum
import functools
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

import openai

logger = logging.getLogger("worker.call_policy")

T = TypeVar("T")

BUSY_DELAYS_S: tuple[float, ...] = (2, 4, 8, 16, 30, 30)
UNREACHABLE_DELAYS_S: tuple[float, ...] = (1, 3)
BUSY_BUDGET_S = 120.0
CONNECT_TIMEOUT_S = 5.0
# Placeholders until the AA-66 spike measures the worst chunk time (R4.4).
EXTRACTION_READ_TIMEOUT_S = 120.0
COMMENTARY_READ_TIMEOUT_S = 60.0

_BUSY_STATUSES = frozenset({408, 429, 503, 504})
_UNREACHABLE_STATUSES = frozenset({500, 502})


class FailureClass(enum.StrEnum):
    BUSY = "busy"
    UNREACHABLE = "unreachable"
    PERMANENT = "permanent"


@dataclass(frozen=True)
class Success(Generic[T]):
    value: T
    attempts: tuple[str, ...]


@dataclass(frozen=True)
class Wait:
    """Retries exhausted on a busy or unreachable backend: the job should wait."""

    failure_class: FailureClass
    attempts: tuple[str, ...]


@dataclass(frozen=True)
class Permanent:
    """Never retried; `error_type` is the exception's class name, never its text."""

    error_type: str
    attempts: tuple[str, ...]
    failure_class: FailureClass = FailureClass.PERMANENT


def classify(exc: BaseException) -> FailureClass:
    if isinstance(exc, openai.APIStatusError):
        if exc.status_code in _BUSY_STATUSES:
            return FailureClass.BUSY
        if exc.status_code in _UNREACHABLE_STATUSES:
            return FailureClass.UNREACHABLE
        return FailureClass.PERMANENT
    if isinstance(exc, openai.APITimeoutError):
        # The SDK raises one timeout type for both phases; its cause says which.
        cause = type(exc.__cause__).__name__ if exc.__cause__ is not None else ""
        return FailureClass.UNREACHABLE if "Connect" in cause else FailureClass.BUSY
    if isinstance(exc, openai.APIConnectionError):
        return FailureClass.UNREACHABLE
    return FailureClass.PERMANENT


def _retry_after_s(exc: BaseException) -> float | None:
    response = getattr(exc, "response", None)
    value = response.headers.get("retry-after") if response is not None else None
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def make_client(*, base_url: str, api_key: str, read_timeout_s: float) -> openai.OpenAI:
    """An SDK client with its own retries off; this module owns the budget."""
    return openai.OpenAI(
        base_url=base_url,
        api_key=api_key,
        max_retries=0,
        timeout=openai.Timeout(read_timeout_s, connect=CONNECT_TIMEOUT_S),
    )


def run(
    send: Callable[[], T],
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    busy_delays: Sequence[float] = BUSY_DELAYS_S,
    unreachable_delays: Sequence[float] = UNREACHABLE_DELAYS_S,
) -> Success[T] | Wait | Permanent:
    """Call `send` until it succeeds, fails permanently, or the budget runs out."""
    delays = {
        FailureClass.BUSY: list(busy_delays),
        FailureClass.UNREACHABLE: list(unreachable_delays),
    }
    attempts: list[str] = []
    start = clock()
    while True:
        try:
            value = send()
        except Exception as exc:
            failure = classify(exc)
            attempts.append(failure.value)
            if failure is FailureClass.PERMANENT:
                return Permanent(type(exc).__name__, tuple(attempts))
            if not delays[failure]:
                return Wait(failure, tuple(attempts))
            delay = delays[failure].pop(0)
            remaining = start + BUSY_BUDGET_S - clock()
            retry_after = _retry_after_s(exc)
            if retry_after is not None:
                if retry_after > remaining:
                    return Wait(failure, tuple(attempts))
                delay = max(delay, retry_after)
            if delay > remaining:
                return Wait(failure, tuple(attempts))
            logger.info("model call %s; retry %d in %ss", failure.value, len(attempts), delay)
            sleep(delay)
            continue
        attempts.append("success")
        return Success(value, tuple(attempts))


async def run_async(send: Callable[[], T], **kwargs: Any) -> Success[T] | Wait | Permanent:
    """`run` in a worker thread, so retry sleeps never stall the heartbeat loop."""
    return await asyncio.to_thread(functools.partial(run, send, **kwargs))
