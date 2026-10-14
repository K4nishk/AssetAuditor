"""Coding: reversible replacement of sensitive values before Egress — AA-64 (#47).

ADR v1.2.0 §3, owner decision R3.3. Coding runs on text that Masking already
processed, and only on payloads bound for an Egress backend. Amounts become
`[A01]`-style codes; every run of words outside the approved vocabulary
(`coding_vocabulary.txt`) becomes a `[P01]`-style code. Dates, the statement's
own account mask and Masking's `[REDACTED…]` markers stay plain. Each
occurrence gets its own code, so a repeated payee is not linkable.

The `CodeMap` holds the plaintext. It lives in worker memory only: it refuses
to be pickled or serialized, its repr shows counts only, and error messages
name codes, never literals.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import NoReturn

_VOCABULARY_FILE = Path(__file__).with_name("coding_vocabulary.txt")
VOCABULARY: frozenset[str] = frozenset(
    line.strip().lower()
    for line in _VOCABULARY_FILE.read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.lstrip().startswith("#")
)

CODE_SHAPE_RE = re.compile(r"\[[A-Z]\d+\]")
REDACTED_RE = re.compile(r"\[REDACTED(?:_[A-Z]+)?\]")
AMOUNT_RE = re.compile(r"(?<![\w.,])(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}(?![\w.,])")
MASK_RE = re.compile(r"(?<![\w.])[a-z0-9]+-\.\.\.\d{4}(?!\w)", re.IGNORECASE)
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip
_MONTH_NAME = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
DATE_RE = re.compile(
    rf"\b\d{{4}}-\d{{2}}-\d{{2}}\b|\b{_MONTH_NAME}\.?\s+\d{{1,2}}(?:,?\s+\d{{4}})?\b",
    re.IGNORECASE,
)
_MIN_YEAR, _MAX_YEAR = 1900, 2100
_WORD_RE = re.compile(r"[^\W_]+(?:[-'&./#*][^\W_]+)*")


def is_real_date(text: str) -> bool:
    """True for an ISO date or a month-day (optionally with year) that exists."""
    try:
        if text[:4].isdigit():
            parsed = date.fromisoformat(text)
        else:
            parts = re.split(r"[\s.,]+", text.strip())
            month, day = _MONTHS[parts[0][:3].lower()], int(parts[1])
            year = int(parts[2]) if len(parts) > 2 and parts[2] else 2000  # 2000: leap year
            parsed = date(year, month, day)
    except (ValueError, KeyError, IndexError):
        return False
    # A "year" outside this window is more likely a number fragment (e.g. the
    # first group of a card number after a date) than a statement date.
    return _MIN_YEAR <= parsed.year <= _MAX_YEAR


class CodeError(ValueError):
    """A code could not be decoded. Messages name the code, never a literal."""


class CodeMap:
    """Code → plaintext, for one statement or one commentary request."""

    __slots__ = ("_counts", "_entries")

    def __init__(self) -> None:
        self._entries: dict[str, tuple[str, str]] = {}
        self._counts = {"A": 0, "P": 0}

    def _add(self, kind: str, literal: str) -> str:
        self._counts[kind] += 1
        code = f"[{kind}{self._counts[kind]:02d}]"
        self._entries[code] = (kind, literal)
        return code

    def code_amount(self, literal: str) -> str:
        if not AMOUNT_RE.fullmatch(literal):
            raise CodeError("not an en-CA two-decimal amount")
        return self._add("A", literal)

    def code_text_run(self, literal: str) -> str:
        return self._add("P", literal)

    def __contains__(self, code: object) -> bool:
        return code in self._entries

    def amounts(self) -> dict[str, Decimal]:
        return {
            code: Decimal(literal.replace(",", ""))
            for code, (kind, literal) in self._entries.items()
            if kind == "A"
        }

    def decode_amount(self, code: str) -> Decimal:
        kind, literal = self._lookup(code)
        if kind != "A":
            raise CodeError(f"{code} is not an amount code")
        return Decimal(literal.replace(",", ""))

    def decode_text(self, text: str) -> str:
        return CODE_SHAPE_RE.sub(lambda m: self._lookup(m.group())[1], text)

    def _lookup(self, code: str) -> tuple[str, str]:
        try:
            return self._entries[code]
        except KeyError:
            raise CodeError(f"unknown code {code}") from None

    def __repr__(self) -> str:
        return f"CodeMap(amounts={self._counts['A']}, text_runs={self._counts['P']})"

    __str__ = __repr__

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        raise TypeError("CodeMap holds plaintext and is never serialized")

    def __getstate__(self) -> NoReturn:
        raise TypeError("CodeMap holds plaintext and is never serialized")


def _code_words(gap: str, code_map: CodeMap, vocabulary: frozenset[str]) -> str:
    """Keep vocabulary words; code each run of other words joined only by spaces."""
    pieces: list[str] = []
    run: list[str] | None = None
    cursor = 0
    for match in _WORD_RE.finditer(gap):
        between, word = gap[cursor : match.start()], match.group()
        if word.lower() in vocabulary:
            if run is not None:
                pieces.append(code_map.code_text_run("".join(run)))
                run = None
            pieces.extend([between, word])
        elif run is not None and between.strip(" \t") == "":
            run.extend([between, word])
        else:
            if run is not None:
                pieces.append(code_map.code_text_run("".join(run)))
            pieces.append(between)
            run = [word]
        cursor = match.end()
    if run is not None:
        pieces.append(code_map.code_text_run("".join(run)))
    pieces.append(gap[cursor:])
    return "".join(pieces)


_PROTECTED_RE = re.compile(
    "|".join(
        f"(?P<{name}>{rx.pattern})"
        for name, rx in (
            ("redacted", REDACTED_RE),
            ("date", DATE_RE),
            ("amount", AMOUNT_RE),
            ("mask", MASK_RE),
        )
    ),
    re.IGNORECASE,
)


def code_text(
    text: str, *, institution_slug: str, vocabulary: frozenset[str] = VOCABULARY
) -> tuple[str, CodeMap]:
    """Code already-masked `text` for Egress; returns the coded text and its map."""
    code_map = CodeMap()
    own_mask_prefix = f"{institution_slug.lower()}-..."
    out: list[str] = []
    cursor = 0
    for match in _PROTECTED_RE.finditer(text):
        out.append(_code_words(text[cursor : match.start()], code_map, vocabulary))
        token, kind = match.group(), match.lastgroup
        if kind == "amount":
            out.append(code_map.code_amount(token))
        elif kind == "mask" and not token.lower().startswith(own_mask_prefix):
            out.append(code_map.code_text_run(token))
        elif kind == "date" and not is_real_date(token):
            out.append(_code_words(token, code_map, vocabulary))
        else:
            out.append(token)
        cursor = match.end()
    out.append(_code_words(text[cursor:], code_map, vocabulary))
    return "".join(out), code_map
