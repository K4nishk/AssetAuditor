---
tags: [architecture]
updated: 2026-08-31 (v1.1.0 — home-lab worker, LiteLLM)
---

# System Overview

Full detail + diagrams: [[../../adr/ADR_v1.1.0|ADR v1.1.0]] (current) ← supersedes [[../../adr/ADR_v1.0.0|v1.0.0]]; [[../../adr/ADR_v2.0.0|v2.0.0]] rejected/archived.

```
FREE-TIER CLOUD                                OWNER'S GPU BOX (home-lab, outbound-only)
React+Chakra (Vercel Hobby)                    ┌─ docker-compose ───────────────────┐
   │ Supabase Auth (JWT)                       │ worker ── pdfplumber/Docling       │
   ▼                                           │   │       adapters · masking       │
FastAPI (Vercel Python fns)                    │   ├──► LiteLLM ─► vLLM (primary*)  │
   │ raw SQL (RLS)      ▲ poll jobs +          │   │        └────► Groq (fallback)  │
   ▼                    │ heartbeat            │   └──► OpenLineage events          │
Supabase Postgres ◄─────┴──────────────────────│ Grafana Alloy ─► Grafana Cloud     │
Vercel Blob (bronze 14d TTL · silver · gold) ◄─┘  (*once box lands; Groq-only till) └
GitHub Actions: CI · retention sweeper · price refresh · llm evals    — total: $0/mo
```

Component responsibilities, one line each:
- **Frontend** — onboarding, upload, parse-confirm, dashboards w/ hover+drill-down, "queued — worker offline" state; never computes financial math.
- **FastAPI API** — auth verification, CRUD, room engine, dashboard + lineage queries; short-lived requests only.
- **Worker (GPU box)** — parsing, masking, LLM tier via LiteLLM, parquet writes, gold rebuild, lineage emission, 30s heartbeat; the only long-running compute; polls outbound, exposes nothing inbound.
- **LiteLLM** — single LLM gateway: vLLM↔Groq routing, fallback+cooldown, rate caps below Groq free tier ([[LLM-Strategy]]).
- **Supabase** — Postgres (system of record, job queue, heartbeat) + Auth + RLS + pgsodium.
- **Vercel Blob** — bronze (14d TTL via sweeper) and silver parquet, gold CSVs.

Key mental models: [[../10-mental-models/Medallion-Architecture]] · [[../10-mental-models/Provenance-First]] · [[../10-mental-models/Push-Dont-Scrape-Metrics]].
Zero-cost contract: ADR v1.1.0 §4 + Assumption A16 in [[../Assumptions]].
