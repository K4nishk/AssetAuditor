---
tags: [research, llm]
verified: 2026-08-31
---

# LLM routing — why LiteLLM

The owner's requirement: *"attach a rate-limiting proxy to choose Groq or vLLM API endpoint"*, with an eval harness incoming and no tolerance for a design that forecloses self-hosting.

| Option | Nature | Verdict |
|---|---|---|
| **[LiteLLM proxy](https://docs.litellm.ai/docs/)** | Self-hosted OSS gateway: one OpenAI-compatible endpoint over 100+ providers; per-key/team rate limits & hard budgets; fallback chains with provider cooldown (quota-exhausted models cool down and traffic re-routes); YAML config; native vLLM + Groq support | **leverage** — it is literally the requested component. Runs beside the worker on the GPU box; config versioned in-repo |
| Vercel AI Gateway | Hosted gateway, provider failover + spend caps | **drop** — value was for Vercel-originated calls; all LLM callers now live on the worker. Second config surface with no remaining caller |
| OpenRouter | Hosted multi-provider marketplace | **skip** — third party in the data path, pay-per-token, can't route to on-box vLLM |
| Hand-rolled httpx wrapper | ~100 lines: try vLLM, catch, call Groq | **skip** — re-implements cooldown, rate caps, spend logs, and virtual keys that LiteLLM ships tested; violates "don't accept sub-par for complexity" in the other direction |

## How it lands in AssetAuditor ([[../30-architecture/LLM-Strategy]], [[../../adr/ADR_v1.1.0|ADR v1.1.0]] §3)
- Model group `extractor`: `vllm` primary (compose profile-gated until the GPU box lands) → `groq` fallback, RPM/TPM caps set *below* Groq's free tier so cost is structurally impossible.
- Worker code targets one endpoint and never learns which backend answered; the serving provider is recorded in lineage (`extraction_backend`) — provenance extends to model choice.
- Eval harness hits the same endpoint; Groq↔vLLM comparison = model-name switch; LiteLLM usage logs feed eval reports (mvp AA-33).

Sources: [LiteLLM docs](https://docs.litellm.ai/docs/) · [LiteLLM gateway with Groq/vLLM fallback walkthrough](https://stevescargall.com/blog/2026/04/run-free-llms-at-scale-litellm-gateway-with-groq-nvidia-nim-openrouter-and-local-vllm/) · [LLM gateway playbook](https://medium.com/@adnanmasood/using-litellm-as-an-open-source-llm-proxy-the-llm-gateway-playbook-part-2-c50166ac1446)
