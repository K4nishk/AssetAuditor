"""Extraction tier 1: pdfplumber (KCH-50 / AA-15).

Deterministic text+table extraction for text-layer PDF statements — the first
line of `docs/vault/40-research/PDF-Statement-Parsing.md`'s tier strategy.
Institution adapters (`worker/adapters/scotiabank.py` and future ones) consume
this module's output and normalize it into `StagedRowDraft`s; this module
never resolves account numbers or PII itself, and it never falls back to
Docling/LLM tiers (AA-16/future) — that decision belongs to the caller.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import pdfplumber

PDF_MAGIC = b"%PDF-"


@dataclass(frozen=True)
class ExtractedPage:
    text: str
    tables: list[list[list[str | None]]]


@dataclass(frozen=True)
class ExtractedPdf:
    pages: list[ExtractedPage]

    @property
    def text(self) -> str:
        return "\n".join(page.text for page in self.pages)

    @property
    def tables(self) -> list[list[list[str | None]]]:
        return [table for page in self.pages for table in page.tables]


def is_pdf(raw: bytes) -> bool:
    """Cheap magic-byte sniff — use before `extract()` in any `detect()`."""
    return raw.startswith(PDF_MAGIC)


def extract(raw: bytes) -> ExtractedPdf:
    """Extract text + tables from every page of a text-layer PDF.

    Propagates whatever pdfplumber/pdfminer raise on a corrupt or non-PDF
    file — callers (adapter `detect()`s) must catch broadly, since malformed
    input is an expected case here, not a bug.
    """
    pages: list[ExtractedPage] = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            pages.append(
                ExtractedPage(text=page.extract_text() or "", tables=page.extract_tables())
            )
    return ExtractedPdf(pages=pages)
