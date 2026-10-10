# AssetAuditor MVP1 — build plan

> Successor to `mvp.md` (AA-1…AA-36, "MVP"). MVP1 has a **dual purpose**: a
> **Senior AI Security Engineer portfolio project** *and* a **working personal finance tool
> the owner uses every week**. The security pillars are the headline; the finance features
> are both the workload that justifies the platform and a real tool that must work. Written
> issues-not-stories (per `mvp.md`): each issue is a concrete task with a done-state; the
> *why* lives in the milestone header. Issue IDs continue from **AA-37** and are stable.
>
> **Priority order is unchanged** (CLAUDE.md): data provenance > end-user satisfaction >
> maintainability > testing > documentation > delivery timelines. Every hard rule still
> binds: masking-before-LLM, LLM-as-parser-not-oracle, deterministic money math,
> observations-not-advice, Decimal, RLS, raw parameterized SQL, **local-first / near-zero
> spend** (own hardware is primary; capped cloud burst only), and **public repo /
> mock-data-only** (real holdings never enter the repo, CI, or blog).
>
> **Code review:** CodeRabbit is not used (no review tier). Reviews run via the built-in
> `/code-review` skill locally and a dedicated reasoning-tier pass (Opus 4.8) per
> `model-policy.md` — not a SaaS gate.

## Headline for MVP1
Two security pillars are the deliverable the portfolio is built around:
- **(A) Secure the chatbot** (M10) — the new CSV-upload + chat surface ingests untrusted
  input and routes masked content to an LLM. Build real defenses.
- **(B) LLM container escape & isolation testing** (M11) — red-team *our own* K8s LLM
  deployment to prove a compromised model pod cannot break its container boundary into the
  host, then harden until it can't. Documented as a reproducible lab + methodology +
  interview "gotchas" (see `docs/mvp1/gotchas.md`, produced in M11).

## Starting reality (as of 2026-10, verified)
`mvp.md`'s MVP is **code-complete in pieces but unproven as a product**: nothing external
has ever been deployed (Vercel, Supabase, Blob, the worker, vLLM, Grafana, Amplitude are
all cold), and the pipeline is not wired end to end (`worker/main.py` logs
`"extraction not yet implemented"`; nothing calls `rebuild_gold` except the demo seed; the
dashboard is only reachable via the demo seed; holdings are valued at cost because fetched
prices are never read). **MVP1 therefore begins with wiring and deployment, not features.**

---

## M6 — Trunk, rails, doctrines, wiring
*Why: nothing is deployed and the core pipeline is broken; fix the foundation before features.*

- **AA-37 Land the CI fix + unblock the demo-mode PR** — merge the green CI-repair branch
  (`fix/ci-development-green` / PR #35) after its three cleanups: correct the two overstated
  claims in its description (CI *was* green 5× on 2026-09-02 before the audit gates; the
  golden-set eval has *never* actually run — the "green" runs skipped it), run the built-in
  `/code-review` (CodeRabbit is not used), and confirm the identity `USDCAD=X`=1.00
  test fixture is acceptable vs a realistic 1.35. Then add the 7-line `auth.uid()` stub to
  `tests/db/test_demo_seed_live.py` on `feature/kch-69` so PR #34 (demo mode) goes green.
  done: `development` CI green; #35 and #34 merged. deps: none.
- **AA-38 Deploy rails actually live** — provision the Vercel project (frontend + `api/index.py`),
  a free Supabase project (run migration 0001), and Vercel Blob; prove a hello-world API
  response and a worker heartbeat row written from a real run. done: the three components
  respond in a deployed environment, not just CI. deps: AA-37.
- **AA-39 ADR v1.3.0 — local-first compute ladder** (renumbered: ADR v1.2.0 is now the sovereign-inference ADR; this one covers only the remaining Modal/Oracle rungs, both treated as Egress) — evolves ADR v1.1.0 (home-lab) for the
  owner's real hardware; **no cloud GPU is the primary**. Frontend + API stay on the Vercel +
  Supabase **free tiers** (always-up). The AI tier (vLLM + LiteLLM + worker + DCGM +
  Prometheus/Grafana) is **proven locally first**, then burst to cloud only when a local box
  can't hold it. The ladder, cheapest-first:
  1. **Mac (dev)** — Metal LLM via Ollama/mlx-lm; kind/k3d (CPU) for K8s manifest work.
  2. **Windows + RTX 3060 (local GPU POC)** — single-node k3s with the NVIDIA device plugin:
     real vLLM, real DCGM metrics, real GPU passthrough → this is where M9 and the M11
     isolation lab are **proven, at $0**.
  3. **Modal ($30/mo free credits, owner-set $7.5 cap)** — serverless burst to a bigger GPU
     (T4/A10) only when the 3060's 12 GB is too small. **Do not run GPU workloads until
     necessary.**
  4. **Oracle Cloud Always Free (ARM, 24 GB RAM)** — optional 24/7 CPU-mode host for a
     quantized model or the always-on worker.
  Jobs queue while the local box is off (existing heartbeat/queued UX). Amends A6, A8, A16
  (see `assumptions.md`). done: ADR committed; v1.1.0 marked superseded. deps: none.
- **AA-40 Port standards + token-economy toolchain + security skills** —
  (a) into `CLAUDE.md`: FinHive's cost-optimised **model-tier table** (Opus=reasoning,
  Sonnet=implementation, Haiku=generation; `IMPL_MODEL`→Sonnet, `MEDIATOR_MODEL`→Opus),
  the **stage-gate STOP-and-approve** rule, and a **workflow-discipline** rule (multi-agent
  workflows only for true fan-out — migrations, audits, broad sweeps — never for
  planning/interviews);
  (b) the **token-economy toolchain**: the `caveman-micro` concise-output prompt in
  `CLAUDE.md`, the `ponytail` "does-this-need-to-exist" decision-ladder vendored as
  `.claude/skills/ponytail/SKILL.md`, and the `rtk` (Rust Token Killer) output-compression
  proxy installed in the dev image + `ops/`;
  (c) the `/learn` and `/skill-create` commands;
  (d) **vendor** the `AI Security` and `Container Security` skill sets from
  `anthropic-cybersecurity-skills` into `.claude/skills/` (adapted to our rules, not imported
  blindly) for pillars A and B;
  (e) **remove the CodeRabbit gate** from `CLAUDE.md` and the AGENT_CONTRACT (no review tier);
  replace it with the built-in `/code-review` skill + an Opus-4.8 reasoning-tier review.
  done: CLAUDE.md updated; skills present and invocable; `rtk` runs in the dev env. deps: AA-37.
- **AA-41 Self-improving e2e-testing skill** — upgrade `skills/e2e-testing/SKILL.md` from a
  checklist to the FinHive persona-driven **living regression suite** ("become the test
  user"; *a finding closes when a permanent test covers it, not when the bug is fixed*). Add
  a **security persona** (prompt-injection, escape attempts, abuse/DoS) and the
  capture-after-release loop so a validated test run becomes a permanent workflow. done: a
  persona run produces findings that convert to committed regression tests. deps: AA-40.
- **AA-42 Local dev + GPU-POC loop** — (Mac) LiteLLM routes to a Metal-native
  OpenAI-compatible server (Ollama or mlx-lm) for app LLM work; K8s manifests/probes/metrics
  drafted on kind/k3d (CPU). (Windows + RTX 3060) stand up single-node k3s with the NVIDIA
  device plugin as the **local GPU POC node** — the target for M9 and the M11 lab. The Groq
  key for local runs lives in `ops/.env.local` (gitignored; never committed). done: `litellm`
  → local model answers on the Mac; the 3060 k3s node schedules a GPU pod and exposes
  `nvidia.com/gpu`. deps: AA-40.
- **AA-43 Wire the pipeline end to end** — `worker/main.py` job loop runs
  adapters → mask → stage → silver; `rebuild_gold(user_id)` is invoked on confirm; the
  dashboard is reachable from a real upload without the demo seed; holdings valued at market
  (read the fetched prices). done: upload a fixture → confirm → dashboard renders market
  values with lineage drill-down. deps: AA-38.

## M7 — P1: CSV chatbot (React) + full holdings ingestion
*Why: the owner's actual use case; the chatbot is the attack surface M10 secures.*

- **AA-44 Generic CSV column-mapping ingestion** — a mapping layer above the 6 existing
  institution adapters so an arbitrary holdings CSV maps into the silver schema via the
  existing bronze → stage → confirm → silver → gold path (masking + lineage unchanged).
  done: a non-institution CSV ingests through the confirm screen. deps: AA-43.
- **AA-45 React + Chakra chatbot UI** — upload a `.csv`, converse, and render the existing
  Recharts portfolio visuals; the LLM is used only as a parser/explainer (observations, not
  advice), never to compute numbers of record. done: upload → chat → visuals, on mock data.
  deps: AA-44, AA-42.
- **AA-46 Asset-class coverage** — mutual funds, GIC, HISA, and cash as first-class holding
  types through to the dashboard (gap today). done: each type ingests and shows in net worth
  + term buckets. deps: AA-44.

## M8 — P2: stock health analyzer + P3: overlap/diversification
*Why: finance depth, deterministic and honest about data gaps.*

- **AA-47 Stock health analyzer** — ticker → fundamentals + 5y chart + per-statement metrics
  + an overall health rating. **Deterministic** rating; P/E and valuation shown as
  **observations against thresholds** ("P/E 32, above the 25 band"), never "undervalued/
  overvalued". yfinance caveats surfaced (annual financials ~4y not 5; TSX `.TO`/currency).
  done: a ticker renders the dashboard with observation-worded flags. deps: AA-43.
- **AA-48 Overlap & diversification analyzer** — ETF look-through from **issuer holdings
  files** (iShares/Vanguard/BMO CSVs), overlap across funds + direct holdings, sector &
  geography exposure. Mutual-fund holdings are top-10/semi-annual only — show
  "data unavailable", never invented. done: sample portfolio shows overlap + exposures with
  explicit data-coverage notes. deps: AA-44.

## M9 — P4: sovereign AI tier (Local inference, Coded Egress, evals)
*Why: the platform the security pillars act on, rebuilt by the sovereign-AI interview
(`docs/vault/50-decisions-log/2026-10-09-sovereign-ai-interview.md`, ADR v1.2.0). Compose runs
daily on the Box (RTX 3060, ~8 GB free); k3s only for M9/M11 labs.*

Specs (GitHub issues, `ready-for-agent`): **#39** Local inference on the Box · **#40** Coded
Egress fallback · **#41** Deterministic evals + Laya Shadow guard. Tickets are their sub-issues:

| ID | Issue | Ticket | Blocked by |
|---|---|---|---|
| AA-59 | #42 | Harden the Golden-set scorer | — |
| AA-60 | #43 | vLLM on the Box: pinned stack, local groups, preflight | — |
| AA-61 | #44 | Lineage records the backend that actually served | — |
| AA-62 | #45 | Failure classes and retry budget | — |
| AA-63 | #46 | Waiting jobs: migration, exponential backoff, visible reason | — |
| AA-64 | #47 | Coding and pre-send check | — |
| AA-65 | #48 | On-demand Golden-set eval on the Box | #42, #43 |
| AA-66 | #57 | Model spike + config PR (human) | #48 |
| AA-67 | #49 | Readiness probe, backend choice, early wake | #43, #45, #46 |
| AA-68 | #50 | Commentary eval: grounding + advice set | #42 |
| AA-69 | #55 | Coded commentary Egress, enforced caps (ships off) | #49, #47, #50 |
| AA-70 | #58 | Coded extraction + chain validator + Coded CI eval | #42, #55 |
| AA-71 | #51 | Bounded requests and page chunking | #43 |
| AA-72 | #52 | DCGM metrics + Grafana allowlist | #43 |
| AA-73 | #53 | K8s lab manifests + kubeconform + kind smoke | #43 |
| AA-74 | #56 | Laya Shadow guard (CPU; needs Box RAM check) | #43, #50 |

These replace the GPU parts of AA-42, all of AA-49 and AA-50, and AA-33. Ticket branches are
`feature/aa-NN`, stacked on their blocker's branch (or on the top of the docs stack).

## M10 — Security pillar A: secure the chatbot (BUILD)
*Why: headline; the LLM ingests untrusted CSV + chat. Uses the vendored AI Security skills (AA-40).*

- **AA-51 Prompt-injection defense** — direct and **indirect injection via CSV cell
  contents**; enforce masking + observations-only as security controls; allow-list any
  tool/action the chat can trigger. done: a documented injection corpus is blocked (tests in
  the e2e security persona). deps: AA-45.
- **AA-52 Cost / rate-limit / DoS** — LiteLLM spend caps + RPM/TPM, upload size/row caps,
  token-flood guards, queue protection so a prompt loop can't exhaust the Modal credit cap or
  wedge the local queue. done: abuse cases hit limits, not the wallet. deps: AA-45, AA-49.
- **AA-53 Output handling** — treat LLM output as untrusted in React (no HTML/markdown
  injection, no code execution, safe chart rendering); verify RLS/JWT tenant isolation holds
  even under manipulation. done: XSS/markdown-injection attempts render inert; cross-user
  reach stays impossible. deps: AA-45.

## M11 — Security pillar B: LLM container escape & isolation (DOCUMENT + lab)
*Why: the senior AI-security story; red-team our own pod. Uses the vendored Container Security skills (AA-40).*

- **AA-54 Baseline hardening + escape-attempt lab** — non-root UID, read-only rootfs, drop
  ALL caps, seccomp `RuntimeDefault`, restricted Pod Security Standards, default-deny egress
  NetworkPolicy; a reproducible lab that attempts classic escapes (privileged, `hostPath`,
  `docker.sock`, `CAP_SYS_ADMIN`, writable `/proc`, host namespaces) and shows them blocked.
  done: lab runbook + results committed. deps: AA-49.
- **AA-55 Sandboxed runtime** — run the LLM pod under gVisor or Kata as a second kernel
  boundary; write up the **GPU-passthrough-vs-sandbox tension** (sandboxed runtimes don't
  cleanly support `/dev/nvidia*`) — the central AI-security tradeoff, shown concretely on the
  RTX 3060 (a consumer GeForce, which also makes the limited-DCGM-metrics point real). done:
  comparison documented with evidence. deps: AA-54.
- **AA-56 eBPF runtime detection** — Falco or Tetragon detecting syscall/escape anomalies,
  wired into the Prometheus/Grafana stack from AA-50; the "gotchas" write-up
  (`docs/mvp1/gotchas.md`) for interview prep + the blog. done: an injected escape attempt
  fires a detection alert. deps: AA-54, AA-50.

## M12 — P5: momentum backtest + market scanner
*Why: quant workload; observations-only.*

- **AA-57 Momentum engine + scanner** — 12-1 momentum, 200-SMA regime filter, volatility
  parity; use **OHLC** for ATR/Chandelier (resolves the prompt's "adjusted close only"
  conflict); CAD-investor caveats (SPY/FX, XIC benchmark, T-bill proxy); output labelled
  **"highest 12-1 momentum"**, never "proposed investments". Metrics: CAGR, max drawdown,
  Sharpe vs buy-and-hold. done: backtest + scanner run on a fixture universe with
  observation-worded output. deps: AA-43.

## M13 — Blog + showcase
*Why: the portfolio artifact.*

- **AA-58 MDX blog** — the security + AI-platform story (both pillars, the gotchas, the
  architecture), **mock data only**, rendered in-app. done: blog page renders from repo MDX.
  deps: M10, M11.

---

## Out of MVP1
AA-30 (per-user derived encryption keys), realtime prices, OCR of scanned statements,
multi-user, and AA-33 (superseded — vLLM now lands on the local RTX 3060 node per ADR v1.2.0,
not a dedicated home-lab box). Cloud GPU (Modal/Oracle) is burst/overflow only, reached after
the local POC, never a prerequisite.

## Dependency spine
AA-37 → AA-38 → AA-39/40/41/42 → **AA-43** → (M7 ∥ M9) → M8 → **M10 → M11** → M12 → M13.
Security pillars (M10, M11) depend on the chatbot (M7) and the AI tier (M9) existing.

## Open inputs
- **Redacted holdings list** (optional) — sharpens AA-46/AA-48 data-source scoping.
- **GPU is resolved (local-first):** the RTX 3060 box proves M9 + the M11 lab at $0; Modal
  ($7.5 cap) is burst; Oracle Always Free is an optional 24/7 CPU host. No longer a blocker —
  see A17 in `assumptions.md`. The only setup gate is standing up k3s + the NVIDIA device
  plugin on the Windows box (AA-42).
