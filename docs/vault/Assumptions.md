---
tags: [assumptions, review-me]
created: 2026-08-31
---

# Assumptions — ranked by criticality

Criticality 5 = contesting this reworks the core design. 1 = cosmetic. **Review the 5s and 4s first**; each row names its design blast-radius so you know what changes if you overturn it.

| # | Assumption | Crit | Blast-radius if contested |
|---|-----------|------|---------------------------|
| A1 | **Single-user MVP.** You are the only user; multi-tenant correctness (RLS, per-user encryption keys) is designed in but not stress-tested. | 5 | Auth model, RLS policies, key management, pricing of every hosted service |
| A2 | **Canada-only tax logic.** TFSA/RRSP/FHSA engines hardcode CRA rules; `holdings_country` is a schema field but only `CA` has an engine. | 5 | Contribution-room module becomes a pluggable strategy per country; schema for residency history |
| A3 | **Uploaded PDFs have a text layer.** Scanned/image statements (OCR) are out of MVP scope; the parse-confirm screen tells the user to export a digital copy instead. | 4 | ETL adds an OCR stage (Docling/Marker w/ OCR), worker needs more CPU/GPU, cost model changes |
| A4 | **LLM extraction is advisory, never authoritative.** Every parsed row is shown on a confirm screen before it is loaded to silver. No number enters gold without either deterministic parsing or human confirmation. | 4 | If you want zero-touch ingestion, we need per-institution golden parsers + regression suites before trusting automation |
| A5 | **Supabase Postgres is the system of record.** Blob storage holds bronze (raw) and silver (parquet) artifacts; gold lives as both Postgres tables and exported CSVs. | 3 | Query layer, lineage granularity, backup story |
| A6 | **All LLM calls go through a self-hosted LiteLLM router** (on the GPU box): vLLM primary once the box lands, Groq free tier as rate-capped fallback. Vercel AI Gateway dropped. | 3 | Router config contract, eval-harness integration, lineage `extraction_backend` facet |
| A7 | **Market prices come from a free-tier source** (yfinance-style scrape or a free API), refreshed on demand + daily cron, with manual override. Not realtime. | 3 | Dashboard freshness expectations, rate-limit handling, a paid data plan |
| A8 | **ETL runs on the owner's GPU box (home-lab docker-compose), jobs queue while it's off.** "The single user can wait" — availability is owner-hardware-dependent, surfaced via heartbeat UX. | 4 | If 24/7 processing ever matters: paid worker host returns (old ADR v1.0.0) or GHA salvage path (PII tradeoff) |
| A9 | **Retention is enforced by a scheduled job** (GitHub Actions cron calling a signed endpoint): bronze deleted at 14 days, app logs at 4 days. Not realtime TTL. | 2 | If hard-realtime deletion is required: storage lifecycle rules / pg_cron instead |
| A10 | **"Deactivate" = soft-delete** (`deactivated_at` set; data frozen, excluded from processing, restorable). **"Delete account" = hard purge** of rows + blobs + auth identity within 30 days. | 4 | Consent/legal posture, schema (soft-delete columns everywhere), test matrix |
| A11 | **CAD is the base currency**; USD/crypto holdings are converted at Bank of Canada daily noon-style rates recorded at snapshot time. | 3 | Multi-currency ledger design, historical FX backfill |
| A12 | **Per-lot cost basis is optional.** If a source (e.g., Yahoo Finance export, Questrade) provides buy dates/lots we keep them; otherwise avg-cost per holding is enough for MVP dashboards. | 2 | ACB accuracy for tax reporting would require mandatory lot tracking |
| A13 | **Amplitude/Vercel Analytics track behaviour only, never financial values.** Event payloads contain no amounts, tickers, or account identifiers. | 4 | If contested (you *want* value analytics), the masking policy and DPA posture change |
| A14 | **MDX content (blog/wiki pages) ships in-repo**, rendered by the frontend; no external CMS. | 1 | Swap to a hosted CMS later without schema impact |
| A15 | **Mock fixtures are the contract.** `data/samples/*` defines silver-schema expectations per institution; real statements must map into the same shapes. | 3 | If real statement formats diverge badly, per-institution adapters grow beyond the fixture contract |
| A16 | **Zero-cost contract.** Every component runs on a free tier or owner hardware (ADR v1.1.0 §4); any component leaving its free ceiling is a design event requiring an ADR bump, never a silent charge. | 5 | Relaxing it reopens paid workers, realtime price feeds, managed lineage — a different cost/benefit frame entirely |

## Notes
- A4 + A13 together are the **provenance/privacy spine** — see [[10-mental-models/Provenance-First]] and [[20-domain/Data-Retention-and-Privacy]].
- A8 + A16 are the core of [[../adr/ADR_v1.1.0|ADR v1.1.0]] (home-lab worker, zero-cost contract); the old paid-worker/serverless fork (v1.0.0 vs v2.0.0) is resolved and archived.
