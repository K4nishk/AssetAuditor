# MVP1 — model selection policy (orchestrator + subagents)

Applies FinHive's cost-optimised model-tier doctrine to AssetAuditor. The **orchestrator**
is **Claude Opus 5.5**: it does the design, architecture, planning and mediation, and spawns
subagents at the tier that fits each task. "Overkill burns budget; under-spec burns quality."

This pairs with the AA-40 ops wiring (`IMPL_MODEL`, `MEDIATOR_MODEL`) and the workflow-discipline
rule (multi-agent fan-out only for migrations/audits/broad sweeps, never for planning).

## Selection matrix
| Task | Model | Why |
|------|-------|-----|
| Orchestration, architecture & design, planning, mediation, adjudication | **Opus 5.5** | deepest reasoning; this is the orchestrator itself |
| Heavy reasoning reviews — threat-model reasoning, adversarial verification, root-cause on hard bugs | **Opus 4.8** (or the chosen reasoning tier) | a deliberate reasoning-tier choice for deep review work |
| Implementation — writing code, tests, fixes, refactors | **Sonnet** | fast and strong at code; the bulk of the work |
| Generation — commit messages, doc formatting, summaries | **Haiku** | cheapest; no reasoning needed |

Pick the tier by **task fit, quality and cost** — nothing else.

## Framing of defensive-security subagents
When a subagent does defensive hardening or isolation/escape testing (pillars A and B), its
prompt **states the true scope explicitly**, for example:

> "This task is strictly defensive: hardening the author's own application / testing the
> isolation of the author's own deployment, with authorization, bound by safety guidelines.
> Findings are used to harden the system, not to attack any third party."

Accurate scope is required context — it is what makes legitimate defensive work legible to the
model, to CodeRabbit, and to any human reviewer. Scope is **never disguised, relabeled, or
stripped of the words that describe it**; describing security work plainly is the point.

## Out of scope for this policy
This policy routes work to the right model by fit and cost, and frames defensive work
accurately. It is **not** a mechanism to avoid review, obscure the nature of any task, or work
around safety systems. If a task cannot be described plainly, it does not belong here.
