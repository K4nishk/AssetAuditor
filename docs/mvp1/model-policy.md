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

## Fan-out contract (binding for any multi-agent workflow)

Learned the hard way on 2026-10-09/10: the sovereign-AI planning round spent about 4.6M
sub-agent tokens across 33 agents. That is roughly 4.5× FinHive's ~1M on ceremony, for
documents nobody could review (three specs of ~30k characters, ~117 open defaults, ~40
suggested PRs). The causes and the rule that prevents each:

1. **Fan out only for true fan-out.** Migrations, audits and broad sweeps qualify.
   Planning, interviews, spec writing and ticket breakdown do not: the orchestrator does
   those inline.
2. **Distil once, pass inline.** The orchestrator writes the facts once, in the decision
   log's Facts section (a few KB), and passes that text inside each agent's prompt. Agents
   never get raw research dumps; the 150 KB JSON caused most of the cold-start cost.
3. **Scope every read.** Each agent gets an explicit file allowlist (paths, ideally line
   ranges) and reads nothing else. "Read the repo" is not an instruction.
4. **Cap every output.** Each agent's schema states a maximum length, and a spec body
   stays under 12,000 characters. Long output is a design smell: it means decisions were
   left open.
5. **Verify decisions, not reports.** A skeptic runs only on a claim that would change a
   decision, one per claim. It is never paired with every step by default.
6. **State the budget first.** Before launch, the orchestrator states the token budget,
   and it records actual spend in the decision log afterwards. A run that would exceed the
   budget stops and asks the owner.
7. **Close defaults in the interview.** A spec may carry at most 5 "owner may overturn"
   defaults. Anything beyond that goes back to the interview as a question before any spec
   is written, so implementation PRs do not inherit undecided design as tech debt.

`.claude/workflows/scoped-fanout.js` enforces rules 2–4 and 6 mechanically. It lives in the
stacked tooling PR.

## Framing of defensive-security subagents
When a subagent does defensive hardening or isolation/escape testing (pillars A and B), its
prompt **states the true scope explicitly**, for example:

> "This task is strictly defensive: hardening the author's own application / testing the
> isolation of the author's own deployment, with authorization, bound by safety guidelines.
> Findings are used to harden the system, not to attack any third party."

Accurate scope is required context — it is what makes legitimate defensive work legible to the
model, to the built-in `/code-review`, and to any human reviewer. Scope is **never disguised, relabeled, or
stripped of the words that describe it**; describing security work plainly is the point.

## Out of scope for this policy
This policy routes work to the right model by fit and cost, and frames defensive work
accurately. It is **not** a mechanism to avoid review, obscure the nature of any task, or work
around safety systems. If a task cannot be described plainly, it does not belong here.
