---
tags: [decision-log]
date: 2026-08-31
---

# 2026-08-31 — CLARIFICATIONS round 1 resolved → ADR v1.1.0

Owner answered `CLARIFICATIONS.md` and a follow-up question round. Everything below is now reflected in [[../../adr/ADR_v1.1.0|ADR v1.1.0]], [[../Assumptions]] (A6/A8/A16), and `mvp.md` (AA-4/16/33/34).

1. **Zero cost, hard constraint** (Q1, Q5): no paid compute of any kind until the single-user MVP exists — "the single user can wait." Free tiers only: Vercel Hobby, Supabase free, Grafana Cloud free, Groq free tier, GitHub Actions public repo. → Assumption A16 (crit 5).
2. **Worker host = owner's GPU box** (follow-up decision): docker-compose (worker + LiteLLM + vLLM profile-gated), outbound-only polling, jobs queue while the box is off, heartbeat UX. Rejected: GitHub Actions primary (PII on GH runners), Oracle Always Free (tier halved + termination notices, verified 2026-08).
3. **LiteLLM replaces Vercel AI Gateway** (follow-up decision): single self-hosted router, vLLM primary once live, Groq rate-capped fallback; eval harness consumes the same endpoint. → [[../40-research/LLM-Routing-LiteLLM]].
4. **ADR v2.0.0 rejected & archived**: all-serverless is doubly wrong under zero-cost + incoming GPU; kept only for its chunked-ETL patterns.
5. **CPP = the Canada Life RPP** (Q12 + follow-up): the requirement's "CPP" means insurer-managed pension contributions (e.g. Canada Life Index ETF Portfolio); folded into RPP handling, no separate card, government CPP out of scope. → [[../20-domain/Institutions]].
6. All other CLARIFICATIONS defaults (Q3, Q4, Q6–Q11, Q13–Q16) accepted as written.

Planning phase is now **wrap-up ready**: no open questions remain.
