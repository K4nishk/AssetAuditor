-- Migration 0002: audit_commentary table (KCH-62 / AA-25)
--
-- Stores the LLM-generated "plain-language observations" card per user per
-- gold snapshot. Same rebuildable-gold shape as 0001's networth_snapshots /
-- term_buckets / diversification_cuts (worker-only writes, `run_id not null`
-- so a card always traces back to a lineage run, `unique (user_id,
-- snapshot_date)` so a regenerate replaces rather than accumulates rows) —
-- see `app/db/queries/audit_commentary.py` for the replace-then-insert write.
--
-- Persisted at write time (not re-derived at read time) because the LLM call
-- can only ever originate on the worker (ADR v1.1.0 §3: "all LLM callers
-- live on the worker, next to LiteLLM" — LiteLLM is a localhost-only
-- listener there, unreachable from the Vercel-hosted API), so the API can
-- only ever serve a stored row, never call the model itself.
create table public.audit_commentary (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    snapshot_date date not null,
    observations jsonb not null,
    disclosure text not null,
    model_backend text not null,
    run_id uuid not null,
    deactivated_at timestamptz,
    created_at timestamptz not null default now(),
    unique (user_id, snapshot_date)
);

create index audit_commentary_user_id_idx on public.audit_commentary (user_id);

alter table public.audit_commentary enable row level security;

create policy audit_commentary_tenant_isolation on public.audit_commentary
    for all
    using (user_id = auth.uid())
    with check (user_id = auth.uid());

-- Audit commentary is worker-only, same convention as the other gold tables'
-- comment in 0001_init.sql — its writes must go through
-- worker.commentary.generate_audit_commentary so every row is masking-safe,
-- lineage-tracked, and filtered by the advice-shaped guardrail. Authenticated
-- (the API's RLS-scoped role) only ever reads.
grant select on public.audit_commentary to authenticated;
