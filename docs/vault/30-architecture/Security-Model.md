---
tags: [architecture, security]
---

# Security Model

Retention/masking/encryption policy: [[../20-domain/Data-Retention-and-Privacy]]. Tenancy posture: [[../10-mental-models/Single-User-First]].

## Auth
- Supabase Auth (email+password for MVP). Frontend holds the Supabase session; FastAPI verifies the JWT (JWKS) on every request and derives `user_id` from it — never from the request body.
- API uses an RLS-scoped Postgres role; the privileged service role exists only in the ETL worker and sweeper, never in serverless env vars exposed to the frontend build.

## Raw SQL discipline (no ORM, owner's choice)
- All SQL lives in versioned `.sql` files / typed query modules with **parameterized queries only** — string-formatted SQL is a CI-failing lint rule.
- Migrations: plain SQL files under `db/migrations`, applied by CI (supabase CLI), reviewed like code.
- RLS on every user table from migration 0001; tests assert cross-user reads return zero rows even with crafted JWTs.

## Secrets & third parties
- Secrets in Vercel/GitHub encrypted env stores; none in repo; `.env.example` documents names only.
- LLM calls leave the worker only via the local LiteLLM proxy; Groq (fallback backend) receives **masked text only**, and vLLM keeps masked text on-box; Amplitude receives behaviour events only (Assumption A13); Grafana receives label-sanitized metrics.

## Account-number vault
`account_number_vault.encrypted_account_number` (migration 0001) holds real,
unmasked account numbers behind pgsodium column encryption (migration 0004,
KCH-66 / AA-29) — a single project-wide key, per-user derived keys are AA-30's
stretch scope. `public.vault_store_account_number`/`vault_reveal_account_number`
(SECURITY DEFINER, `account_id` folded in as AEAD associated data so a
ciphertext copied onto another account_id fails to decrypt) are the only
write/read path; `authenticated`'s raw table grants are revoked so a bug can't
land unencrypted bytes in the bytea column by accident. Nothing in the current
ETL pipeline extracts a real account number yet — every adapter masks via
`worker.masking` before a row is ever staged — so this is unused defensive
infrastructure today, ready for a future feature that legitimately needs it.

## Threat table (AA-29 security-pass review, 2026-09-03)
Reviewed against the actual code, not the aspirational description — see the
"status" column for gaps found and fixed or knowingly deferred during this
pass.

| # | Threat | Mitigation | Verification | Status |
|---|---|---|---|---|
| 1 | Uploaded-file attacks (PDF bombs, malformed CSVs) | Size cap (`app/uploads/validation.py::MAX_UPLOAD_SIZE_BYTES`, 20MB) + magic-byte sniff (`sniff_content_type`) before any parse | `tests/unit/test_upload_validation.py` | **Partial.** Size/type checks are real. "Parse in a resource-limited sandbox" is not: `worker/extract/pdfplumber_tier.py::extract` has no timeout/memory cap, and — pre-existing gap tracked since AA-17/AA-18 — `worker/main.py`'s job loop still only claims+logs, it does not yet call any adapter/pdfplumber/LLM tier at all, so there is no live code path to sandbox yet. Tracked, not fixed here: resource-limiting a CPU-bound sync `pdfplumber`/`pdfminer` call needs a design decision (thread timeout can't kill native code; subprocess isolation changes the module's contract) that belongs with whichever issue finally wires the dispatch, not bolted on to an unused function in a security pass. |
| 2 | Prompt injection via statement text ("ignore previous instructions" inside a PDF) | Masking before any LLM call (`worker.masking.mask_statement_text`); LLM output is strict-schema JSON (`worker/extract/llm_tier.py`), staged for human confirm; model has no tools, no write access | `tests/unit/test_masking.py`, `tests/unit/test_llm_tier.py` | Covered. |
| 3 | IDOR / tenancy bugs | RLS on every user table (migration 0001) + JWT-derived `user_id` (`app.auth.get_current_user_id`, never from the request body) | `tests/db/test_migration_0001_rls.py` (cross-tenant + crafted-JWT reads return zero rows) — runs for real in CI, skips only in sandboxes without live Postgres | Covered. |
| 4 | Secret/PII leakage in logs | Structured (JSON) logging with a `RedactingFilter` (`app.obs.logging`), wired into every process entrypoint (`app.main.create_app`, each `worker.*` `__main__`); logs die at 4 days (retention sweeper) regardless | `tests/unit/test_obs_logging.py` | **Fixed in this pass.** `app/obs/logging.py` was a one-line docstring stub claiming "implemented in AA-27" — AA-27's own contract notes never mention it, and no process called it. Implemented for real here. |
| 5 | Supply chain | Lockfiles (`uv.lock`, `frontend/package-lock.json`), Dependabot, `pip-audit`/`npm audit` as CI gates | `.github/dependabot.yml`, `.github/workflows/ci.yml`'s `pip-audit`/`npm run audit` steps | **Fixed in this pass.** `.github/dependabot.yml` didn't exist; `pip-audit` was an installed-but-unused dev dependency; no `npm audit` step existed. All three added; unverified end-to-end in this sandbox (both tools need registry network the sandbox blocks — confirmed they run and fail on network, not on a code error), so the real gate only proves itself the first time CI runs with network. |
