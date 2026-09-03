"""Unit tests for app.obs.logging (KCH-66 / AA-29)."""

from __future__ import annotations

import io
import json
import logging

from app.obs.logging import JsonFormatter, RedactingFilter, redact_log_text


def _record(msg: str, *args: object) -> logging.LogRecord:
    return logging.LogRecord(
        name="worker.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


def test_redact_log_text_masks_a_bare_long_digit_run():
    text = redact_log_text("account number 1234567890123 flagged")
    assert "1234567890123" not in text
    assert text.endswith("...0123 flagged")


def test_redact_log_text_masks_email_and_sin():
    text = redact_log_text("user alex@example.com SIN 123-456-789 failed parse")
    assert "alex@example.com" not in text
    assert "123-456-789" not in text
    assert "[REDACTED_EMAIL]" in text
    assert "[REDACTED_SIN]" in text


def test_redact_log_text_leaves_short_ids_and_dollar_amounts_alone():
    text = redact_log_text("job 4821 failed, amount $1234.56")
    assert "job 4821 failed" in text
    assert "$1234.56" in text


def test_redacting_filter_rewrites_msg_and_clears_args():
    record = _record("account %s failed for user %s", "1234567890123", "alex@example.com")

    RedactingFilter().filter(record)

    assert "1234567890123" not in record.msg
    assert "alex@example.com" not in record.msg
    assert record.args == ()
    # getMessage() must not try to re-interpolate the now-argless record.
    assert record.getMessage() == record.msg


def test_redacting_filter_is_applied_before_formatting_in_a_real_handler():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactingFilter())
    handler.setFormatter(JsonFormatter())

    logger = logging.getLogger("test.obs.logging.integration")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    try:
        logger.info("card %s charged", "1234567890123")
    finally:
        logger.removeHandler(handler)

    payload = json.loads(stream.getvalue())
    assert "1234567890123" not in payload["message"]
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.obs.logging.integration"


def test_json_formatter_includes_exc_info_when_present():
    formatter = JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="worker.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failed",
            args=(),
            exc_info=sys.exc_info(),
        )
    payload = json.loads(formatter.format(record))
    assert "ValueError: boom" in payload["exc_info"]
