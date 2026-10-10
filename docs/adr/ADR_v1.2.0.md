# ADR v1.2.0 — Sovereign inference: local first, worker-decided coded egress

- **Status:** Accepted (owner, interview rounds 1–2). Amends [ADR v1.1.0](ADR_v1.1.0.md) and
  supersedes its Decision B routing (vLLM primary with a Groq fallback inside LiteLLM).
  Delta-style: anything not mentioned here is unchanged from v1.1.0.
- **Date:** 2026-10-10
- **Deciders:** owner + planning agent. Evidence and answers:
  [2026-10-09 sovereign-AI interview](../vault/50-decisions-log/2026-10-09-sovereign-ai-interview.md).

## 1. Context

The owner wants AI features whose sensitive data never leaves their control, running on
hardware they own. Two facts from the premise check drive this ADR:

- **Today's only model backend is Groq.** Commentary sends total assets, liabilities, net
  worth and per-institution dollar totals to it. Masking covers identifiers, not amounts.
- **The worker cannot treat the Groq-bound copy differently.** LiteLLM's fallback resends
  whatever the worker sent.

The owner still wants a fallback, provided whatever leaves the Box is masked enough that
sensitive values do not.

This ADR covers model calls only. Data at rest in Supabase and Vercel Blob is unchanged
from v1.1.0.

## 2. Decision A — The Box runs Compose daily; k3s only for labs

- **Daily stack:** the existing Docker Compose stack, on Docker Desktop with the WSL2
  backend and GPU passthrough.
- **Lab sessions:** single-node k3s in WSL2 runs only for M9/M11 lab work, and the daily
  stack stops first.
- **K8s manifests:** checked in CI with kubeconform and a GPU-less kind smoke test.
- **Hardware:** an RTX 3060 12 GB with about 8 GB free. vLLM therefore reserves roughly
  `gpu_memory_utilization` 0.55, about 5 GB of weights at most.
- **Model choice:** a spike scored on the Golden set picks the model, not a
  tool-calling benchmark. No tool-calling exists in the code.

## 3. Decision B — The worker chooses the backend; Egress is always coded

Each model use (`extractor`, `commentary`) gets two LiteLLM groups:

- `<use>-local`: the Local backend only, with no router-side retries;
- `<use>-egress`: the Egress backend, with its rate caps actually enforced.

The flow:

1. The worker uses the local group when vLLM reports ready.
2. When vLLM is unreachable, or stays busy past the retry budget, the worker may use the
   egress group, but only with a Coded payload:
   - Masking still runs first, on every call, local or egress;
   - amounts and payee names are replaced by codes (new reversible code);
   - the code map lives only in worker memory;
   - a no-plaintext check runs immediately before each egress send, and a failed check
     blocks the send. It admits only codes present in the current map, real calendar dates,
     the account-mask format of the statement's own institution, words from the
     owner-approved vocabulary, and the exact percentages the commentary renderer produced.
     Every other description word is Coded (R3.3).
3. Decoded output passes deterministic validation (for extraction, the running-balance
   chain) before it is staged.
4. Every call records the backend actually used and whether the payload was Coded. This is
   provenance, so it goes in the lineage event.

## 4. Decision C — LiteLLM stays the only gateway

- **Hard rule 6 stands.** No FinHive-style gateway is added.
- **Readiness comes from vLLM's own health endpoint.** The router's `/health` makes real
  completion calls, which spends quota when it probes an egress group.
- **Per-device keys wait.** They are added through LiteLLM only when a second client exists.

## 5. Decision D — Laya is a Shadow guard, never a scorer

- **Where it runs:** `laya-serve` as a CPU service in the Compose stack, so it never
  competes with vLLM for VRAM.
- **What it answers:** for each commentary Observation, "advice-shaped?" and "grounded in
  the facts?".
- **What it never does:** change the output. It only logs the verdict, and those logs seed
  the labelled set that any later enforcement needs.
- **Not a scorer:** the Golden-set scorer stays deterministic, because exact-match checks
  beat a probabilistic judge on structured fields.

## 6. Consequences

**Positive:**
- Real data leaves owner hardware only in Coded form.
- The Local backend keeps full-fidelity text.
- Lineage names the backend that actually served each call.

**Negative / accepted:**
- **The worker owns more logic.** It classifies failures and owns the retry budget; the
  openai SDK's silent retries are switched off.
- **Coded extraction may be less accurate.** The Golden set measures it in coded mode
  before Egress is enabled for extraction.
- **Dates and non-payee description text still leave in Egress.**
- **The pre-send check can refuse a statement.** It denies by default, and a refused job
  waits in the queue for the Local backend.

- **Commentary percentages still reveal scale.** With one known anchor figure (a property
  value, say), each coded amount can be reconstructed to within about ±$300.

**Rejected:**
- **Router fallback with coding on every call:** the Local backend would also lose the
  numbers.
- **Coding only the Groq-bound copy inside LiteLLM:** a deployment-level hook makes this
  possible, but the code map, prompt rewriting and decoding would then live in the proxy,
  outside the worker's tests.
- **No fallback:** the owner wants one.
- **A FinHive-style FastAPI gateway:** LiteLLM covers it.
- **Laya as the Golden-set judge:** strictly worse than exact match.

## 7. Follow-ups

- **ADR v1.3.0:** the remaining rungs of the AA-39 compute ladder (Modal burst, Oracle).
  Modal is third-party hardware, so it is treated as Egress.
- **Key escrow (OpenBao):** deferred. No client-held key exists.
- **Masking gap:** the layout-mode leak in `worker/masking.py` is escalated separately.
