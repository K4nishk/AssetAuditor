---
tags: [domain, security, retention]
---

# Data Retention & Privacy

The privacy spine, applied per medallion layer ([[../10-mental-models/Medallion-Architecture]]).

## Retention policy (owner-specified)
| Data | TTL | Enforcement |
|---|---|---|
| Bronze raw uploads (PDF/CSV) | **14 days** | nightly sweeper job deletes blobs + marks `bronze_files.purged_at`; lineage keeps hash + metadata only |
| App/ETL logs | **4 days** | log-store retention config + sweeper for DB-persisted job logs |
| Silver parquet | indefinite (masked) | effective source of record once bronze purges |
| Gold tables/CSVs | indefinite, rebuildable | derived; delete-and-rebuild is a supported operation |

## Masking rules (applied bronze → silver, and before any LLM call)
- Account numbers → last-4 + institution slug (`scotia-...4821`); full number never leaves bronze.
- Names, addresses, SINs, emails inside statements → redacted tokens.
- Amplitude/Vercel Analytics: **behavioural events only** — no amounts, tickers, or account identifiers (Assumption A13).
- Grafana metrics: no user-identifying labels ([[../10-mental-models/Push-Dont-Scrape-Metrics]]).

## Encryption
- **In transit:** TLS everywhere (Vercel/Supabase default); no plaintext internal hops.
- **At rest:** Supabase disk encryption + **column-level encryption (pgsodium)** for high-sensitivity columns (masked-account mapping table, user profile facts); Vercel Blob server-side encryption for bronze/silver.
- Key posture: Supabase-managed keys for MVP; per-user derived keys documented as a later hardening issue in `../../mvp.md`.

## Account lifecycle (Assumption A10)
- **Deactivate:** `deactivated_at` set → data frozen, excluded from all processing and dashboards, restorable on request.
- **Delete:** hard purge — DB rows, blobs, lineage payloads (hashes may remain), Supabase Auth identity — completed within 30 days; verified by the e2e skill's deletion checklist.
