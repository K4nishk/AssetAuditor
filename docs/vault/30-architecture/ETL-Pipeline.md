---
tags: [architecture, etl]
---

# ETL Pipeline (bronze → silver → gold)

Layers defined in [[../10-mental-models/Medallion-Architecture]]; parsing tiers in [[../40-research/PDF-Statement-Parsing]]; lineage contract in [[../40-research/Lineage-OpenLineage]].

## Flow
1. **Upload** — frontend gets a signed Blob URL from the API; file lands in `bronze/{user_id}/{yyyy-mm}/{uuid}.{ext}`; `bronze_files` row records sha256, institution guess, period, size.
2. **Extract** — worker picks up the job (Postgres `etl_jobs` table as queue, `FOR UPDATE SKIP LOCKED` — no extra queue infra). pdfplumber first; Docling fallback; CSV/JSON go straight to adapters.
3. **Mask** — account numbers → last-4 tokens; names/addresses redacted. Masking happens before any LLM call and before silver write.
4. **Stage + confirm** — parsed rows land in `staged_rows` with per-field confidence; user reviews on the parse-confirm screen (low-confidence highlighted); edits are recorded as `extraction_method='manual_correction'`.
5. **Load silver** — confirmed rows → canonical parquet in Blob (`silver/{user_id}/{entity}/{period}.parquet`) + reference rows in Postgres.
6. **Transform gold** — deterministic SQL/Python builds: `networth_snapshots`, `term_buckets`, `diversification_cuts`, `room_ledger`, exported also as CSVs (`gold/{user_id}/*.csv`).
7. **Lineage** — every step emits OpenLineage START/COMPLETE/FAIL into `lineage_events`.

## Idempotency & replay
- Re-uploading the same file (same sha256) is a no-op with a friendly message.
- Silver is append-only per (institution, period, statement-hash); re-parse creates a new run superseding the old (old rows kept, marked `superseded_by_run`).
- Gold is fully rebuildable: `rebuild_gold(user_id)` is a first-class operation and the recovery story.

## Failure posture
Every job ends in exactly one of `succeeded | failed(reason) | needs_user(action)`. `needs_user` covers: unknown institution layout, low-confidence rows, password-protected PDF, scanned/no-text-layer PDF (Assumption A3).
