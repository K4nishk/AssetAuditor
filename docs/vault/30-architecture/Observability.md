---
tags: [architecture, observability]
---

# Observability

Core pattern: [[../10-mental-models/Push-Dont-Scrape-Metrics]].

| Concern | Tool | How |
|---|---|---|
| Metrics/APM | **Grafana Cloud hosted Prometheus** (free tier) | Grafana Alloy on the GPU box scrapes worker + LiteLLM + vLLM `/metrics` (all three expose Prometheus natively); Vercel functions push batched counters via remote_write. PromQL + Grafana dashboards |
| Web analytics | Vercel Analytics | zero-config page traffic |
| Product analytics | Amplitude | behaviour events only (`statement_uploaded {institution, file_type}`, `parse_confirmed {row_count, corrections}`, `dashboard_drilldown {chart}`) — **no financial values** |
| Logs | Vercel logs + worker stdout → 4-day retention | structured JSON, redaction filter, no PII |
| Alerts | Grafana alerting | ETL failure rate, sweeper-didn't-run (retention breach = privacy incident), LLM error rate, **worker heartbeat stale >2h while jobs queued** (home-lab availability, ADR v1.1.0) |

## Metric set (initial)
- `etl_jobs_total{status,institution}` · `etl_job_duration_seconds` (histogram) · `etl_rows_parsed_total{extraction_method}`
- `llm_requests_total{outcome,backend}` · `llm_tokens_total{backend}` (backend = vllm|groq; watchdog for the Groq free-tier caps)
- `worker_heartbeat_timestamp` + `etl_jobs_queued` (paired for the offline-while-queued alert)
- `retention_sweeper_last_success_timestamp` (alert if stale > 26h)
- API: request count/latency per route (batched push)

**Cardinality rule:** labels are enums only — never user IDs, filenames, or tickers.
