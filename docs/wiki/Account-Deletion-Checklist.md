# Account Hard-Purge Verification Checklist

Mirrors `skills/e2e-testing/SKILL.md` Flow 1.6, one level deeper — the exact
checks an operator runs after triggering `DELETE /api/account` (or after a
user reports a deletion) to confirm the purge actually reached every layer
Assumption A10 (`docs/vault/Assumptions.md`) and
`docs/vault/20-domain/Data-Retention-and-Privacy.md` promise: **DB rows,
blobs, and the Supabase Auth identity, all gone**, with lineage
**payloads** reduced to hashes rather than deleted outright.

Preconditions: the `user_id` (UUID) of the account that was deleted, and a
`service_role` Postgres/Supabase session (RLS blocks a normal session from
seeing a deleted user's rows anyway — that's expected, not a purge check).

## 1. Rows

Run against the Postgres instance directly (`service_role`, bypasses RLS):

```sql
select count(*) from public.accounts             where user_id = :user_id; -- 0
select count(*) from public.account_number_vault where user_id = :user_id; -- 0
select count(*) from public.holdings             where user_id = :user_id; -- 0
select count(*) from public.lots                 where user_id = :user_id; -- 0
select count(*) from public.transactions         where user_id = :user_id; -- 0
select count(*) from public.liabilities          where user_id = :user_id; -- 0
select count(*) from public.bronze_files         where user_id = :user_id; -- 0
select count(*) from public.etl_jobs             where user_id = :user_id; -- 0
select count(*) from public.staged_rows          where user_id = :user_id; -- 0
select count(*) from public.room_events          where user_id = :user_id; -- 0
select count(*) from public.networth_snapshots   where user_id = :user_id; -- 0
select count(*) from public.term_buckets         where user_id = :user_id; -- 0
select count(*) from public.diversification_cuts where user_id = :user_id; -- 0
select count(*) from public.users_profile        where id      = :user_id; -- 0
```

☐ Every count above is `0`.

## 2. Lineage — hashes, not silence

`lineage_events` rows are **not** deleted (`app/db/queries/account_lifecycle.py`'s
`redact_lineage_events`) — a fully-empty result here would actually mean
something went wrong (e.g. the FK cascade from a since-deleted `auth.users`
row swept them too), not that the purge succeeded.

```sql
select event_type, payload, facets
from public.lineage_events
where user_id = :user_id
order by occurred_at;
```

☐ At least one row exists (the `account_deletion` START/COMPLETE pair).
☐ Every row's `event_type = 'account_deletion'` (or `payload->>'redacted' = 'true'`)
  — no row still carries an unredacted `extraction_method`, `institution`,
  `sha256`, or error-message facet from before the purge.
☐ Redacted rows have exactly `{"redacted": true, "sha256": "<64 hex chars>"}`
  in both `payload` and `facets` — a stub, not the original content.

## 3. Blobs

No blob-store console access exists in an agent sandbox; run this from
wherever `BLOB_READ_WRITE_TOKEN` is available (the owner's machine or a
Vercel dashboard shell).

☐ Listing the Vercel Blob store with prefix `bronze/:user_id/` returns
  zero blobs.
☐ Listing with prefix `silver/:user_id/` returns zero blobs.
☐ Listing with prefix `gold/:user_id/` returns zero blobs.

If any of the three is non-empty, `app.uploads.blob.VercelBlobStorage.delete_prefix`
either wasn't reached (check `auth_admin`/blob errors in the request's server
logs — `app.account_purge.purge_account_external` raises rather than
swallowing a failed step) or the account had blobs under a pathname this
checklist doesn't yet know about — treat as a purge failure either way and
re-run the affected `delete_prefix` call manually, then re-check.

## 4. Supabase Auth identity

☐ `GET {SUPABASE_URL}/auth/v1/admin/users/:user_id` (service_role) returns
  404 / "user not found".
☐ Attempting to sign in as the deleted account's email fails.
☐ No row in Supabase's own `auth.users` table for `:user_id` (visible from
  the Supabase dashboard's Auth panel, or `select 1 from auth.users where id
  = :user_id` on a service_role connection — expect zero rows).

## 5. Timing

☐ All of the above completed within **30 days** of the `DELETE /account`
  request (Assumption A10). In practice this purge is synchronous — steps
  1–2 land inside the request's own DB transaction, steps 3–4 immediately
  after — so same-day completion is the expectation; a gap here means the
  request errored after the DB purge but before the blob/identity steps
  (see `app.account_purge.purge_account_external`'s docstring) and needs a
  manual re-run of just that half.
