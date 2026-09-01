---
tags: [research, etl]
verified: 2026-08-31
---

# PDF statement parsing — tool survey

Bank statements are hostile: merged headers, per-institution column layouts, balances wrapping across pages, debit/credit sometimes one signed column. Strategy: **deterministic-first, LLM-fallback** ([[../10-mental-models/LLM-as-Parser-not-Oracle]]).

| Tool | Nature | Verdict | Notes |
|---|---|---|---|
| [pdfplumber](https://github.com/jsvine/pdfplumber) | Pure-Python text+table extraction | **leverage (first line)** | Best for digital PDFs with structural lines (Scotiabank/TD style). Fast, no models, deterministic — ideal for per-institution adapters |
| [Docling](https://github.com/docling-project/docling) (IBM) | VLM-based document toolkit, ~97.9% table-cell accuracy | **leverage (fallback tier)** | Strongest on table-dense financial docs; heavier (model inference) — runs on the ETL worker, not serverless. Also the future OCR path if Assumption A3 falls |
| [Marker](https://github.com/datalab-to/marker)/Surya | 5-stage layout pipeline, PDF→markdown | **borrow-ideas** | Faster than Docling on long docs; GPL-ish licensing tier for commercial — fine for personal project but noted |
| Camelot / tabula | Rule-based table extractors | **skip** | pdfplumber covers the same digital-PDF ground with a nicer API |
| unstructured | General doc chunking for RAG | **skip** | RAG-oriented chunking, wrong shape for row-accurate tables |
| LLM structured extraction (Groq + JSON schema) | Model fills canonical rows from extracted text | **leverage (last tier)** | Only ever sees masked text; per-field confidence; output staged to parse-confirm screen |

## Pipeline decision
```
bronze PDF ──pdfplumber──> text+tables ──institution adapter──> silver rows
                 │ (low confidence / unknown layout)
                 └──Docling──> structured doc ──LLM (masked, JSON-schema)──> staged rows → user confirms → silver
```
CSV/JSON uploads (Kraken, Questrade export, Wealthsimple) skip straight to adapters.

Sources: [CodeCut Docling vs Marker vs LlamaParse](https://codecut.ai/docling-vs-marker-vs-llamaparse/) · [Parsing bank statement PDFs, 5 tools (2026)](https://dev.to/urios/parsing-bank-statement-pdfs-5-tools-compared-for-developers-2026-4b70) · [pdfplumber vs Camelot vs Tabula](https://invoicedataextraction.com/blog/python-pdf-table-extraction-invoices)
