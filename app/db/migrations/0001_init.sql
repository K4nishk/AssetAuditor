-- Migration 0001: core schema + RLS on every user table (KCH-37 / AA-2)
--
-- Tables per ADR v1.0.0 §7 (unchanged by v1.1.0) + worker_heartbeat (ADR v1.1.0 §2).
-- Every user-scoped table carries `user_id` (JWT-derived, never from the request body
-- per CLAUDE.md) and `deactivated_at` (freeze semantics — Data-Retention-and-Privacy.md);
-- RLS enforces tenant isolation on `user_id = auth.uid()`. `auth.users` / `auth.uid()`
-- are provided by Supabase; not defined here.
--
-- Reference (non-user) tables `prices` and `worker_heartbeat` also carry RLS — Supabase
-- exposes every public table via its auto-generated API by default, so an un-RLS'd table
-- is a live gap even when nothing in this app queries it that way — but their policies
-- are read-only for `authenticated`; writes happen through the worker's service_role,
-- which bypasses RLS entirely.
--
-- `authenticated` is a built-in Supabase role and is not created here. The API's
-- RLS-scoped pool (AA-6) connects as `authenticated` with the request's JWT claims set
-- per transaction so that `auth.uid()` resolves.
--
-- pgsodium column-level encryption for `account_number_vault.encrypted_account_number`
-- is AA-29's job (Security pass); this migration only enables the extension and reserves
-- the column.

create extension if not exists pgcrypto;
create extension if not exists pgsodium;

-- ---------------------------------------------------------------------------
-- set_updated_at — shared BEFORE UPDATE trigger for tables that track
-- updated_at; attached per-table below.
-- ---------------------------------------------------------------------------
create function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- users_profile — one row per Supabase Auth user; id IS the user id (no
-- separate user_id column needed).
-- ---------------------------------------------------------------------------
create table public.users_profile (
    id uuid primary key references auth.users (id) on delete cascade,
    age integer not null,
    holdings_country text not null,
    year_in_canada integer not null,
    fhsa_opened_year integer,
    risk_profile text not null
        check (risk_profile in ('very_risky', 'high', 'medium', 'low', 'no_risk')),
    prior_year_earned_income numeric(14, 2),
    deactivated_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create trigger users_profile_set_updated_at
    before update on public.users_profile
    for each row
    execute function public.set_updated_at();

alter table public.users_profile enable row level security;

create policy users_profile_tenant_isolation on public.users_profile
    for all
    using (id = auth.uid())
    with check (id = auth.uid());

grant select, insert, update, delete on public.users_profile to authenticated;

-- ---------------------------------------------------------------------------
-- bronze_files — raw uploads (14-day TTL via sweeper, AA-19).
-- ---------------------------------------------------------------------------
create table public.bronze_files (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    sha256 text not null,
    institution text,
    period text,
    blob_url text not null,
    purged_at timestamptz,
    deactivated_at timestamptz,
    created_at timestamptz not null default now(),
    unique (user_id, sha256)
);

create index bronze_files_user_id_idx on public.bronze_files (user_id);

alter table public.bronze_files enable row level security;

create policy bronze_files_tenant_isolation on public.bronze_files
    for all
    using (user_id = auth.uid())
    with check (user_id = auth.uid());

grant select, insert, update, delete on public.bronze_files to authenticated;

-- ---------------------------------------------------------------------------
-- etl_jobs — worker claims via FOR UPDATE SKIP LOCKED.
-- ---------------------------------------------------------------------------
create table public.etl_jobs (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    bronze_file_id uuid not null references public.bronze_files (id) on delete cascade,
    status text not null default 'pending'
        check (status in ('pending', 'claimed', 'parsing', 'needs_user', 'done', 'failed')),
    claimed_by text,
    claimed_at timestamptz,
    error text,
    deactivated_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (user_id, id)
);

create index etl_jobs_user_id_idx on public.etl_jobs (user_id);

create trigger etl_jobs_set_updated_at
    before update on public.etl_jobs
    for each row
    execute function public.set_updated_at();

alter table public.etl_jobs enable row level security;

create policy etl_jobs_tenant_isolation on public.etl_jobs
    for all
    using (user_id = auth.uid())
    with check (user_id = auth.uid());

grant select, insert, update, delete on public.etl_jobs to authenticated;

-- ---------------------------------------------------------------------------
-- staged_rows — parse-confirm screen source; entity/method vocab per
-- templates/backend/v1_fastapi_modular/README.md StagedRow sketch.
-- ---------------------------------------------------------------------------
create table public.staged_rows (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    job_id uuid not null,
    entity text not null
        check (entity in ('transaction', 'holding', 'lot', 'liability', 'account')),
    payload jsonb not null,
    confidence real,
    method text not null
        check (method in ('deterministic', 'llm', 'manual_entry', 'manual_correction')),
    confirmed_at timestamptz,
    deactivated_at timestamptz,
    created_at timestamptz not null default now(),
    foreign key (user_id, job_id) references public.etl_jobs (user_id, id) on delete cascade
);

create index staged_rows_user_id_idx on public.staged_rows (user_id);
create index staged_rows_job_id_idx on public.staged_rows (job_id);

alter table public.staged_rows enable row level security;

create policy staged_rows_tenant_isolation on public.staged_rows
    for all
    using (user_id = auth.uid())
    with check (user_id = auth.uid());

grant select, insert, update, delete on public.staged_rows to authenticated;

-- ---------------------------------------------------------------------------
-- accounts — silver refs; masked identifiers only (full account numbers live
-- only in account_number_vault, below).
-- ---------------------------------------------------------------------------
create table public.accounts (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    institution text not null,
    account_type text not null,
    masked_identifier text,
    currency text not null default 'CAD',
    deactivated_at timestamptz,
    created_at timestamptz not null default now(),
    unique (user_id, id)
);

create index accounts_user_id_idx on public.accounts (user_id);

alter table public.accounts enable row level security;

create policy accounts_tenant_isolation on public.accounts
    for all
    using (user_id = auth.uid())
    with check (user_id = auth.uid());

-- Silver writes are worker-only so they can be committed with their OpenLineage event.
grant select on public.accounts to authenticated;

-- ---------------------------------------------------------------------------
-- account_number_vault — pgsodium-encrypted mapping table for real account
-- numbers (ADR §7). AA-29 wires the actual column encryption; this migration
-- only reserves the column and enables the extension above.
-- ---------------------------------------------------------------------------
create table public.account_number_vault (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    account_id uuid not null,
    encrypted_account_number bytea not null,
    deactivated_at timestamptz,
    created_at timestamptz not null default now(),
    foreign key (user_id, account_id) references public.accounts (user_id, id) on delete cascade
);

create index account_number_vault_user_id_idx on public.account_number_vault (user_id);

alter table public.account_number_vault enable row level security;

create policy account_number_vault_tenant_isolation on public.account_number_vault
    for all
    using (user_id = auth.uid())
    with check (user_id = auth.uid());

grant select, insert, update, delete on public.account_number_vault to authenticated;

-- ---------------------------------------------------------------------------
-- holdings — positions (equities, ETFs, crypto, pension, ESOP).
-- ---------------------------------------------------------------------------
create table public.holdings (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    account_id uuid not null,
    ticker text not null,
    quantity numeric(20, 8) not null,
    avg_cost numeric(20, 8),
    currency text not null default 'CAD',
    deactivated_at timestamptz,
    created_at timestamptz not null default now(),
    unique (user_id, id),
    foreign key (user_id, account_id) references public.accounts (user_id, id) on delete cascade
);

create index holdings_user_id_idx on public.holdings (user_id);

alter table public.holdings enable row level security;

create policy holdings_tenant_isolation on public.holdings
    for all
    using (user_id = auth.uid())
    with check (user_id = auth.uid());

grant select on public.holdings to authenticated;

-- ---------------------------------------------------------------------------
-- lots — per-lot buys (Questrade) / vested-flag tranches (ESOP).
-- ---------------------------------------------------------------------------
create table public.lots (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    holding_id uuid not null,
    quantity numeric(20, 8) not null,
    unit_cost numeric(20, 8),
    currency text not null default 'CAD',
    acquired_at date,
    vested boolean,
    deactivated_at timestamptz,
    created_at timestamptz not null default now(),
    foreign key (user_id, holding_id) references public.holdings (user_id, id) on delete cascade
);

create index lots_user_id_idx on public.lots (user_id);

alter table public.lots enable row level security;

create policy lots_tenant_isolation on public.lots
    for all
    using (user_id = auth.uid())
    with check (user_id = auth.uid());

grant select on public.lots to authenticated;

-- ---------------------------------------------------------------------------
-- transactions — native currency + converted-CAD snapshot columns, per
-- CLAUDE.md money conventions.
-- ---------------------------------------------------------------------------
create table public.transactions (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    account_id uuid not null,
    holding_id uuid,
    occurred_at timestamptz not null,
    kind text not null,
    amount numeric(20, 8) not null,
    currency text not null,
    amount_cad numeric(20, 2),
    fx_rate numeric(18, 8),
    fx_date date,
    description text,
    deactivated_at timestamptz,
    created_at timestamptz not null default now(),
    foreign key (user_id, account_id) references public.accounts (user_id, id) on delete cascade,
    foreign key (user_id, holding_id) references public.holdings (user_id, id) on delete set null (holding_id)
);

create index transactions_user_id_idx on public.transactions (user_id);

alter table public.transactions enable row level security;

create policy transactions_tenant_isolation on public.transactions
    for all
    using (user_id = auth.uid())
    with check (user_id = auth.uid());

grant select on public.transactions to authenticated;

-- ---------------------------------------------------------------------------
-- liabilities — mortgages, lines of credit, loans.
-- ---------------------------------------------------------------------------
create table public.liabilities (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    account_id uuid,
    kind text not null,
    balance numeric(20, 2) not null,
    currency text not null default 'CAD',
    interest_rate numeric(6, 4),
    deactivated_at timestamptz,
    created_at timestamptz not null default now(),
    foreign key (user_id, account_id) references public.accounts (user_id, id) on delete set null (account_id)
);

create index liabilities_user_id_idx on public.liabilities (user_id);

alter table public.liabilities enable row level security;

create policy liabilities_tenant_isolation on public.liabilities
    for all
    using (user_id = auth.uid())
    with check (user_id = auth.uid());

grant select on public.liabilities to authenticated;

-- ---------------------------------------------------------------------------
-- room_events — TFSA/RRSP/FHSA ledger; vocab per Contribution-Rooms.md and
-- the RoomEvent pydantic sketch.
-- ---------------------------------------------------------------------------
create table public.room_events (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    account_type text not null check (account_type in ('tfsa', 'rrsp', 'fhsa')),
    year integer not null,
    kind text not null
        check (kind in ('grant', 'contribution', 'withdrawal', 'pension_adjustment', 'cra_override')),
    amount numeric(14, 2) not null,
    source_ref uuid,
    deactivated_at timestamptz,
    created_at timestamptz not null default now()
);

create index room_events_user_id_idx on public.room_events (user_id);

alter table public.room_events enable row level security;

create policy room_events_tenant_isolation on public.room_events
    for all
    using (user_id = auth.uid())
    with check (user_id = auth.uid());

grant select, insert, update, delete on public.room_events to authenticated;

-- ---------------------------------------------------------------------------
-- lineage_events — OpenLineage-shaped events; drill-down chain root.
-- ---------------------------------------------------------------------------
create table public.lineage_events (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    run_id uuid not null,
    job_id uuid,
    event_type text not null check (event_type in ('START', 'COMPLETE', 'FAIL')),
    facets jsonb not null default '{}'::jsonb,
    payload jsonb not null default '{}'::jsonb,
    deactivated_at timestamptz,
    occurred_at timestamptz not null default now(),
    foreign key (user_id, job_id) references public.etl_jobs (user_id, id) on delete set null (job_id)
);

create index lineage_events_user_id_idx on public.lineage_events (user_id);
create index lineage_events_run_id_idx on public.lineage_events (run_id);

alter table public.lineage_events enable row level security;

create policy lineage_events_tenant_isolation on public.lineage_events
    for all
    using (user_id = auth.uid())
    with check (user_id = auth.uid());

grant select, insert, update, delete on public.lineage_events to authenticated;

-- ---------------------------------------------------------------------------
-- networth_snapshots / term_buckets / diversification_cuts — gold, rebuildable.
-- run_id is not null so drill-down (pie slice -> gold row -> run_id -> inputs,
-- ADR v1.0.0 §7) never silently dead-ends; it isn't FK'd to a runs registry
-- because none exists yet — AA-13 (OpenLineage emitter) owns run_id semantics
-- and would design that table.
-- ---------------------------------------------------------------------------
create table public.networth_snapshots (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    snapshot_date date not null,
    total_assets_cad numeric(20, 2) not null,
    total_liabilities_cad numeric(20, 2) not null,
    net_worth_cad numeric(20, 2) not null,
    run_id uuid not null,
    deactivated_at timestamptz,
    created_at timestamptz not null default now(),
    unique (user_id, snapshot_date)
);

create index networth_snapshots_user_id_idx on public.networth_snapshots (user_id);

alter table public.networth_snapshots enable row level security;

create policy networth_snapshots_tenant_isolation on public.networth_snapshots
    for all
    using (user_id = auth.uid())
    with check (user_id = auth.uid());

-- Gold rebuilds are worker-only so their writes cannot bypass lineage emission.
grant select on public.networth_snapshots to authenticated;

create table public.term_buckets (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    snapshot_date date not null,
    bucket text not null,
    amount_cad numeric(20, 2) not null,
    run_id uuid not null,
    deactivated_at timestamptz,
    created_at timestamptz not null default now(),
    unique (user_id, snapshot_date, bucket)
);

create index term_buckets_user_id_idx on public.term_buckets (user_id);

alter table public.term_buckets enable row level security;

create policy term_buckets_tenant_isolation on public.term_buckets
    for all
    using (user_id = auth.uid())
    with check (user_id = auth.uid());

grant select on public.term_buckets to authenticated;

create table public.diversification_cuts (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    snapshot_date date not null,
    cut text not null,
    label text not null,
    amount_cad numeric(20, 2) not null,
    run_id uuid not null,
    deactivated_at timestamptz,
    created_at timestamptz not null default now(),
    unique (user_id, snapshot_date, cut, label)
);

create index diversification_cuts_user_id_idx on public.diversification_cuts (user_id);

alter table public.diversification_cuts enable row level security;

create policy diversification_cuts_tenant_isolation on public.diversification_cuts
    for all
    using (user_id = auth.uid())
    with check (user_id = auth.uid());

grant select on public.diversification_cuts to authenticated;

-- ---------------------------------------------------------------------------
-- prices — shared market data, not user-scoped. RLS stays on (Supabase
-- exposes every public table by default) but read-only for authenticated;
-- writes come from the price-refresh job's service_role only.
-- ---------------------------------------------------------------------------
create table public.prices (
    id uuid primary key default gen_random_uuid(),
    ticker text not null,
    date date not null,
    close numeric(20, 6) not null,
    source text not null,
    created_at timestamptz not null default now(),
    unique (ticker, date, source)
);

alter table public.prices enable row level security;

create policy prices_read on public.prices
    for select
    using (auth.uid() is not null);

grant select on public.prices to authenticated;

-- ---------------------------------------------------------------------------
-- worker_heartbeat — single-row liveness (ADR v1.1.0 §2); "queued — will
-- process when your worker is online" UX in AA-34 reads this.
-- ---------------------------------------------------------------------------
create table public.worker_heartbeat (
    id smallint primary key default 1 check (id = 1),
    last_beat_at timestamptz not null default now(),
    status text
);

alter table public.worker_heartbeat enable row level security;

create policy worker_heartbeat_read on public.worker_heartbeat
    for select
    using (auth.uid() is not null);

grant select on public.worker_heartbeat to authenticated;
