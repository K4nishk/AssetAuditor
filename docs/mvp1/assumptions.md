---
tags: [assumptions, mvp1, review-me]
created: 2026-10-04
---

# MVP1 Assumptions — ranked by criticality

Successor to `docs/vault/Assumptions.md` (A1–A16). Same scale: **Crit 5 = contesting this
reworks the core design; 1 = cosmetic.** Each row names its blast-radius. New assumptions are
numbered from **A17**. The second section lists existing A1–A16 that MVP1 **amends or
contests** — read those together with the originals.

## New assumptions (A17+)

| # | Assumption | Crit | Blast-radius if contested |
|---|-----------|------|---------------------------|
| A17 | **GPU is local-first, and resolved** (supersedes the earlier "need a free cloud GPU VM" framing). The AI tier is proven on the owner's own hardware at $0: the **Mac** (Metal, CPU k3d) for dev, the **Windows RTX 3060** (12 GB, single-node k3s + NVIDIA device plugin) for the real GPU POC — vLLM, DCGM, GPU passthrough, the isolation lab. **Modal** ($30/mo credits, owner-set **$7.5 cap**, serverless, pay-per-second) is burst only; **Oracle Cloud Always Free** (ARM, 24 GB RAM) is an optional 24/7 CPU host. No GPU workload runs on cloud until necessary. | 3 | If the 3060's 12 GB proves too small for even a quantized model, Modal burst (within the cap) covers it; neither the portfolio nor the tool is blocked. |
| A18 | **Local-first, near-zero spend** (amends A16). Own hardware is primary and free; the **only** money is Modal burst, hard-capped at **$7.5/mo**, pay-per-second, never always-on, and not run until necessary. Everything else stays on free tiers. | 4 | Relaxing the cap reopens a real budget line; tightening to literal $0 removes Modal burst, leaving the 3060 as the sole GPU ceiling. |
| A19 | **MVP1 is dual-purpose: a Senior AI Security Engineer portfolio AND a working personal finance tool used weekly.** The two security pillars are the headline *and* the finance features must actually work for the owner's own weekly use; a public blog is an end artifact. | 4 | If it were portfolio-only, finance correctness could be thinner; if tool-only, M10/M11 would shrink. Dual purpose means neither is sacrificed — both ship. |
| A20 | **The container-escape work is self-red-teaming of our own deployment**, to harden it — authorized isolation testing on infrastructure we own, documented as a reproducible lab + methodology, not a live campaign against third parties. | 4 | If it must become a broad or external security exercise, scope, authorization, and legal posture change entirely. |
| A21 | **GPU passthrough and strong sandboxing are in tension** (accepted). vLLM needs `/dev/nvidia*`, which sandboxed runtimes (gVisor/Kata) don't cleanly support; MVP1 documents the tradeoff rather than assuming a runtime that gives both. | 3 | If a sandbox-with-GPU path matures (or is required), M11's isolation design and the headline "gotcha" change. |
| A22 | **UI stays React + Chakra; no Streamlit** (amends the P4 prompt). The P1 chatbot and any chat UI live in the existing React app; P4's "Streamlit frontend" is replaced. Frontend + API stay on Vercel/Supabase free tiers; only the AI tier is on K8s (local-first, per A17). | 3 | If Streamlit (or K8s-hosted frontend) is wanted, a second UI stack and deploy target return. |
| A26 | **CodeRabbit is not used anywhere** (no review tier). The binding CodeRabbit protocol in `CLAUDE.md` and the AGENT_CONTRACT is removed in AA-40; reviews run via the built-in `/code-review` skill + an Opus-4.8 reasoning-tier pass (per `model-policy.md`). | 2 | If a review tier is later purchased, the gate can return; nothing depends on CodeRabbit to be correct. |
| A27 | **Development spans two owner machines** (amends A8's single-box assumption): the Mac (Metal LLM, CPU k3d, local runs with the Groq key in `ops/.env.local`) and the Windows RTX 3060 (local GPU k3s POC). The worker/queue/heartbeat design already tolerates an offline box. | 2 | If one machine is unavailable, the other still covers its half (app dev vs GPU POC); only the GPU-bound milestones wait on the 3060. |
| A23 | **Chatbot threat model = prompt-injection (incl. indirect via CSV cells) + cost/DoS + output-handling.** AuthZ/tenant isolation (RLS/JWT) already exists, so it is *verified*, not newly built. | 4 | If authz is treated as unbuilt, or if data-exfil-to-third-party-LLM is added as headline, M10 grows and the masking/DPA posture is revisited. |
| A24 | **Token-economy is a first-class engineering constraint.** The `caveman-micro` concise prompt, the `ponytail` decision-ladder, the `rtk` output-compression proxy, the FinHive model-tier table, and a workflow-only-for-fan-out rule are adopted to stop token/credit burn (the FinHive learning). | 2 | If dropped, build cost and over-engineering rise; no correctness impact. |
| A25 | **Vendored external skills are references, not drop-ins.** The `anthropic-cybersecurity-skills` (AI Security, Container Security) and the three token tools are adapted to AssetAuditor's hard rules before use, never imported blindly. | 2 | If imported verbatim, they may conflict with masking/provenance/zero-spend rules. |

## Existing A1–A16 that MVP1 amends or contests
*(Read against `docs/vault/Assumptions.md`.)*

- **A6 (LLM router on the home box)** → **amended by A17 + ADR v1.2.0**: LiteLLM + vLLM run on
  **local k3s (Mac dev / Windows RTX 3060 POC)**, with Modal burst as overflow — not a
  dedicated home-lab compose and not a cloud VM as primary. The router contract and
  Groq-fallback design are unchanged; only the host moves, and it stays local-first.
- **A8 (ETL on the owner's GPU box; jobs queue while off)** → **largely reaffirmed**: the
  worker still runs on owner hardware (now the Mac/Windows boxes, A27), and the
  *queue-while-offline* model is **kept** — it is what makes an intermittently-on local box,
  or a pay-per-second Modal burst, workable.
- **A16 (zero-cost contract)** → **amended by A18**: from "never any spend" to "local-first,
  $0 primary; the only money is Modal burst capped at $7.5/mo and not run until necessary".
  Recorded in **ADR v1.2.0** (AA-39).
- **A7 (free end-of-day prices)** → **reaffirmed**, with the correction that holdings must be
  valued at **market** (fetched prices are currently unread — fixed in AA-43), and the
  yfinance caveats in A17-adjacent notes (annual financials ~4y; TSX suffixes) apply to P2.
- **A4 / A13 (provenance + no-financial-values-to-analytics spine)** → **reaffirmed and
  extended**: masking-before-LLM is now also treated as a **security control** against
  prompt-injection/exfil (A23), not only a privacy control.

## Notes
- A17 + A18: the GPU question is now resolved local-first (Mac + RTX 3060, Modal burst capped
  at $7.5). The only setup gate is k3s + the NVIDIA device plugin on the Windows box (AA-42) —
  no provider shopping, no blocker.
- A19 is dual-purpose: the security pillars are the headline, but the finance tool must work
  for real weekly use. When they compete for time, prefer the change that serves both; cut
  showcase polish before cutting a feature the owner actually relies on.
