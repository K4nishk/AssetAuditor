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
| A17 | **A free-tier GPU instance can be obtained.** MVP1's AI tier (vLLM, DCGM, the escape lab) assumes a root-access NVIDIA VM reachable at $0 out of pocket (provider trial/credits). Many free trials block GPU quota, so this is **unverified**. Until one exists, dev runs on the Mac (kind/k3d, CPU, tiny model) and the GPU-dependent work (AA-49, AA-50, gVisor+GPU in M11) is *written but unproven*. | 5 | If no free GPU is available: the AI-infra + GPU-security half of the portfolio becomes a documented design only, or the zero-spend rule (A16/A18) must relax to a small paid GPU budget. |
| A18 | **Free-credits-only spend rule** (amends A16). Only the GPU instance may ever consume money, and only from provider trial/credits — never a card, never always-on, hard monthly cap, auto-stop when idle. Everything else stays on free tiers. | 5 | Relaxing it reopens paid always-on compute and a real budget line; tightening it to literal $0 may foreclose the GPU tier entirely (see A17). |
| A19 | **MVP1's purpose is a Senior AI Security Engineer portfolio.** The finance features are the workload; the two security pillars (chatbot hardening, container-escape/isolation) are the headline, and a public blog is the end artifact. | 4 | If the purpose is instead "a working personal finance tool", sequencing flips — finance correctness and daily use outrank the security showcase, and M10/M11 shrink. |
| A20 | **The container-escape work is self-red-teaming of our own deployment**, to harden it — authorized isolation testing on infrastructure we own, documented as a reproducible lab + methodology, not a live campaign against third parties. | 4 | If it must become a broad or external security exercise, scope, authorization, and legal posture change entirely. |
| A21 | **GPU passthrough and strong sandboxing are in tension** (accepted). vLLM needs `/dev/nvidia*`, which sandboxed runtimes (gVisor/Kata) don't cleanly support; MVP1 documents the tradeoff rather than assuming a runtime that gives both. | 3 | If a sandbox-with-GPU path matures (or is required), M11's isolation design and the headline "gotcha" change. |
| A22 | **UI stays React + Chakra; no Streamlit** (amends the P4 prompt). The P1 chatbot and any chat UI live in the existing React app; P4's "Streamlit frontend" is replaced. Frontend + API stay on Vercel/Supabase free tiers; only the AI tier is on K8s. | 3 | If Streamlit (or K8s-hosted frontend) is wanted, a second UI stack and deploy target return. |
| A23 | **Chatbot threat model = prompt-injection (incl. indirect via CSV cells) + cost/DoS + output-handling.** AuthZ/tenant isolation (RLS/JWT) already exists, so it is *verified*, not newly built. | 4 | If authz is treated as unbuilt, or if data-exfil-to-third-party-LLM is added as headline, M10 grows and the masking/DPA posture is revisited. |
| A24 | **Token-economy is a first-class engineering constraint.** The `caveman-micro` concise prompt, the `ponytail` decision-ladder, the `rtk` output-compression proxy, the FinHive model-tier table, and a workflow-only-for-fan-out rule are adopted to stop token/credit burn (the FinHive learning). | 2 | If dropped, build cost and over-engineering rise; no correctness impact. |
| A25 | **Vendored external skills are references, not drop-ins.** The `anthropic-cybersecurity-skills` (AI Security, Container Security) and the three token tools are adapted to AssetAuditor's hard rules before use, never imported blindly. | 2 | If imported verbatim, they may conflict with masking/provenance/zero-spend rules. |

## Existing A1–A16 that MVP1 amends or contests
*(Read against `docs/vault/Assumptions.md`.)*

- **A6 (LLM router on the home box)** → **amended by A17/A22 + ADR v1.2.0**: LiteLLM + vLLM
  now run on a **cloud-GPU k3s** tier, not a home-lab compose. The router contract and
  Groq-fallback design are unchanged; only the host moves.
- **A8 (ETL on the owner's GPU box; jobs queue while off)** → **amended**: the worker moves to
  the same cloud-GPU K8s tier. The *queue-while-offline* model is **kept** — it's exactly what
  makes an on-demand (idle-stopped) GPU VM workable.
- **A16 (zero-cost contract)** → **amended by A18**: from "never any spend" to "only a
  trial/credit-funded GPU instance may spend, under a hard cap; everything else free". This is
  an ADR-level event, recorded in **ADR v1.2.0** (AA-39).
- **A7 (free end-of-day prices)** → **reaffirmed**, with the correction that holdings must be
  valued at **market** (fetched prices are currently unread — fixed in AA-43), and the
  yfinance caveats in A17-adjacent notes (annual financials ~4y; TSX suffixes) apply to P2.
- **A4 / A13 (provenance + no-financial-values-to-analytics spine)** → **reaffirmed and
  extended**: masking-before-LLM is now also treated as a **security control** against
  prompt-injection/exfil (A23), not only a privacy control.

## Notes
- A17 + A18 are the crux: the whole AI-infra/GPU-security showcase rests on a free GPU
  existing. Validate provider-by-provider before committing M9/M11 timelines.
- A19 sets the tie-breaker: when finance-correctness and security-showcase compete for time,
  the security pillars win in MVP1 — the inverse of a pure personal-tool build.
