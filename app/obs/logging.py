"""Structured logging with a PII/secret redaction filter (KCH-66 / AA-29).

Security-Model.md threat #4: "Secret leakage in logs -> structured logging
with a redaction filter." Every process in this codebase — the FastAPI app
and each of the worker's `python -m worker.*` entrypoints — should call
`configure_logging()` once at startup instead of `logging.basicConfig`
directly, so every log line (from every module, not just ones that remember
to redact by hand) passes through `RedactingFilter` before it reaches stdout,
where both the worker's log tail and Vercel's function logs end up.

This does not replace `worker.masking` — that module scrubs *statement text*
before it is staged or sent to an LLM, a much larger and less predictable
surface (whole PDF/CSV documents). This filter scrubs *log messages*, a
smaller, structured-call-site surface, so it reuses `worker.masking.redact_pii`
for emails/SINs/phone numbers and adds one extra rule: any bare run of 8+
digits, which is never a legitimate thing to log (job/run ids are UUIDs,
dollar amounts never carry that many digits) but is exactly the shape an
account/card number takes if one is ever interpolated into a log line by
mistake.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime

from worker.masking import redact_pii

_DIGIT_RUN_RE = re.compile(r"\b(?:\d[ -]?){8,}\d\b")


def _redact_digit_runs(text: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        digits = re.sub(r"[ -]", "", match.group(0))
        return f"...{digits[-4:]}"

    return _DIGIT_RUN_RE.sub(_replace, text)


def redact_log_text(text: str) -> str:
    """Apply the same redaction every log line gets before it leaves this process."""
    return _redact_digit_runs(redact_pii(text))


class RedactingFilter(logging.Filter):
    """Redacts PII/account-number-shaped substrings from every log record.

    Resolves `record.msg % record.args` once via `getMessage()` and rewrites
    `record.msg` with the redacted result, clearing `record.args` — this way
    whatever formatter runs next (this module's `JsonFormatter`, or a bare
    default one in a test) can't accidentally re-interpolate a `%`-style
    placeholder that happened to survive redaction inside the message text.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_log_text(record.getMessage())
        record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line — the "structured logging" half of the threat note."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, str] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


_configured = False


def configure_logging(level: int = logging.INFO) -> None:
    """Attach a redacting, JSON-formatted stdout handler to the root logger.

    Idempotent (a second call is a no-op) so it's safe to call from both a
    library entrypoint (`app.main.create_app`) and a `__main__` block without
    doubling handlers. Call this once per process instead of
    `logging.basicConfig` directly — that bypasses `RedactingFilter` entirely.
    """
    global _configured
    if _configured:
        return
    _configured = True

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactingFilter())
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)


__all__ = ["JsonFormatter", "RedactingFilter", "configure_logging", "redact_log_text"]
