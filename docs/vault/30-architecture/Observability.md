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
- `etl_jobs_total{status,institution}` · `etl_job_duration_seconds` (histogram) — `worker/metrics.py`, recorded by `worker.queue.release_job` (AA-27). `etl_rows_parsed_total{extraction_method}` is not yet wired: its only current call site (`app.db.queries.staged_rows.insert_draft`) runs in the API process, not the worker, and there is no adapter/LLM-tier dispatch into the job loop yet to instrument on the worker side (see CONTRACT_OUT.md).
- `llm_requests_total{outcome,backend}` · `llm_tokens_total{backend}` (backend = vllm|groq; watchdog for the Groq free-tier caps) — recorded directly in `worker.extract.llm_tier.extract_transactions` and `worker.commentary.request_commentary` (AA-27).
- `worker_heartbeat_timestamp` + `etl_jobs_queued` (paired for the offline-while-queued alert)
- `retention_sweeper_last_success_timestamp` (alert if stale > 26h)
- API: `api_requests_total{route,method,status}` + `api_request_duration_seconds_{sum,count}{route,method,status}` — `app/obs/metrics.py`'s `metrics_middleware`, one batched `remote_write` push per request (AA-27).

**Cardinality rule:** labels are enums only — never user IDs, filenames, or tickers. API route labels use the matched path *template* (e.g. `/api/staged/{job_id}/rows`), never the raw request path.

**Alerts (AA-27):** `worker/observability/*.yaml` — `stale_while_queued_alert.yaml` (AA-34), `retention_sweeper_stale_alert.yaml` (AA-19), `etl_failure_rate_alert.yaml`, `llm_error_rate_alert.yaml`. All four are Grafana file-provisioning definitions; applying them to a live Grafana Cloud instance is a manual step (`worker/bring-up.md`'s "Alloy scrape + Grafana Cloud" section) — none of it is verified against a real instance from this repo.
