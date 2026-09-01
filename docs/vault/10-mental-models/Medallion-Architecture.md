---
tags: [mental-model, etl]
---

# Medallion Architecture (bronze → silver → gold)

- **Bronze** = raw uploads, any format (PDF/CSV/JSON), stored as-is in Vercel Blob, PII intact, encrypted at rest, deleted after 14 days.
- **Silver** = normalized, typed, masked Parquet: one canonical schema per entity (`transactions`, `holdings`, `accounts`, `liabilities`), institution quirks resolved by adapters.
- **Gold** = curated consumption tables (Postgres) + exported CSVs: net-worth snapshots, term buckets, diversification cuts, contribution-room ledgers.

**Why it matters:** it makes retention, masking, and lineage each live at exactly one layer. Bronze is the only layer holding unmasked PII, so the 14-day deletion policy automatically bounds PII exposure. Silver is the replayable contract; gold is disposable and can always be rebuilt from silver.

**Gotcha:** with bronze gone after 14 days, silver becomes the *effective* source of record — silver must retain enough fidelity (and lineage metadata) to re-derive gold forever. Never put a "we'll fix it in gold" hack into a silver adapter.

See [[Provenance-First]], [[../30-architecture/ETL-Pipeline]].
