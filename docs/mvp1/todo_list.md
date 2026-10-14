# MVP1 — task list, priorities & estimates

A single scannable tracker for MVP1. Pairs with `build_plan.md` (full issue specs) and
`assumptions.md` (A17+). Update the **Status** column as work lands.

**Priority:** `P0` blocker / do-first · `P1` headline + core · `P2` important · `P3` later.
**Estimates** are in dev-days, AI-assisted solo: **Build** = write + test locally; **Integ** =
wire into the app / deploy / get CI green. Ranges, not commitments. **⛔** marks an external
blocker (an account, a credit-funded GPU, a secret) that must be cleared before the row can finish.
**Status:** `todo` · `in-progress` · `blocked` · `done`.

> Why the order looks like this: the app is **not wired end-to-end** and **nothing is
> deployed** yet (see `build_plan.md` "Starting reality"), so the P0 rows are foundation, not
> features. The two security pillars (M10, M11) are the portfolio headline but depend on the
> chatbot (M7) and the AI tier (M9) existing first.

## Now — unblock the trunk (everything inherits red CI until this lands)
| ID | Task | Pri | Build | Integ | Depends / ⛔ | Status |
|----|------|-----|-------|-------|-------------|--------|
| AA-37 | Land CI fix: correct the 2 overstated claims in PR #35's body, run `/code-review` (CodeRabbit not used), confirm the `USDCAD=X`=1.00 test fixture | P0 | 0.5 | 0.5 | — | todo |
| AA-37b | Add the 7-line `auth.uid()` stub to `tests/db/test_demo_seed_live.py` on `feature/kch-69` → unblocks PR #34 | P0 | 0.25 | 0.25 | AA-37 | todo |
| #36 | Rebase this MVP1-docs PR after #35 merges → green | P2 | — | 0.25 | AA-37 | todo |

## Golden-set LLM eval (separate track)
| ID | Task | Pri | Build | Integ | Depends / ⛔ | Status |
|----|------|-----|-------|-------|-------------|--------|
| EVAL-1 | Open a PR for branch `fix/llm-evals-diagnostics` (commit `3b06d29` already exists) → merge the self-explaining eval (names row+field, publishes score-vs-threshold to the step summary, captures litellm logs on failure) | P1 | 0.25 | 0.5 | AA-37 (green base) | todo |
| EVAL-2 | Read the now-visible score/field breakdown → fix the real cause (likely a formatting slip: statement's own `3,450.00` into a Decimal field, or `Jul 02` with year only in the header) **or** confirm it's a Groq/model infra issue (401/429/decommissioned id) | P1 | 0.5–1 | 0.5 | EVAL-1 · needs a run with the Groq key (CI secret, or locally via `ops/.env.local`) | todo |

> Eval status, verified: it has **never passed** (the old "green" runs *skipped* the step); there
> is **no regression to bisect**; the 75% floor is not the cause (a null-everything model still
> scores 79%). EVAL-1 is instrumentation that makes the failure legible; EVAL-2 is the actual fix.

## M6 — rails, doctrines, wiring
| ID | Task | Pri | Build | Integ | Depends / ⛔ | Status |
|----|------|-----|-------|-------|-------------|--------|
| AA-38 | Deploy rails live: Vercel (frontend + API) + Supabase free project (run migration 0001) + Blob; prove hello-world + a real heartbeat row | P0 | 1–2 | 1 | ⛔ accounts/secrets | todo |
| AA-43 | **Wire pipeline end-to-end**: worker runs adapters→mask→stage→silver; `rebuild_gold` on confirm; dashboard reachable from a real upload (no demo seed); holdings valued at market | P0 | 2–3 | 1 | AA-38 | todo |
| AA-39 | ADR v1.2.0 — local-first compute ladder (Mac → RTX 3060 k3s → Modal burst → Oracle CPU); amends A6/A8/A16 | P1 | 0.5 | — | — | todo |
| AA-40 | Port standards + token toolchain (caveman-micro prompt, `ponytail` skill, `rtk` proxy) + `/learn` `/skill-create` + vendor AI-Security & Container-Security skills | P1 | 1.5 | 0.5 | — | todo |
| AA-41 | Self-improving e2e-testing skill: persona-driven living suite + security persona + capture-after-release loop | P1 | 1.5 | 0.5 | AA-40 | todo |
| AA-42 | Local dev + GPU POC: Mac (LiteLLM→Ollama/mlx-lm, k3d CPU) + Windows RTX 3060 (single-node k3s + NVIDIA device plugin); Groq key in `ops/.env.local` | P1 | 1–2 | 0.5 | AA-40 | todo |

## M7 — P1 CSV chatbot + full holdings ingestion
| ID | Task | Pri | Build | Integ | Depends / ⛔ | Status |
|----|------|-----|-------|-------|-------------|--------|
| AA-44 | Generic CSV column-mapping ingestion (beyond the 6 adapters) → existing bronze→confirm→silver→gold | P1 | 2–3 | 1 | AA-43 | todo |
| AA-45 | React + Chakra chatbot UI: upload CSV, converse, render existing Recharts visuals; LLM = parser/explainer only | P1 | 3–5 | 1–2 | AA-44, AA-42 | todo |
| AA-46 | Asset-class coverage: mutual funds, GIC, HISA, cash | P2 | 1–2 | 0.5 | AA-44 | todo |

## M8 — P2 stock health + P3 overlap/diversification
| ID | Task | Pri | Build | Integ | Depends / ⛔ | Status |
|----|------|-----|-------|-------|-------------|--------|
| AA-47 | Stock health analyzer: deterministic metrics, observations-vs-thresholds (no "undervalued/overvalued"), yfinance caveats (~4yr financials, TSX suffixes) | P2 | 3–5 | 1 | AA-43 | todo |
| AA-48 | Overlap/diversification + ETF look-through from issuer holdings files; mutual-fund top-10 caveat; "data unavailable" shown honestly | P2 | 3–5 | 1 | AA-44 | todo |

## M9 — P4 AI tier on K8s (headline infra)
| ID | Task | Pri | Build | Integ | Depends / ⛔ | Status |
|----|------|-----|-------|-------|-------------|--------|
| AA-49 | **Superseded** by #39 / AA-60, AA-65, AA-66, AA-71, AA-73 (see `build_plan.md` M9) | — | — | — | — | superseded |
| AA-50 | **Superseded** by #39 / AA-72 (see `build_plan.md` M9) | — | — | — | — | superseded |

## M10 — Security pillar A: secure the chatbot (BUILD, headline)
| ID | Task | Pri | Build | Integ | Depends / ⛔ | Status |
|----|------|-----|-------|-------|-------------|--------|
| AA-51 | Prompt-injection defense (direct + indirect via CSV cells); masking + observations as security controls; allow-listed actions | P1 | 2–4 | 1 | AA-45 | todo |
| AA-52 | Cost / rate-limit / DoS: LiteLLM spend + RPM/TPM caps, upload size/row caps, token-flood guards, queue protection | P1 | 1–2 | 0.5 | AA-45, AA-49 | todo |
| AA-53 | Output handling: treat LLM output as untrusted in React (no HTML/markdown injection); verify RLS/JWT under manipulation | P1 | 1–2 | 0.5 | AA-45 | todo |

## M11 — Security pillar B: LLM container escape & isolation (DOCUMENT + lab, headline)
| ID | Task | Pri | Build | Integ | Depends / ⛔ | Status |
|----|------|-----|-------|-------|-------------|--------|
| AA-54 | Baseline hardening manifests + escape-attempt lab (non-root, ro-rootfs, drop caps, seccomp, restricted PSS, default-deny egress) | P1 | 2–3 | 1–2 | AA-49 (baseline on kind) | todo |
| AA-55 | Sandboxed runtime (gVisor/Kata) + GPU-passthrough-vs-sandbox writeup (the headline gotcha), shown on the RTX 3060 | P2 | 2–3 | — | AA-54 · local 3060 | todo |
| AA-56 | eBPF detection (Falco/Tetragon) wired into Prometheus/Grafana + `docs/mvp1/gotchas.md` | P2 | 2–3 | 1 | AA-54, AA-50 | todo |

## M12–M13 — quant + showcase
| ID | Task | Pri | Build | Integ | Depends / ⛔ | Status |
|----|------|-----|-------|-------|-------------|--------|
| AA-57 | Momentum backtest + scanner (12-1, 200-SMA regime, vol-parity, OHLC for ATR); output "highest 12-1 momentum", never "proposed investments" | P3 | 3–4 | 1 | AA-43 | todo |
| AA-58 | MDX blog: the security + AI-platform story, mock data only | P3 | 2–3 | 0.5 | M10, M11 | todo |

## Rollups
| Priority | Build + Integ (midpoints) |
|----------|---------------------------|
| P0 | ≈ 7–9 dev-days |
| P1 | ≈ 22–30 dev-days |
| P2 | ≈ 16–22 dev-days |
| P3 | ≈ 7–9 dev-days |
| **Total** | **≈ 52–70 dev-days** |

**Critical path:** AA-37 → AA-38 → AA-43 → (M7 ∥ M9) → M10 → M11.
**Clear these setup gates early** (they gate whole milestones):
1. **Local k3s + NVIDIA device plugin on the Windows RTX 3060** (AA-42) — this is the GPU POC node for M9 and the M11 lab; replaces the old "find a free cloud GPU" blocker (now resolved local-first, A17). Modal ($7.5 cap) is burst only, Oracle Always Free an optional CPU host.
2. **Vercel + Supabase accounts/secrets** (AA-38) — gate every deployed/wired row.
3. **The Groq key** (EVAL-2) — already in GitHub Actions secrets for CI, and in `ops/.env.local` for local runs; so the eval can be root-caused either way.

> Estimates assume the token-economy doctrine (AA-40) is in place early; without it, build cost
> runs higher. GPU-dependent rows (AA-49/50, AA-55) are proven on the local RTX 3060; the Mac
> (k3d, CPU) covers manifest/probe work until the 3060 node is up.
