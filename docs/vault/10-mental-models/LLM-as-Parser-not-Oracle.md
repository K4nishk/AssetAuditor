---
tags: [mental-model, llm]
---

# LLM as Parser, not Oracle

The LLM's job is narrow: turn messy statement text (already extracted by Docling/pdfplumber) into schema-constrained JSON rows, and later write audit *commentary*. It never invents numbers, never does arithmetic that matters (contribution rooms, totals, conversions are deterministic Python), and its output is always staged for user confirmation before load.

**Why it matters:** financial auditing demands reproducibility. A model temperature away from a wrong net worth is unacceptable; a model that pre-fills a form the user confirms is a huge UX win at zero trust cost.

**Consequences:**
- Structured output (JSON schema / tool-call mode) with per-field confidence; rows below threshold highlighted on the confirm screen.
- Masking happens *before* the LLM call: account numbers, names, addresses are redacted from the prompt (Groq is a third party).
- Deterministic parsers per institution are the goal; the LLM is the fallback for unrecognized layouts and the bootstrapper for writing new adapters.

See [[../30-architecture/LLM-Strategy]], [[../40-research/PDF-Statement-Parsing]], [[Provenance-First]].
