# Local quickstart

Getting a working local session, in the order that actually works. Written against
`feature/kch-69` (KCH-69 + KCH-68 + KCH-72) on 2026-09-04.

**Read [Environment-Gotchas](Environment-Gotchas.md) first if anything below fails.** Every
failure mode in this page is already documented there, in more detail, with the reason.

## What you can run locally, and what you can't

| | Needs | Works offline |
|---|---|---|
| Unit + API tests | `.venv` (pre-provisioned) | yes |
| DB-backed tests | Postgres (Docker) | yes |
| FastAPI server | `DATABASE_URL` | only against a real Supabase DB |
| Frontend dev server | `VITE_SUPABASE_*` | boots, but cannot sign in |
| Demo seed button | Supabase + Vercel Blob | no |

No Supabase project, Vercel token, Groq key or GPU box exists on this machine. Roughly a
third of the backlog is written, committed and **flagged unverified** for exactly this
reason — see Project-Status. Don't read "unverified" as "broken".

## 1. Run the tests — the fastest useful thing

Dependencies are pre-provisioned; do not `uv add`, `pip install` or `npm install`.

```bash
.venv/bin/python -m pytest -q
```

For just the demo-mode surface (KCH-69):

```bash
.venv/bin/python -m pytest tests/unit/test_demo_domain.py tests/api/test_demo_routes.py tests/db/test_demo_seed_live.py -q -rs
```

`-rs` prints skip reasons, which is how you tell "no Postgres" from "test is broken".
Expect **21 passed, 2 skipped** with no Postgres running; the 2 skips are the live-DB
tests covered in the next step.

Lint and types, the same way CI runs them:

```bash
.venv/bin/python -m ruff check . && .venv/bin/python -m mypy .
```

## 2. Postgres — use Docker, not Homebrew

The DB-backed tests spin up an ephemeral cluster with `initdb`. On this machine that
fails:

```
initdb: could not create shared memory segment: No space left on device
```

That is **not** a disk problem. macOS allocates a small fixed pool of SysV shared-memory
identifiers at boot (`shmmni` default 32) and long uptimes leak them; `kern.sysv.*` is
largely read-only after boot, so a **reboot is the real fix**. Two Homebrew-service
failures stack on top of it — see Environment-Gotchas § "PostgreSQL — the big one".

The workaround that sidesteps all three, because containers get their own IPC namespace:

```bash
docker run -d --name aa-postgres -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_USER=$(whoami) -e POSTGRES_DB=$(whoami) -p 5432:5432 postgres:16
```

Then always probe over TCP — a container publishes TCP only and never creates the UNIX
socket in `/tmp` that a bare `pg_isready` looks for:

```bash
pg_isready -h localhost && export PGHOST=localhost
```

Without `PGHOST` set, tooling reports "no PostgreSQL" while a healthy container is
listening.

## 3. Migrations don't apply to a plain Postgres

`app/db/migrations/` targets Supabase specifically:

- `0001_init.sql` — `create extension pgsodium`, and every user table references
  `auth.users (id)`
- `0004_account_number_vault_encryption.sql` — `pgsodium.create_key`,
  `crypto_aead_det_encrypt`
- RLS policies throughout are written against `auth.uid()`

A stock `postgres:16` container has neither the `auth` schema nor pgsodium, so applying
these by hand fails. **Don't hand-port them.** The live tests already solve this properly
— they create an `auth` schema stub and strip the pgsodium extension line at load time
(`tests/db/test_demo_seed_live.py`, `MIGRATION_SQL_LOCAL` / `AUTH_STUB_SQL`). Copy that
approach if you need a scratch DB; use a real Supabase project if you need the real thing.

## 4. The API server

```bash
DATABASE_URL='postgresql://…' .venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

`app/main.py` builds the app at import (`app = create_app()`), so `app.main:app` is the
right target. Every route is mounted under `/api` to match `vercel.json`'s rewrite.

Check it's up without needing a database:

```bash
curl -s localhost:8000/api/health
```

`{"status":"ok","service":"AssetAuditor"}`. The pool is created lazily
(`app/db/pool.py:37`), so health answers before `DATABASE_URL` is ever touched — a green
health check does **not** mean your database works.

## 5. The frontend

Node 18, Vite 5 pinned. Do not upgrade Vite without upgrading Node first
(`source ~/.nvm/nvm.sh && nvm install 22`).

```bash
cd frontend && npm run dev
```

Two things will bite you:

**It throws at import without Supabase vars.** `frontend/src/lib/supabaseClient.ts:10`
raises `VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY must be set` before React renders.
Put both in `frontend/.env.local`.

**There is no `/api` dev proxy.** `frontend/src/lib/api.ts:5` sends every request to the
relative path `/api/...` and its comment claims this "works identically in dev (Vite
proxy) and in production" — but `frontend/vite.config.ts` declares no `server.proxy`, so
in dev those calls hit Vite on :5173 and 404. Until that's fixed, add:

```ts
server: { proxy: { "/api": "http://localhost:8000" } },
```

The one page that works with none of this is the public blog route,
`/blog/architecture-story` — it is mounted ahead of the auth gate and renders from
fixture totals, never a live account.

## 6. Demo mode (KCH-69 / AA-32)

`POST /api/demo/seed` rebuilds the demo account from `data/samples/` through the real
bronze → silver → gold pipeline, so lineage drill-down is honest rather than staged.

Both demo routes require an authenticated session, and the seed is gated twice
(`app/routes/demo.py:70`):

- `DEMO_USER_ID` unset → **503** `demo mode is not configured`, and the button never appears
- signed in as anyone else → **403** `seed-from-fixtures only runs against the demo account`

So you need: `DEMO_USER_ID` set to one Supabase `auth.users` id, a login for that exact
account, `BLOB_READ_WRITE_TOKEN` (a real Vercel Blob token —
`app/uploads/blob.py:193` has no local fake), and `DATABASE_URL`. Provisioning the shared
demo login is a manual owner step, deliberately not in this repo (`.env.example:65`).

Check the gate before hunting a missing button:

```bash
curl -s localhost:8000/api/demo/status -H "Authorization: Bearer $TOKEN"
```

7 of the 9 fixtures load. `canadalife_rpp.json` (no adapter yet) and
`scotiabank_chequing_savings.csv` (the golden twin of the PDF fixture — loading both
would double the chequing balance) come back in `fixtures_skipped` rather than being
silently dropped. Check the totals against `data/samples/README.md`.

> **Use a throwaway Supabase project and blob store.** Per **KCH-74**, the seed calls
> `blob.delete_prefix` inside the request transaction (`app/routes/demo.py:203`) but the
> delete is a real, non-transactional API call. Any failure afterwards rolls the rows back
> while the blobs stay deleted, leaving rows pointing at objects that no longer exist. On a
> scratch account that's a re-click. Do not point this at anything you care about until
> KCH-74 is resolved.

## Where to go next

- [Project-Status](Project-Status.md) — where the build is and the cold-start reading order
- [Environment-Gotchas](Environment-Gotchas.md) — the machine's landmines, each already paid for
- [Runbooks](Runbooks.md) — the ops loops (builder, review sweeper, PR gate)
- `ops/README.md` — the operator runbook · `CLAUDE.md` — binding conventions
- `data/samples/README.md` — fixtures and golden numbers
