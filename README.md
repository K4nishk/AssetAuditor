# AssetAuditor

A Finances + Portfolio auditor (FastAPI LLM-agent webapp, Canada-first): upload institution statements → masked, lineage-tracked ETL (bronze→silver→gold) → net-worth / term-bucket / diversification dashboards and TFSA·RRSP·FHSA contribution-room ledgers.

**Status: planning complete and clarifications resolved (2026-08-31) — build can start at `mvp.md` AA-1.** Runs at **$0/month**: free-tier cloud + the owner's GPU box for ETL and LLM inference (LiteLLM routing vLLM ↔ Groq).

## Reviewing the plan
| Artifact | Where |
|---|---|
| Clarifications (✅ resolved 2026-08-31) | [`CLARIFICATIONS.md`](CLARIFICATIONS.md) — answers + where each landed |
| Obsidian vault (mental models, domain, research, assumptions) | [`docs/vault/`](docs/vault/00-Home.md) — in Obsidian: *Open folder as vault* → `docs/vault` (or the repo root); start at `00-Home`, then `AssetAuditor-Map.canvas` |
| Architecture decision records | [`docs/adr/ADR_v1.1.0.md`](docs/adr/ADR_v1.1.0.md) (**current** — zero-cost home-lab + LiteLLM) · [`ADR_v1.0.0.md`](docs/adr/ADR_v1.0.0.md) (superseded) · [`ADR_v2.0.0.md`](docs/adr/ADR_v2.0.0.md) (rejected/archived) |
| Build plan (Linear-method issues) | [`mvp.md`](mvp.md) |
| UI wireframes (open in a browser) | `wireframes/frontend/v1_topnav/index.html` (interactive) · `v2_sidebar/` · `wireframes/backend/v1_etl_lineage/` |
| Styled design template | `templates/frontend/v1_chakra_light/index.html` (light/dark toggle) |
| Backend scaffold spec | `templates/backend/v1_fastapi_modular/README.md` |
| E2E / UAT skill | [`skills/e2e-testing/SKILL.md`](skills/e2e-testing/SKILL.md) |
| Mock fixtures + golden numbers | [`data/samples/README.md`](data/samples/README.md) |
| Agent instructions for build sessions | [`CLAUDE.md`](CLAUDE.md) — includes the branching model and the binding CodeRabbit protocol |
| Overnight build orchestration | [`ops/README.md`](ops/README.md) — bootstrap, Linear seeding, the nightly `tmux` run, and the morning PR approval order |

All financial data in this repo is fabricated (mock user "Alex Mock").
