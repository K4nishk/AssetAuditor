---
tags: [architecture, llm]
updated: 2026-08-31 (v1.1.0 — LiteLLM router, vLLM in-line)
---

# LLM Strategy

Doctrine: [[../10-mental-models/LLM-as-Parser-not-Oracle]]. Serving decision: [[../../adr/ADR_v1.1.0|ADR v1.1.0]] §3. Router survey: [[../40-research/LLM-Routing-LiteLLM]].

## Roles the LLM plays (MVP)
1. **Extraction fallback** — masked statement text → JSON-schema-constrained rows + per-field confidence → parse-confirm screen.
2. **Adapter bootstrapper** (dev-time) — given a new institution's masked sample, draft a deterministic adapter for human review.
3. **Audit commentary** — turns gold facts into plain-language observations, rendered with a generated-text disclosure.

## Roles it never plays
Arithmetic of record, contribution-room math, buy/sell advice, anything unstaged into the DB.

## Serving path (v1.1.0)
```
worker ──"extractor"──► LiteLLM proxy (localhost on GPU box)
                            ├─ primary: vLLM (own GPT-style model, on-box)   ← once GPU box lands
                            └─ fallback: Groq free tier (rate-capped)
```
- **LiteLLM** is the single gateway: OpenAI-compatible endpoint, fallback chain with provider cooldown, RPM/TPM hard caps sized *below* the Groq free tier (a runaway loop can queue, never bill). Config: `llm/litellm.config.yaml`, in-repo, reviewed like code.
- Until the GPU box arrives the config lists Groq only; promoting vLLM to primary is a YAML edit, zero code changes.
- The provider that served each extraction lands in the lineage facet `extraction_backend` — provenance extends into model choice.
- Vercel AI Gateway: **dropped** (all LLM callers live on the worker, next to LiteLLM).

## Eval harness integration
The owner's incoming eval harness points at the same LiteLLM endpoint: golden-set fixtures → expected rows, runnable against `groq` or `vllm` by model-name switch, with LiteLLM's usage/spend logs feeding comparison reports. CI job `llm-evals.yml` runs the golden set on prompt, config, or model change (mvp issue AA-33).

## Guardrails (unchanged)
- Masking before every call ([[../20-domain/Data-Retention-and-Privacy]]) — vLLM additionally keeps masked text on-box, the privacy end-state.
- JSON-schema/tool-call output, temperature 0, max-token caps.
- Cost watchdog metric `llm_tokens_total{backend}` ([[Observability]]).
