-- Migration 0005: waiting jobs on etl_jobs (AA-63, #46; spec #40 decision 8)
--
-- A job no backend can serve right now goes back to 'pending' with a reason
-- and a not_before time instead of failing (owner decision R4.1). Waiting is
-- 'pending' plus a reason, so the status set, the etl_jobs_queued gauge and
-- the stale-while-queued alert are unchanged. Additive only: nullable
-- columns plus a zero default, no backfill. The table's existing tenant
-- isolation covers the new columns; nothing about access changes here.
alter table public.etl_jobs
    add column wait_reason text
        check (wait_reason in (
            'local_not_ready',
            'local_unavailable',
            'not_codable',
            'check_failed',
            'egress_unavailable',
            'coded_result_invalid'
        )),
    add column not_before timestamptz,
    add column defer_count integer not null default 0 check (defer_count >= 0),
    add constraint etl_jobs_wait_reason_only_when_pending
        check (wait_reason is null or status = 'pending');

comment on column public.etl_jobs.wait_reason is
    'Why a pending job is waiting (fixed codes, no values). Null when the job is not waiting.';
comment on column public.etl_jobs.not_before is
    'Earliest time the worker may claim this job again. Null means claimable now.';
comment on column public.etl_jobs.defer_count is
    'How many times this job has been deferred; drives the exponential backoff in worker/queue.py.';
