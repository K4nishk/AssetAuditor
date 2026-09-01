"""Account-number + PII redaction, run before silver write and any LLM call — AA-12."""

from __future__ import annotations

import re

_DIGIT_RE = re.compile(r"\d")

# Institution-provided partial masks: `****4821`, `XXXXXXXX4821`, `#### 4821`.
# `*`/`#` aren't word characters, so `\b` can't anchor the left edge — use a
# negative lookbehind instead so this doesn't require a preceding word character.
_MASKED_STYLE_RE = re.compile(r"(?<!\w)[*#Xx]{2,}[*#Xx\s]*\d{4}\b")

# A raw account number directly labeled on the same line, e.g.
# "Account Number: 1234567890" or "Acct# 4821". Deliberately requires the
# number to start immediately (allowing only a colon/hash/space run) after
# the label so we never swallow unrelated digits later in the line (dollar
# amounts, dates, etc.). The lead-in group is optional so a bare last-4
# (e.g. "Acct# 4821") still matches, not just longer raw/partial-mask numbers.
_LABELED_ACCOUNT_RE = re.compile(
    r"(?i)\b(?:account|acct)\.?\s*(?:no\.?|number)?\s*[:#]?\s*"
    r"((?:[*#Xx\d][*#Xx\d\-\s]{0,24})?\d{4})\b"
)

# Whole-line labeled PII fields: "Customer: ALEX MOCK 123 Sample St, Toronto ON".
_LABELED_PII_RE = re.compile(
    r"(?im)^(customer|account holder|name|address|client)\s*:\s*.*$"
)

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b")
_SIN_RE = re.compile(r"\b\d{3}(?:[-\s]?\d{3}){2}\b")

# Canonical masked-token shape produced by this module: `{institution}-...{last4}`.
_ALREADY_MASKED_RE = re.compile(r"^[a-z0-9]+-\.\.\.\d{4}$")


def mask_account_number(raw: str, institution_slug: str) -> str:
    """Normalize any raw or partially-masked account number to `{institution}-...{last4}`.

    The full number is never retained: only the last 4 digits survive.
    """
    digits = _DIGIT_RE.findall(raw)
    if len(digits) < 4:
        raise ValueError(f"cannot derive last-4 from account number: {raw!r}")
    last4 = "".join(digits[-4:])
    return f"{institution_slug}-...{last4}"


def redact_account_numbers(text: str, institution_slug: str) -> str:
    """Replace institution-masked and labeled raw account numbers with the canonical token."""

    def _replace(match: re.Match[str]) -> str:
        number = match.group(1) if match.lastindex else match.group(0)
        return mask_account_number(number, institution_slug)

    text = _MASKED_STYLE_RE.sub(_replace, text)
    text = _LABELED_ACCOUNT_RE.sub(_replace, text)
    return text


def redact_pii(text: str) -> str:
    """Strip names, addresses, emails, phone numbers, and SINs from statement text."""
    text = _LABELED_PII_RE.sub(lambda m: f"{m.group(1)}: [REDACTED]", text)
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = _SIN_RE.sub("[REDACTED_SIN]", text)
    text = _PHONE_RE.sub("[REDACTED_PHONE]", text)
    return text


def mask_statement_text(text: str, institution_slug: str) -> str:
    """Full pipeline: account numbers -> last-4 tokens, then PII redaction.

    Must run on every statement before it is staged to silver or sent to any LLM.
    """
    text = redact_account_numbers(text, institution_slug)
    return redact_pii(text)


def is_masked_account_token(value: str) -> bool:
    """True if `value` already matches the canonical `{institution}-...{last4}` shape."""
    return bool(_ALREADY_MASKED_RE.match(value))
