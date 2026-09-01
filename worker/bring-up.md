# Worker box bring-up

The ETL worker runs on the owner's GPU box via `docker compose` (ADR v1.1.0 §2) —
outbound-only, no inbound ports, nothing exposed to the internet. This is the
runbook for bringing that box online. Everything below is unverified in the
agent sandbox that authored it (no Docker daemon, no network, no credentials);
run it for real on the box and fix anything that doesn't match reality.

## Prerequisites

- Docker + Compose v2 (`docker compose version`).
- A Supabase project already created (production project — the CI "branch" DB
  in `.env.example`'s last section is separate and unrelated to the worker).
- `WORKER_DATABASE_URL`: the project's direct Postgres connection string,
  connected as `service_role` (bypasses RLS — see `app/db/migrations/0001_init.sql`'s
  comments on `worker_heartbeat`/reference tables). Find it in the Supabase
  dashboard under Project Settings → Database → Connection string.

## First bring-up

```bash
cd AssetAuditor/worker
cp ../.env.example .env      # fill in WORKER_DATABASE_URL at minimum; the rest
                              # (GROQ_API_KEY, BLOB_READ_WRITE_TOKEN, ...) are
                              # only needed once their owning issues land
docker compose up -d         # worker + litellm, Groq-only — no --profile gpu yet
docker compose logs -f worker
```

`llm/litellm.config.yaml` (AA-16) routes the `extractor` model group to Groq;
set `GROQ_API_KEY` and `LITELLM_MASTER_KEY` in `.env` for the `litellm`
container to actually serve requests. Without them it still starts (compose
doesn't fail), but any call to `worker/extract/llm_tier.py`'s fallback tier
will error at Groq auth, not at startup.

Once the GPU box's driver/toolkit are in place (AA-33), add the vLLM service
without touching anything else:

```bash
docker compose --profile gpu up -d
```

## Heartbeat check

The worker upserts a single row into `public.worker_heartbeat` every 30s while
running (`worker/main.py`) — this is the "is the box actually reachable and
talking to the DB" smoke test for these deploy rails, and later (AA-34) drives
the "queued — will process when your worker is online" UX. Confirm it's alive:

```sql
select id, last_beat_at, status, now() - last_beat_at as age
from public.worker_heartbeat;
```

`age` should stay under ~30s while the container is running. If `docker compose
up` succeeds but this row never updates, check `WORKER_DATABASE_URL` first —
the container has no other way to reach Supabase.

The same row backs `GET /api/uploads/{id}/status`'s "queued — will process
when your worker is online" message (AA-34) — a pending upload's response
flips `worker_online: false` once `age` exceeds ~90s.

## Metrics check

The worker also serves Prometheus text format on `/metrics` (`worker/metrics.py`,
port `WORKER_METRICS_PORT`, default `9100`, `expose`d on the compose network
only — not published to the host or internet). From inside the compose
network (e.g. `docker compose exec worker curl -s localhost:9100/metrics`),
`worker_heartbeat_timestamp` should track `now()` within ~30s and
`etl_jobs_queued` should match `select count(*) from public.etl_jobs where
status = 'pending'`. Grafana Alloy scraping this endpoint into Grafana Cloud,
and the `worker/observability/stale_while_queued_alert.yaml` alert rule that
reads it, are wired up in AA-27.

## Bringing the box down / recovery

The worker is stateless (bronze/silver live in Blob, all state in Supabase) —
`docker compose down` is safe at any time, and `docker compose up -d` anywhere
resumes the queue. Nothing on the box is the only copy of anything.

## Vercel side (frontend + `api/index.py`)

No compose file needed here — Vercel builds straight from the repo:

1. Connect the GitHub repo in the Vercel dashboard (Hobby/free tier). Vercel
   reads `vercel.json` at the repo root: builds `frontend/` (Vite) as static
   output and auto-detects `api/index.py` as a Python serverless function.
2. Set the env vars listed under "Vercel serverless functions" and "Frontend
   build" in `.env.example`, in the Vercel project's encrypted env store —
   never in the repo.
3. Push to a branch → Vercel deploys a preview; push to `main` → production
   deploy. No custom GitHub Action needed for this (`ci.yml` covers lint/test/
   migration-dry-run; deployment itself is Vercel's native git integration).
4. Smoke test: `GET /api/health` on the deployed URL should return
   `{"status": "ok", "service": "AssetAuditor"}`.

**Unverified**: no Vercel token or account access exists in the agent sandbox
that wrote this — the dashboard steps above have not been exercised against a
real Vercel project. `app/main.py`'s `/api/health` route and `vercel.json`'s
rewrite rules were checked locally with FastAPI's `TestClient` only.

## CI "branch" DB (`migration-dry-run` job in `.github/workflows/ci.yml`)

That job needs `SUPABASE_ACCESS_TOKEN`, `SUPABASE_PROJECT_ID`, and
`SUPABASE_DB_PASSWORD` as GitHub Actions repo secrets, pointing at a **second**,
dedicated free-tier Supabase project used only as a throwaway migration
target — not the worker's production project, and not the paid Branching
add-on (ADR v1.1.0's zero-cost contract). Until those three secrets are set,
the job reports `::notice::` and passes without running. Provisioning that
project and wiring the secrets is a manual step for the owner; no credentials
exist in this sandbox to do it here.
