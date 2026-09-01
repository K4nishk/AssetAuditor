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

## Threat notes (top items for the ADR's threat table)
1. Uploaded-file attacks (PDF bombs, malformed CSVs) → size caps, magic-byte checks, parse in worker sandbox w/ resource limits.
2. Prompt injection via statement text ("ignore previous instructions" inside a PDF) → LLM output is schema-validated JSON staged for human confirm; model has no tools and no write access.
3. IDOR/tenancy bugs → RLS + JWT-derived user_id + tests.
4. Secret leakage in logs → structured logging with a redaction filter; logs die at 4 days anyway.
5. Supply chain → lockfiles, Dependabot, `pip-audit`/`npm audit` in CI.
