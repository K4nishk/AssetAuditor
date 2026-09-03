---
name: e2e-testing
description: End-to-end / user-acceptance test flows for AssetAuditor. Run any flow against a deployed environment with the mock-user fixtures; extend by copying the flow template at the bottom.
---

# AssetAuditor E2E / UAT Skill

Conventions: run flows top-to-bottom against a fresh **staging** environment unless a flow says otherwise. Mock user "Alex Mock" facts and golden numbers come from `data/samples/README.md` — if a golden number changes there, it changes here. ☐ boxes are the owner's acceptance record. `<!-- TODO(owner) -->` marks extension points.

## Flow 1 — Account lifecycle
1. ☐ Signup with a fresh email → lands on onboarding; no dashboard access pre-profile.
2. ☐ Logout → protected routes redirect to login; JWT no longer accepted by API (curl check).
3. ☐ Login → session restored, profile intact.
4. ☐ **Deactivate** → dashboards/uploads hidden; direct API calls return "account deactivated"; DB rows still present with `deactivated_at` set; ETL skips this user.
5. ☐ Reactivate → everything back, numbers unchanged.
6. ☐ **Delete account** (requires re-auth) → within purge window: user rows gone, blob prefixes empty, Supabase Auth identity gone, lineage retains only hashes. Login now fails.
<!-- TODO(owner): add password-reset and session-expiry cases -->

## Flow 2 — Profile → contribution rooms (golden numbers)
1. ☐ Enter facts: age 29, CA, in Canada since 2019, FHSA opened 2024, income $82,000, risk medium.
2. ☐ Rooms screen shows **TFSA $41,200** (after Questrade TFSA contributions load in Flow 3), **RRSP new room $10,660** (after Canada Life PA loads), **FHSA $12,000**.
3. ☐ Each figure expands to its ledger: grants per year, contributions linked to source transactions.
4. ☐ Enter `cra_override` TFSA = $40,000 → room updates, delta explained in ledger.
5. ☐ Set country to non-CA → room widgets hidden, dashboards still render.

## Flow 3 — Upload → parse-confirm → silver/gold (per institution)
For each fixture in `data/samples/` (start: `scotiabank_sample_statement.pdf`, then all CSVs/JSONs):
1. ☐ Upload → status reaches `needs_user` or `done`; unknown-layout file routes to LLM tier and is flagged for review.
2. ☐ Parse-confirm shows rows with masked account (`…4821` — full number nowhere in the UI or API responses).
3. ☐ Edit one low-confidence row → correction stored as `manual_correction` in lineage facets.
4. ☐ Confirm-all → silver parquet exists in Blob; gold rebuilt; dashboard totals move to the reference totals in `data/samples/README.md` (net worth **$196,459** once all nine fixtures are in).
5. ☐ Re-upload same file → dedupe message, no duplicate rows.
6. ☐ Upload a password-protected and a scanned (no text layer) PDF → both end `needs_user` with actionable messages, never a silent failure.
<!-- TODO(owner): per-institution quirks to spot-check (Kraken decimals sum exactly; TD available credit NOT counted as asset; ESOP unvested excluded from liquid net worth) -->

## Flow 4 — Dashboards & drill-down (the responsive-dashboard requirement)
1. ☐ Hover any pie slice → highlight + tooltip with value and %.
2. ☐ Click a slice → drill-down panel lists underlying line items with provenance (run id, source file or purge tombstone, method, confirm timestamp) that justify the slice's number — sum of rows equals slice value.
3. ☐ Diversification cut switcher (class/geo/sector/currency) re-renders without reload; flags match risk profile (crypto >10% flagged for medium).
4. ☐ Dashboard is responsive: usable at 375px width (pies stack, drill-down becomes bottom sheet).
5. ☐ Audit-commentary card shows the generated-text disclosure.

## Flow 5 — Retention & privacy
1. ☐ Set a bronze file's upload date >14d back (test hook) → run sweeper → blob gone, tombstone lineage event exists, drill-down still renders with "source purged (hash 9f3c…)".
2. ☐ Logs older than 4 days absent from log store.
3. ☐ Grep staging logs + Amplitude debug stream for: full account numbers, amounts in analytics events → zero hits.
4. ☐ Sweeper stale >26h fires the Grafana alert.

## Flow 6 — Manual entry parity
1. ☐ Enter one holding by form (ticker/shares/avg-cost) → identical silver shape and dashboard treatment as parsed rows; lineage method = `manual_entry`.
2. ☐ Import a Yahoo Finance portfolio export with per-lot dates → lots preserved and visible in drill-down.

---

## Verification log — AA-29 security pass (2026-09-03)
No live staging environment or Supabase/Vercel credentials exist in the build
sandbox, so the boxes above are untouched — they're the owner's record of a
real staging run, and none happened here. What could be checked at the
code/test level for Flow 1 (deletion) and the masking-related items in Flows
3/5:
- Flow 1.4–1.6 (deactivate/reactivate/delete): `tests/api/test_profile_routes.py`
  + `tests/api/test_account_routes.py` exercise the HTTP layer against a fake
  connection; `tests/db/test_account_lifecycle_live.py` proves the FK-cascade
  purge + lineage redaction against a real (ephemeral) Postgres — skips in
  this sandbox (no live Postgres reachable), runs for real in CI.
- **Gap found, not fixed here**: Flow 1.4's "direct API calls return 'account
  deactivated'" does not hold. `users_profile.deactivated_at` is set correctly,
  but no route besides `/api/profile*`/`/api/account` checks it — a deactivated
  user's still-valid JWT can fully use uploads/staged/dashboard/rooms/etc. RLS
  isn't the right layer for this (it isn't a tenancy check), so fixing it means
  a deactivation check added consistently across every protected router — a
  cross-cutting change, not a one-line fix, so it's flagged here for a
  follow-up issue rather than bolted on inside this security pass.
- Flow 3.2 / Flow 5.3 (masked account numbers, no financial data in
  logs/Amplitude): `worker/masking.py` unit tests + all 6+1 adapter fixture
  tests (`tests/unit/test_adapters.py`) prove every adapter emits the
  canonical `{institution}-...{last4}` token, never a raw number, before a
  row is ever staged. `frontend/scripts/check-analytics-schema.mjs` (AA-28's
  CI gate) ran for real in this pass (`npm run test:analytics-events` /
  `npm run check:analytics-events` both pass) and confirms no declared event
  schema or call site carries an amount/account/ticker-shaped field. Log-side
  redaction (`app.obs.logging.RedactingFilter`) is new in this pass — see
  `tests/unit/test_obs_logging.py`.
- Not runnable here: `npm run build`/`npm run lint` still fail on the
  pre-existing missing `@supabase/supabase-js`/`eslint` install (AA-3/AA-6's
  known gap, unrelated to this issue — confirmed unchanged, not a regression
  from this pass).

## Flow template (copy to extend)
```markdown
## Flow N — <name>
Preconditions: <env, fixtures, state>
1. ☐ <action> → <observable expected result>
Failure notes: <what a failure here implies architecturally>
```
