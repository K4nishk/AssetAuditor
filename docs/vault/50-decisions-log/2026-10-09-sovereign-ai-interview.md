---
tags: [decision-log, sovereign-ai, mvp1]
date: 2026-10-09
status: interview closed 2026-10-10 (rounds 1-2) — specs next; no code until the owner says PROCEED
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
| P8 | Box: Windows, RTX 3090, ~8 GB VRAM free | `docs/mvp1/assumptions.md` A17: Windows, RTX 3060, 12 GB. | **conflict** — resolved in R1.1: RTX 3060 12 GB with ~8 GB free |
| P9 | Box runs Docker Compose; K8s manifests are CI-only artefacts | `worker/docker-compose.yml` already runs worker + LiteLLM + profile-gated `vllm` (no model, args or healthcheck yet). `build_plan.md` AA-42/AA-49 put single-node k3s on the Windows box; the M11 lab (AA-54/55) needs a real GPU cluster. | **conflict** with the MVP1 plan |
| P10 | A gateway must answer 503 while vLLM loads | No gateway. LiteLLM fronts providers; how it reports a loading vLLM upstream is unverified. | **gap** — LiteLLM vs a new gateway is a decision |
| P11 | Judge = Laya, ~2B, generative | Read from the publisher's PyPI README (laya 0.4.1, 2026-10-08), GitHub README and BENCHMARKS.md via raw.githubusercontent.com; the Hugging Face card itself stays blocked. Apache-2.0. English checkpoint: 421M ModernBERT-large plus a custom scoring head, default 512-token window (raisable; `predict_long` windows long input); multilingual 322M, up to 8,192 tokens. **Non-generative**: returns probabilities for typed choice / score / yes-no questions. Served only by its own package or `laya-serve`; the custom head rules out vLLM and HF TEI. ~0.6 s per question on 4 CPU cores, ~2.3 GB RAM per checkpoint. Author's own numbers: near chance zero-shot on typed decisions, 0.86 on English NLI, over-confident until recalibrated, weak on negation. | **false** on size and kind — a classifier, not a generative judge |
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

### Facts gathered before round 2

Five research agents, each followed by an independent skeptic that re-ran the cited checks.
Nothing here ran on the box or called a model: every figure is read from source, measured
offline on the fixtures, or computed. Scratch scripts are not committed.

**Golden-set eval (skeptic: upheld, minor corrections).**
- The system under test is the LLM extraction tier against the LiteLLM `extractor` group.
- Scoring is fully deterministic: 6 exact checks per row, rows paired by position, and a single
  0.75 floor across all checks. Nothing in it is subjective, so a model judge adds no signal.
- The floor is loose. Measured offline by running the real test with fake model output:
  - every debit/credit flipped: passes at 83%;
  - every amount off by $1: passes at 83%;
  - one invented row appended: passes at 89%;
  - one row dropped: fails at 23%, because positional pairing shifts every later row.
- A comma amount such as `3,450.00` copied by the model raises an error, so the eval errors
  out instead of scoring that field as a miss.
- The `todo_list.md` claim that a null-everything model scores 79% does not reproduce.
- Audit commentary has no real-model eval. Every test fakes the LLM.
- In a hand-written probe, the advice regex missed 7 of 7 softly worded recommendations and
  flagged 2 of the factual sentences.

**Laya (skeptic: upheld).**
- It cannot replace Groq: it produces no rows.
- As an extraction scorer it is strictly worse than exact-match checks.
- Its best fit is a shadow guard. A second-signal classifier needs a labelled set and
  fine-tuning first.
- Run it on CPU. On the GPU it takes ~1.2 GiB, which would push the 7B baseline out.
- LiteLLM 1.104.2 has a `/v1/systemone` endpoint that may front `laya-serve`
  (unverified end to end).

**Egress coding (skeptic: upheld, with the corrections below).**
- On the sample statement, coding amounts and balances as `[A01]`-style codes round-trips
  16/16 values exactly. With local balance-chain checks, the decoded rows matched the
  deterministic parser.
- Without numbers the model can no longer check running balances. The worker must check:
  - each amount code is used once;
  - each balance equals the previous balance plus or minus the amount (this also decides
    debit/credit from row 2 on).
- Dates stay plain text.
- Coding payee names removes the cue that settles debit/credit in flat text for 2 of 8 rows.
- Wrapped description lines still leak payee names.
- Layout-mode text leaks the customer name and address: the labelled-PII regex in
  `worker/masking.py` is anchored at line start.
- A deny-by-default pre-send check blocked 13 of 13 leak cases. It also blocked benign digits
  (page numbers, years, times) until they were allowlisted.

**LiteLLM (skeptic: upheld).** Read from litellm 1.104.2 source. The skeptic matched image
digests: `main-stable` is currently the same image as `v1.104.2`, but the tag moves.
- The `rpm`/`tpm` values in `llm/litellm.config.yaml` act only as routing weights unless
  `enforce_model_rate_limits` is enabled, so the zero-cost caps are not enforced today.
- The proxy overwrites `response.model` with the requested alias, so the
  `extraction_backend` lineage facet would record `extractor`, not `vllm|groq`. The real
  deployment is in the `x-litellm-model-name` response header.
- A still-loading vLLM surfaces as HTTP 500, the same as a crashed one.
- `/health` makes real completion calls, so probing a group that contains Groq spends quota.
- Retries compound: the openai SDK's 2 retries multiply the router's 2.

**VRAM (skeptic: upheld, with the corrections below).**
- vLLM 0.31.0 takes `gpu_memory_utilization` as a fraction of total memory and aborts at
  start-up if free memory is below it.
- With ~8 GB free on a 12 GB card, about 0.55 is safe (hard ceiling ~0.62).
- The compose file's implicit 0.92 default cannot start.
- The sample statement is ~430 input tokens and ~930–1,290 output tokens; long statements
  need page chunking.
- No tool-calling exists in the code, so the spike measures schema-constrained extraction,
  not tool calls.
- Spike shortlist:
  - Qwen3-4B-Instruct-2507;
  - Qwen3.5-4B;
  - Qwen2.5-7B-Instruct-AWQ, as a quality baseline with no context headroom.
- Llama-3.1-8B-AWQ leaves too little KV cache to be useful.

### Skeptic re-checks (re-run 2026-10-10)

These corrections are carried into the specs:
- **Coding is new code.** `worker/masking.py` is irreversible, so coding is a new step on
  top of it, and masking still runs before every call.
- **Column cues need no masking change.** pdfplumber's extracted tables already keep
  separate withdrawn and deposited cells, so the debit/credit cue survives without layout
  mode.
- **The worker must set debit/credit itself.** It must apply the balance-chain-derived kind
  before building drafts; `_to_draft` does not do this today.
- **The pre-send check had holes.** It let through any code-shaped token (including unknown
  codes), any ISO-date-shaped string, and any account-mask slug. It must allow only codes
  in the current map, real calendar dates and the statement's own institution slug.
- **Harvested deny-terms over-block.** `Sample` from the fixture address matches the
  `SAMPLE FIXTURE` footer, so the coded extractor payload for this fixture is blocked.
- **Commentary percentages still reveal scale.** With one known anchor figure, each coded
  amount can be reconstructed to about ±$300 (±0.05 points of total assets).
- **vLLM corrections:**
  - online FP8 needs `--quantization fp8_per_tensor`; plain `fp8` fails at start-up;
  - Gemma-3-4B-it at 4-bit is a viable alternate candidate;
  - the 7B baseline is fragile if the card reports ~11.76 GiB total;
  - pin `--kv-cache-memory-bytes`, because desktop VRAM changes during start-up mis-size
    the KV cache;
  - the compose service needs `ipc: host` or `shm_size`.
- **LiteLLM corrections:**
  - error responses carry `x-litellm-model-name`;
  - LiteLLM can front a vLLM-served classifier via `/vllm/{endpoint}`, but Laya's custom
    head rules out vLLM, so its route is `laya-serve`, possibly via `/v1/systemone`.

### Round 2 — answered 2026-10-10

| # | Question | Recommended | Owner's answer | Status |
|---|---|---|---|---|
| R2.1 | How does the third-party fallback work, given it is acceptable only for masked data? (reopens R1.2) | Worker decides, codes egress | **Worker decides, codes egress.** Two LiteLLM groups: local (vLLM) and egress (Groq, caps enforced). The worker tries local first. On failure it codes amounts and payees, runs the no-plaintext check immediately before the send, and calls egress. Lineage records the backend actually used. Replaces ADR v1.1.0 Decision B's router-side fallback. | settled → ADR v1.2.0 |
| R2.2 | What role does Laya play? | Shadow guard on commentary | **Shadow guard on commentary.** `laya-serve` runs as a CPU service in the compose stack. Per commentary observation it answers "advice-shaped?" and "grounded in the facts?", and logs the verdict without changing the output. Its logs seed the labelled set. The golden eval stays deterministic and is tightened instead. | settled |
| R2.3 | Build FinHive's FastAPI gateway? | No; LiteLLM plus a readiness check | **No.** The worker checks vLLM readiness before choosing local; LiteLLM stays the only gateway (hard rule 6). Per-device keys only when a second client exists. | settled |
| R2.4 | Test seams | three seams | **Yes:** (1) the worker's LLM call functions with an injected client, plus a local stub HTTP server for real-client resilience; (2) the golden-set eval, in CI against Groq on fabricated fixtures and on the box on demand against vLLM; (3) static checks on compose and K8s files plus a GPU-less kind smoke test in CI. | settled |

### Not asked: defaults the specs assume (owner may overturn)

- **The Groq-backed CI eval stays.** It sends only fabricated fixtures, so it is not egress
  of real data.
- **Modal burst (A18) counts as egress.** It is third-party hardware, so the same coding
  rules apply.
- **Key escrow to OpenBao is deferred.** No client-held key exists (P13). Box secrets keep
  their current git-ignored `.env` home until a later round.
- **The Stop button and waiting status (FinHive slice 3) belong with the chatbot (AA-45).**
  There is no UI to attach them to before then.
- **The `worker/masking.py` layout-mode leak gets its own escalated issue.** It touches
  masking, so it is not folded into these specs.
- **Two pre-existing gaps are prerequisites inside the egress spec:**
  - unenforced LiteLLM caps;
  - the lineage facet recording an alias instead of the backend.
  The sovereignty claim depends on both.
