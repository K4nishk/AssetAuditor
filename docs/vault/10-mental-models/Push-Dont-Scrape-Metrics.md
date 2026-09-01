---
tags: [mental-model, observability]
---

# Push, Don't Scrape (metrics on serverless)

Classic Prometheus *scrapes* long-lived processes on `/metrics`. Vercel functions are ephemeral — there is nothing stable to scrape. The serverless-compatible pattern is to **push**: functions emit metrics to a Prometheus-compatible remote-write endpoint (Grafana Cloud hosted Prometheus/Mimir) or aggregate through a push gateway; the long-lived ETL worker (ADR v1) can still expose a normal `/metrics` scrape target.

**Why it matters:** answers the owner's question "isn't Grafana hosting Prometheus?" — yes: Grafana Cloud's free tier includes a hosted Prometheus-compatible metrics backend with remote-write ingestion, so we keep the Prometheus data model, PromQL, and Grafana dashboards without running a Prometheus server.

**Gotchas:** push-per-invocation is chatty and can bill you on series cardinality — batch metrics, keep label sets tiny (no user IDs, no filenames as labels), and let the ETL worker carry the interesting histograms. See [[../30-architecture/Observability]].
