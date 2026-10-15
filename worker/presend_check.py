"""Pre-send no-plaintext check for Egress — AA-64 (#47), ADR v1.2.0 §3.

Runs on the exact text about to leave the Box, immediately before every
Egress send (including each retry), and fails closed. It admits only:

- codes present in this request's `CodeMap`;
- real calendar dates;
- the statement's own institution account mask;
- Masking's `[REDACTED…]` markers;
- the renderer's exact percentages (one-decimal `NN.N%` strings, passed in);
- words in the approved vocabulary, and punctuation.

Anything else — any other digit (superscripts included), word, unmapped
code-shaped token or foreign mask — blocks the send. Any error inside the
check also blocks. The result carries per-rule counts only, never text.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from worker.coding import (
    CODE_SHAPE_RE,
    DATE_RE,
    MASK_RE,
    REDACTED_RE,
    VOCABULARY,
    CodeMap,
    is_real_date,
)

_PERCENTAGE_RE = re.compile(r"-?\d{1,4}(?:\.\d)?%")
_TOKEN_RE = re.compile(r"[^\W_]+(?:[-'&][^\W_]+)*")


@dataclass(frozen=True)
class CheckResult:
    counts: dict[str, int]

    @property
    def passed(self) -> bool:
        return not any(self.counts.values())


def check_text(
    text: str,
    *,
    code_map: CodeMap,
    institution_slug: str,
    percentages: Iterable[str] = (),
    vocabulary: frozenset[str] = VOCABULARY,
) -> CheckResult:
    counts: Counter[str] = Counter()
    try:
        rest = text
        for pct in sorted(set(percentages), key=len, reverse=True):
            if not _PERCENTAGE_RE.fullmatch(pct):
                counts["bad_percentage"] += 1
                continue
            rest = rest.replace(pct, " ")

        def drop_code(match: re.Match[str]) -> str:
            if match.group() not in code_map:
                counts["unmapped_code"] += 1
            return " "

        rest = CODE_SHAPE_RE.sub(drop_code, rest)
        rest = REDACTED_RE.sub(" ", rest)
        rest = DATE_RE.sub(lambda m: " " if is_real_date(m.group()) else m.group(), rest)

        own_prefix = f"{institution_slug.lower()}-..."

        def drop_mask(match: re.Match[str]) -> str:
            if not match.group().lower().startswith(own_prefix):
                counts["foreign_mask"] += 1
            return " "

        rest = MASK_RE.sub(drop_mask, rest)
        for token in _TOKEN_RE.findall(rest):
            if any(unicodedata.category(ch).startswith("N") for ch in token):
                counts["stray_digit"] += 1
            elif token.lower() not in vocabulary:
                counts["off_vocabulary_word"] += 1
    except Exception:  # fail closed: a broken check never lets a payload out
        counts["check_error"] += 1
    return CheckResult(dict(counts))
