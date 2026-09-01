# CLARIFICATIONS — answer these before the build starts

Each question is plain-language, has a **default** (what I'll build if you just say "defaults"), and is ordered by design blast-radius — the top ones reshape the architecture if answered differently. Answer inline under each question, or just list numbers + answers.

---

## Big blast-radius (changes the architecture)

**Q1. May the ETL worker live off Vercel (one ~$5/month Fly.io/Railway container)?**
ADR v1 (recommended) needs it for Docling PDF parsing, long jobs, and clean Prometheus. ADR v2 shows the all-Vercel alternative and what it costs in extraction quality and latency.
**Default: yes — ADR v1.** I am not okay with paid options. The compute should be free, the single user can wait.

**Q2. Is Groq seeing *masked* statement text acceptable for MVP?**
Masking strips account numbers, names, addresses before any LLM call, but transaction descriptions and amounts do reach Groq. The alternative (self-hosted vLLM from day one) adds GPU cost/ops before the product exists.
**Default: yes, masked-text-to-Groq now; vLLM is a versioned later swap (ADR v1 §10).**

**Q3. "Deactivate their data" — confirm the semantics I assumed.**
Deactivate = freeze: data kept but excluded from everything, restorable. Delete account = hard purge of rows, files, and login within 30 days, with lineage keeping only content hashes (no reconstructable data). Is 30 days acceptable, and should *deactivate* also stop the 14-day bronze clock or let it keep purging? 
**Default: as stated; bronze keeps purging while deactivated.**

**Q4. Will this really stay single-user through MVP?**
I build multi-tenant-*correct* (RLS everywhere) but skip multi-user features (sharing, admin, email flows, billing). If you plan to onboard even 2–3 friends during MVP, say so now — it moves rate limiting, onboarding polish, and legal wording forward.
**Default: single user (you) through M5.**

**Q5. Free tiers only, or is there a monthly budget ceiling?**
Everything is designed to run on free tiers + the ~$5 worker (~$0–10/month total, plus Groq usage likely <$5/month at your volume). If your ceiling is $0, ADR v2 (no worker) becomes the pick despite its costs.
**Default: ≤$15/month all-in.** Free tier only. Absolutely no costs until 1 user MVP is not built.

## Medium blast-radius (changes a module)

**Q6. Where do market prices come from, and how fresh?**
Default: yfinance/OpenBB free data, refreshed daily by cron + a manual refresh button; prices are end-of-day, not realtime; every snapshot records price source and date. Fine? Or do you want a paid/realtime source?
**Default: daily EOD, free source.**

**Q7. Is confirm-before-load acceptable UX for every statement?**
Every parsed statement stops at a review screen before its numbers count (this is the provenance spine, Assumption A4). Zero-touch auto-load for institutions with proven deterministic parsers could be a later opt-in.
**Default: always confirm in MVP.**

**Q8. RRSP room needs an income figure statements don't contain.**
Options: (a) you type prior-year earned income, (b) you type the room figure straight from your CRA Notice of Assessment, (c) both, NOA wins conflicts.
**Default: (c).**

**Q9. Term buckets (short/medium/long): fixed liquidity rules, or per-account overrides?**
E.g., mark an ETF "this is my house down-payment fund → medium-term". Overrides add a small UI + schema field but make the pie honest.
**Default: fixed rules in MVP, override field in the schema from day one, UI in M3 if cheap.**

**Q10. Diversification "avenues" wording — observations only, or suggestions?**
"Tech is 48% of your equities" (observation) vs "consider adding international exposure" (suggestion-shaped). Suggestions get close to financial-advice territory even for a personal tool that you may blog about publicly.
**Default: observations + neutral "avenues" phrased as gaps ("no emerging-markets exposure detected"), never directives.**

**Q11. Home value and mortgage: include real estate in net worth?**
The Wealthsimple fixture implies a home. Default: you enter/update an estimated home value manually; net worth shows with-home and liquid-only views. Or exclude real estate entirely?
**Default: manual estimate, both views.**

**Q12. CPP: the requirements mention Canadian Pension Plan statements.**
CPP is an entitlement (future income stream), not an asset balance — mixing it into net worth is misleading. Options: (a) skip CPP in MVP, (b) show CPP contributions as an info card outside net worth, (c) model an estimated entitlement value.
**Default: (b).** CPP represents the RPP contributions -> They could be from an insurance company pointing to an ETF like Canada Life Index ETF Portfolio

## Small blast-radius (changes a screen or a config)

**Q13. Grafana Cloud free account under your email — OK to create during build?** (You asked "isn't Grafana hosting Prometheus?" — yes, that's exactly what we use: their hosted Prometheus-compatible backend, push-based.)
**Default: yes.**

**Q14. Which wireframe direction?** `v1_topnav` (three pies in a row, drill-down below — fully interactive mock) vs `v2_sidebar` (diversification promoted to hero chart, dedicated Lineage-explorer nav entry). Mixes welcome.
**Default: v1 layout + v2's "Lineage explorer" nav entry.**

**Q15. Blog/publishing constraint check:** the repo is public-showcase-bound. Rule I assumed: fixtures and screenshots only ever use mock-user data; your real statements never enter the repo, CI, or blog — real data exists only in your deployed Supabase/Blob. Confirm, and tell me if the repo should start private.
**Default: repo public from day one, mock data only; real data only in the deployed app.**

**Q16. Statement history depth:** do you want to backfill old statements (e.g., 2019→now) for a net-worth-over-time story, or start from the current month forward? Backfill affects how much the parse-confirm flow must batch.
**Default: current month forward; backfill supported but done gradually.**

---

## ✅ Resolved 2026-08-31 — no open questions remain

Owner answered inline (Q1, Q5, Q12) plus a follow-up round; everything below is now baked into the plan. The questions above are kept verbatim for the record.

| Q | Owner's answer | Where it landed |
|---|---|---|
| Q1 | **No paid compute — free only, "the single user can wait"** | ADR v1.1.0 §2: worker = docker-compose on the owner's GPU box, jobs queue while off; Fly.io dropped |
| Q2 | Default accepted, now stronger: LiteLLM routes to on-box vLLM as primary once live; Groq (masked text) is the rate-capped fallback | ADR v1.1.0 §3 · vault `LLM-Strategy` · mvp AA-16/33 |
| Q3 | Default (freeze / ≤30d purge; bronze TTL keeps running) | unchanged — ADR v1.0.0 §6 state machine |
| Q4 | Default (single user through M5) | Assumption A1 |
| Q5 | **Free tier only, absolutely no costs pre-MVP** | Assumption A16 (crit 5) + ADR v1.1.0 §4 cost table with per-component trip-wires |
| Q6–Q11 | Defaults accepted | as originally specified |
| Q12 | **"CPP" = the insurer-managed RPP contributions (e.g. Canada Life Index ETF Portfolio)** — folded into RPP handling, no separate CPP card, government CPP out of scope | vault `Institutions` · fixture `canadalife_rpp.json` |
| Q13–Q16 | Defaults accepted (Grafana Cloud free account OK; v1 layout + v2's Lineage-explorer nav; public repo, mock data only; current-month-forward) | as originally specified |

Follow-up decisions (same day): worker host = GPU box home-lab (GH Actions rejected as primary — PII on GH runners; Oracle Always Free rejected — tier halved + termination notices) · Vercel AI Gateway dropped in favour of self-hosted LiteLLM · ADR v2.0.0 rejected and archived.

**The build can start at `mvp.md` AA-1.** Current architecture of record: `docs/adr/ADR_v1.1.0.md`.
