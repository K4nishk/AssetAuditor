# Architecture

Public summary of [ADR v1.1.0](../adr/ADR_v1.1.0.md) (current; v1.0.0 superseded, v2.0.0 rejected/archived) — a **$0/month** split between free-tier cloud and the owner's hardware:

- **Cloud (free tiers):** React + Chakra frontend and FastAPI (Python functions) on Vercel Hobby · Supabase (Postgres system-of-record, Auth, RLS, pgsodium, job queue) · Vercel Blob medallion storage (bronze 14-day TTL / silver parquet / gold CSV) · Grafana Cloud hosted Prometheus (push/scrape via Alloy) · Amplitude + Vercel Analytics (behaviour events only) · GitHub Actions (CI, retention sweeper, price refresh, LLM evals — free on a public repo).
- **Home-lab (owner's GPU box, outbound-only):** one docker-compose running the ETL worker (pdfplumber → Docling → LLM tiers, masking, lineage emission), **LiteLLM** as the single LLM gateway (vLLM primary once the box lands, Groq free tier as rate-capped fallback, cooldown-based failover), and vLLM itself (compose profile-gated). Jobs queue in Postgres while the box is off; a heartbeat surfaces "queued — worker offline" in the UI.
- **Lineage:** OpenLineage-format events in-DB; the dashboard drill-down is the lineage query. The LLM backend that served each extraction is itself recorded in lineage (`extraction_backend`).

Diagrams: the ADR's mermaid blocks (they render on GitHub). Cost contract: ADR v1.1.0 §4 — any component leaving its free tier is a design event, not a charge.
