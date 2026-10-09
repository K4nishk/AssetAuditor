---
tags: [decision-log, sovereign-ai, mvp1]
date: 2026-10-09
status: interview in progress — no code until the owner says PROCEED
---

# 2026-10-09 — Sovereign AI: premise check + owner interview

Source: the owner's "sovereign AI stack, using FinHive's learnings" brief (slice 1 of 11).
FinHive's decisions are treated as proposals; each one was checked against this codebase
before any question was put to the owner.

## Premise check

Verdicts: **holds** · **partly** · **false** (doesn't transfer) · **conflict** (contradicts a
committed plan or ADR, so the owner decides).

| # | FinHive premise | Evidence in AssetAuditor | Verdict |
|---|---|---|---|
| P1 | The agent loop and its tools run on a client that holds the data | No agent loop or tools exist. Every LLM call is a single shot from the worker: `worker/extract/llm_tier.py:256`, `worker/commentary.py:170`. No `openai`/`litellm` import in `app/`, `api/`, `frontend/src`. Data lives in Supabase + Vercel Blob; the worker (service-role, on owner hardware) is the only process that reads it and calls a model. | **false** — the worker plays the role FinHive gave the client |
| P2 | Inference already goes through an OpenAI-compatible client; switching is a `base_url` change | `openai.OpenAI(base_url=…)` at `worker/extract/llm_tier.py:189` and `worker/commentary.py:123`, pointed at LiteLLM. Host allowlist `{litellm, localhost, 127.0.0.1}` at `llm_tier.py:52`. ADR v1.1.0 §3: promoting vLLM is a YAML edit. | **holds**, but the switch point is `llm/litellm.config.yaml`, not the client |
| P3 | No third-party fallback | Groq is the *only* backend today (`llm/litellm.config.yaml:20-33`). ADR v1.1.0 Decision B makes Groq the permanent fallback behind vLLM. | **conflict** with ADR v1.1.0 |
| P4 | Sensitive fields are tokenised before egress | `worker/masking.py:66-74` masks account numbers, names, addresses, email, phone, SIN. Amounts, dates, merchant descriptions and institution names are not masked. Commentary sends total assets, liabilities, net worth and per-institution dollar totals (`app/domain/audit_commentary.py:103-128`) to Groq today. | **partly** — `Security-Model.md`'s "Groq receives masked text only" is true by the masking definition, but dollar figures leave owner hardware |
| P5 | Amounts can be replaced with codes | The extractor's whole job is to read amounts out of statement text (`amount`, `balance_after` in the schema at `llm_tier.py:59-118`). | **false** for extraction; possible for commentary |
| P6 | The client has no retry policy | Measured: openai SDK 3.6.0 defaults `max_retries=2`, read timeout 600 s (`openai._constants`); neither call site overrides them. LiteLLM sets `max_retries: 0`. | **partly** — silent SDK retries exist; no failure classes, no total cap, 10-minute read timeout |
| P7 | A UI waits on the model and needs Stop + a waiting status | No chat UI. LLM work is async through the `etl_jobs` queue; the API reports "queued — worker offline" (`app/routes/uploads.py:82,229`). Chat arrives with AA-45. | **false today** — slice 3 has nothing to attach to before AA-45 |
| P8 | Box: Windows, RTX 3090, ~8 GB VRAM free | `docs/mvp1/assumptions.md` A17: Windows, RTX 3060, 12 GB. | **conflict** — owner's call |
| P9 | Box runs Docker Compose; K8s manifests are CI-only artefacts | `worker/docker-compose.yml` already runs worker + LiteLLM + profile-gated `vllm` (no model, args or healthcheck yet). `build_plan.md` AA-42/AA-49 put single-node k3s on the Windows box; the M11 lab (AA-54/55) needs a real GPU cluster. | **conflict** with the MVP1 plan |
| P10 | A gateway must answer 503 while vLLM loads | No gateway. LiteLLM fronts providers; how it reports a loading vLLM upstream is unverified. | **gap** — LiteLLM vs a new gateway is a decision |
| P11 | Judge = Laya, ~2B, generative | huggingface.co is blocked from this sandbox (403 / DNS), as it was for FinHive. Web-search snippets only: a **421M ModernBERT-large, non-generative decision model**, 512-token context, Apache 2.0, answers typed choice / score / yes-no questions with calibrated probabilities. [REVIEW REQUIRED — model card not read] | **false** on size and kind — a classifier, not a generative judge; 512 tokens fits one observation, not a statement |
| P12 | The eval pipeline is new | `tests/evals/test_llm_golden_set.py` + `.github/workflows/llm-evals.yml` exist, run against Groq on GitHub-hosted runners, and have never passed (`docs/mvp1/todo_list.md` EVAL-1/2). | **partly** — exists and broken; GitHub runners cannot reach a LAN box |
| P13 | Data and its key live on the client; key backup escrowed to OpenBao | Encryption is server-side pgsodium with a project-wide key (`app/db/migrations/0004_account_number_vault_encryption.sql`). Box secrets (`LITELLM_MASTER_KEY`, `WORKER_DATABASE_URL`) live in git-ignored `worker/.env`. | **false** as stated; the box's secrets are the analogue |
| P14 | One command starts the app on demo data | `db/seeds/` is empty on this branch; demo mode is PR #34 (unmerged, AA-37b). | **gap**, already owned by AA-37b |
| P15 | The client is heavy | No desktop client exists; the frontend is a Vite web app. | n/a |

Key consequence of P1 + P3: the worker runs on the box, so when the box is off nothing is
processed with or without a fallback. A Groq fallback only matters while the worker is up and
vLLM is loading or out of memory.

## Interview

### Round 1 — answered 2026-10-09

| # | Question | Recommended | Owner's answer | Status |
|---|---|---|---|---|
| R1.1 | Which GPU box runs sovereign inference? | — (a fact, not a choice) | **RTX 3060 12 GB, ~8 GB VRAM free.** Corrects A17 (which implied the full 12 GB) and the FinHive brief (RTX 3090). | settled |
| R1.2 | May real user data go to Groq when vLLM is unavailable? | No fallback; Groq in CI only | **Revisit.** The owner likes having a fallback and accepts one if what is sent is masked enough that sensitive data does not leave. Also asks whether Laya would fit the golden eval sets better. | reopened → round 2 |
| R1.3 | How does the box run vLLM day to day? | Compose daily, k3s for labs | **Compose daily, k3s for labs.** `worker/docker-compose.yml` grows into the daily stack; single-node k3s in WSL2 comes up only for M9/M11 lab sessions; K8s manifests are also checked in CI. | settled |
| R1.4 | Where does the sovereign track go in MVP1? | Becomes M9, after AA-43 | **Becomes M9, after AA-43.** Sovereign slices replace the GPU parts of AA-42/AA-49/AA-50 under new IDs from AA-59; slices that do not need the wired pipeline may start earlier. | settled |

Consequences already fixed by round 1:
- With ~8 GB free on a 12 GB card, vLLM's `gpu_memory_utilization` cannot be FinHive's 0.3
  (0.3 × 12 GB = 3.6 GB holds no 7B model). The safe value and the model shortlist are being
  measured before round 2.
- Compose-daily means the FinHive slice-5 compose stack extends the existing
  `worker/docker-compose.yml` rather than adding a second one.
- Two runtimes share one GPU: a lab session stops the daily stack first.
