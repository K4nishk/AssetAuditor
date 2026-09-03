-- Migration 0003: holdings.mer_pct column (KCH-63 / AA-26)
--
-- AA-26's fee-drag comparison needs each holding's disclosed management
-- expense ratio. `worker/adapters/td.py` already parses `mer_pct` off the TD
-- mutual-fund fixture (statement-sourced, not a static assumption) but
-- `app/db/queries/silver.py`'s holding insert previously dropped it — this
-- column is additive-only (nullable, no backfill) so every other adapter
-- that never captures a MER keeps writing holdings unchanged.
alter table public.holdings
    add column mer_pct numeric(6, 4);

comment on column public.holdings.mer_pct is
    'Management expense ratio (percent, e.g. 2.18) disclosed on the source statement. Null when the adapter never captures one.';
