# AssetAuditor MVP — issues, not user stories

Written per [linear.app/method/write-issues-not-user-stories](https://linear.app/method/write-issues-not-user-stories): each issue is a concrete task with a clear done-state, written for the person doing the work; the *why* lives in the milestone header, not restated per issue. `deps:` are hard blockers. Priority within a milestone follows: **provenance > user satisfaction > maintainability > testing > docs > timeline**.

Issue IDs are stable (`AA-n`) so tmux build sessions can reference them.

---

## M0 — Scaffolding & rails
*Why: every later issue lands on these rails; CI + RLS + lint discipline must exist before feature code.*

- **AA-1 Scaffold repo per backend template v1** — create the tree in `templates/backend/v1_fastapi_modular/README.md`; empty modules, `pyproject` (uv), Vite+Chakra frontend shell, worker Dockerfile. done: `uv run pytest` and `npm run build` pass on empty project.
- **AA-2 Migration 0001: core schema + RLS on every user table** — tables from ADR v1 §7, RLS policies, `deactivated_at` columns, pgsodium extension enabled. deps: AA-1.
- **AA-3 CI pipeline** — GitHub Actions: ruff+mypy+pytest, frontend lint+build, **SQL-injection lint (fail on f-string SQL)**, migration dry-run against a Supabase branch DB. deps: AA-1.
- **AA-4 Deploy rails** — Vercel project (frontend + `api/index.py`); worker = docker-compose on the owner's GPU box (documented `bring-up.md`: compose file, env, `--profile gpu` for vLLM later); secrets wiring, `.env.example`. done: hello-world API live on Vercel + worker heartbeat visible in DB from the home box. deps: AA-1, AA-3.
- **AA-5 Project CLAUDE.md + session playbook committed** — copy from planning repo; defines tmux session scopes + budget rules. deps: AA-1.

## M1 — Auth, profile, contribution rooms
*Why: the first end-to-end user value (room numbers) with zero statement parsing — proves auth, RLS, and the ledger engine.*

- **AA-6 Supabase Auth integration** — signup/login/logout in frontend; FastAPI JWT verification dependency (JWKS cache); RLS-scoped pool. deps: AA-2, AA-4.
- **AA-7 Profile CRUD + onboarding screen** — wireframe v1 screen 1; facts persisted; country≠CA hides room widgets. deps: AA-6.
- **AA-8 Contribution-room engine (pure) + CRA limits table** — ledger model from `docs/vault/20-domain/Contribution-Rooms.md`; unit tests must reproduce the three golden numbers in `data/samples/README.md` ($41,200 / $10,660 / $12,000). deps: AA-1 only (pure module — parallelizable).
- **AA-9 Rooms screen + ledger drill-down** — every room figure expandable to its grant/contribution entries; `cra_override` reconciliation entry UI. deps: AA-7, AA-8.
- **AA-10 Deactivate + delete account flows** — soft-freeze endpoint + purge job (rows, blobs, auth identity); deletion checklist doc. deps: AA-6.

## M2 — Upload, ETL, lineage (the provenance core)
*Why: the differentiating feature; everything here is provenance-first.*

- **AA-11 Upload path: signed Blob URLs + bronze registry + job queue** — sha256 dedupe, size caps, magic-byte checks; `etl_jobs` with `FOR UPDATE SKIP LOCKED` claim. deps: AA-4, AA-6.
- **AA-12 Masking module** — account-number → last-4 tokens, PII redaction; unit-tested against every fixture; runs before silver write and before any LLM call. deps: AA-1.
- **AA-13 OpenLineage emitter + `lineage_events` table** — START/COMPLETE/FAIL with facets (extraction_method, masking_applied, user_confirmed); every ETL step emits. deps: AA-2.
- **AA-14 CSV/JSON adapters ×6** — questrade, wealthsimple, td, kraken (`Decimal`!), moomoo, equateaccess; each validated against its fixture in CI. deps: AA-11, AA-12, AA-13.
- **AA-15 PDF tier-1: pdfplumber + scotiabank adapter** — parses `scotiabank_sample_statement.pdf` to the same rows as the CSV fixture. deps: AA-11, AA-12, AA-13.
- **AA-16 LLM fallback tier via LiteLLM** — worker calls the LiteLLM endpoint (model group `extractor`), JSON-schema output, temp 0, per-field confidence, masked input only; write `llm/litellm.config.yaml` (Groq-only initially, RPM/TPM caps below the free tier); record `extraction_backend` in lineage facets; golden-set eval in CI (`llm-evals.yml`). deps: AA-12, AA-15.
- **AA-17 Parse-confirm screen** — wireframe v1 screen 2: staged rows, low-confidence highlight, inline edit (logged as `manual_correction`), confirm-all→silver. deps: AA-14 or AA-15.
- **AA-18 Silver parquet writes + gold rebuild** — canonical parquet to Blob; `rebuild_gold(user_id)` computing snapshots/buckets/cuts/room-contribution links; gold CSV export. deps: AA-17, AA-8.
- **AA-19 Retention sweeper** — nightly Action: bronze >14d purged (with lineage tombstone event), DB-persisted logs >4d deleted; `retention_sweeper_last_success_timestamp` metric + alert. **Treat sweeper-stale as a privacy incident.** deps: AA-11, AA-13.
- **AA-20 Manual-entry forms** — portfolio (ticker/shares/avg-cost, optional lots incl. Yahoo Finance export import) and account-balance forms as the no-PDF path, writing the same silver shapes. deps: AA-17.
- **AA-33 LiteLLM router: vLLM promotion + eval-harness hook** — add `vllm` to the compose (`--profile gpu`) and to the LiteLLM fallback chain as primary; fallback test (kill vLLM → Groq picks up after cooldown); golden-set evals runnable against either backend by model-name switch, LiteLLM usage logs feeding the comparison report. deps: AA-16, GPU box available.
- **AA-34 Worker heartbeat + queued-job UX** — `worker_heartbeat` table (30s updates), upload states show "queued — will process when your worker is online", `worker_heartbeat_timestamp`/`etl_jobs_queued` metrics + stale-while-queued alert. deps: AA-11.

## M3 — Dashboards & drill-down
*Why: the visible payoff; drill-down is the lineage query surfaced.*

- **AA-21 Price layer** — daily cron + on-demand refresh (yfinance/OpenBB), `prices` table with source+date; FX CAD conversion recorded per snapshot. deps: AA-18.
- **AA-22 Dashboard: three pies + KPI row** — term-buckets, net-worth distribution, diversification (cut switcher); Recharts; hover=highlight. Numbers must match `data/samples/README.md` reference totals for the mock user. deps: AA-18, AA-21.
- **AA-23 Drill-down panel** — click slice → `/lineage/slice` → underlying rows w/ run, source file (or purge tombstone), method, confirmation timestamp. deps: AA-22, AA-13.
- **AA-24 ETF classification seed + diversification flags** — XEQT/VEQT/VFV/XIC factsheet weights table; risk-profile-dependent flags (crypto >10% for medium, sector >30%, home bias, employer concentration). deps: AA-22.
- **AA-25 Audit commentary (LLM)** — gold facts → plain-language observations card with generated-text disclosure; never advice-shaped. deps: AA-22, AA-16.
- **AA-26 Extra visuals** — net-worth-over-time line, room gauges, fee-drag bar (MER comparison). deps: AA-22.

## M4 — Hardening & observability
- **AA-27 Metrics wiring** — worker `/metrics` + Alloy scrape, API batched remote_write, Grafana dashboards + 3 alerts (ETL failure rate, sweeper stale, LLM errors). deps: M2 done.
- **AA-28 Amplitude events (behaviour-only)** — event schema review gate in CI: payloads containing amount/ticker/account fields fail. deps: AA-22.
- **AA-29 Security pass** — pgsodium column crypto for sensitive mapping table, `pip-audit`/`npm audit` gates, threat-table review vs `docs/vault/30-architecture/Security-Model.md`, e2e run of `skills/e2e-testing/SKILL.md` deletion + masking checklists. deps: M2, M3.
- **AA-30 Per-user derived encryption keys (stretch)** — deps: AA-29.

## M5 — Showcase
- **AA-31 MDX blog page in-app + repo wiki polish** — architecture story with the mermaid diagrams; screenshots from mock-user data only. deps: M3.
- **AA-32 Demo mode** — seed-from-fixtures button so the blog demo never touches real data. deps: AA-18.

### Dependency spine (critical path)
AA-1 → AA-2/3/4 → AA-6 → AA-11 → AA-12/13 → AA-15 → AA-17 → AA-18 → AA-22 → AA-23. Parallel tracks: AA-8 (rooms engine) and AA-14 (CSV adapters) can start early; AA-34 (heartbeat UX) right after AA-11; AA-27 anytime after M2. AA-33 (vLLM promotion) is gated on the GPU box arriving, not on other issues — everything works Groq-only until then.
