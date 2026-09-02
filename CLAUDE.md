# CLAUDE.md — AssetAuditor

Finances + Portfolio auditor for one Canadian user. Planning artifacts in this repo are the **source of truth** — read them before coding, don't re-derive.

## Non-negotiable priority order
data provenance > end-user satisfaction > code maintainability+quality > testing > documentation > delivery timelines.

## Read-first map (cheapest sufficient context)
- Building a feature → its issue in `mvp.md` (IDs `AA-n`) + the one vault note it links.
- Architecture question → `docs/adr/ADR_v1.1.0.md` (current: home-lab GPU-box worker + LiteLLM router; v1.0.0 superseded, v2.0.0 rejected — don't implement either).
- Domain math → `docs/vault/20-domain/Contribution-Rooms.md`; golden test numbers in `data/samples/README.md`.
- Parser/adapter work → `data/samples/` fixtures are the contract; `docs/vault/40-research/PDF-Statement-Parsing.md` for the tier strategy.
- Testing → `skills/e2e-testing/SKILL.md`.

## Hard rules
1. **Never bypass provenance**: every silver/gold write emits a lineage event; every dashboard number must drill down to sources.
2. **Masking before LLM, always.** No unmasked statement text in any prompt. No financial values in Amplitude events or metric labels.
3. **Raw SQL, parameterized only** — f-string SQL fails CI. Migrations are plain `.sql`, reviewed like code.
4. `Decimal` for all money/quantities. RLS on every user table. `user_id` comes from the JWT, never the request body.
5. LLM output is staged, never loaded without confirmation. Contribution-room math is deterministic Python — the model never computes it.
6. **Zero-cost contract (Assumption A16)**: never introduce a paid service or exceed a free tier; LLM calls go only through the LiteLLM endpoint (its rate caps are the cost guarantee). Anything that would cost money stops and asks the owner.

## Branching and pull requests

```
main                     production-deployment code
└── development          demoable MVP — the only merge target for feature work
    └── feature/<linear-issue>   one issue per branch, stacked, deleted after merge
```

- Branch name is the **Linear issue id, lowercased**: `feature/asa-14`. Never work directly on `development` or `main`.
- **Stacked PRs.** If the issue you depend on is still unmerged, your branch is cut from *its* branch and your PR targets *its* branch, so the diff stays incremental. GitHub retargets your PR to `development` automatically when the parent merges.
- **You never merge and never push to `development`.** Human review is the only merge gate; branch protection enforces it. Open the PR and stop.
- Commit messages are conventional and name the issue: `feat(ASA-14): kraken ledger adapter`.
- Upstream work you inherit is listed in the "Contracts from upstream agents" section of your prompt. Those interfaces are settled — import them, don't reimplement or refactor them.
- Publish your own interface to `CONTRACT_OUT.md` in the repo root (never commit it): module paths, key signatures, new tables/columns, under 20 lines. The next agent builds against it.

## CodeRabbit review protocol — binding

CodeRabbit reviews this repo through **three surfaces, and all of them must be accounted for**:

| Surface | When | Who answers it |
|---|---|---|
| **CLI gate** — `coderabbit review --agent --committed --base <base> -c CLAUDE.md` | before the PR opens, non-blocking | the implementing agent, then the mediator |
| **SaaS PR review** — comments the GitHub app posts on the PR | after the PR opens | `ops/remediate_prs.sh` |
| **Deferred review** — debt settled when quota returns | hourly, via launchd | `ops/review_sweeper.sh` |

### The prime directive
**The owner reviews outcomes, not conversations.** By the time a PR reaches them, every finding is either (a) fixed in the branch, or (b) explained in a PR comment stating the impact of not fixing it and linking the Linear issue that tracks it. Never leave a finding silently unanswered, and never ask for approval on a PR whose review comments nobody has answered.

### The free-tier rule: review is deferred, never skipped, and never blocks
This project runs CodeRabbit's **free tier on purpose** — 8 reviews per replenishing window, far fewer than the queue needs. When quota runs out the gate does not sleep or stall: it records the unfinished review in `ops/.review_debt.tsv`, marks the PR ⏳ `deferred`, and the queue moves on. Running out of review quota is an expected operating condition, not an error.

**Claude's quota and CodeRabbit's quota are independent, and the tooling keeps them that way.** If Claude is out of credit the sweeper still reviews: it publishes findings to the PR and the Linear issue, parks the branch as `reviewed_pending_fix` with the findings saved, and keeps reviewing the next PR. When credit returns it fixes from the stored findings without spending another review. A Claude outage delays fixes; it must never stop reviews.

Three debt states must never be conflated: `never_reviewed`, `unverified` (reviewed and fixed, but the confirming re-review never ran), and `reviewed_pending_fix` (review complete and saved, fixer blocked on Claude credit).

### Classify every finding by contract impact, not by severity
Severity says how bad the bug is. **Contract impact says who else breaks if you fix it** — and that decides who handles it. Your contract is what you published in `CONTRACT_OUT.md`: module paths, signatures, tables, columns, event shapes, routes.

**Tier 1 — no contract impact. Fix it yourself, now.** Internal to your own files; no downstream agent can observe it. Fix the *pattern*, not just the flagged line. Commit as `fix(<ISSUE>): address CodeRabbit — <what>`. No escalation, no ceremony.

**Tier 2 — low-to-medium contract impact. Fix it, then republish your contract.** The fix changes something you already published. Do the work, rewrite your `CONTRACT_OUT.md` block, **and** write `CONTRACT_DELTA.md` naming the old shape, the new shape, and which downstream issues consume it. A silent contract change is worse than the original finding, because it breaks work that already passed review.

**Tier 3 — high risk. Do not fix. Escalate with evidence.** Redesigns, cross-cutting refactors, ADR decisions, or anything touching provenance, masking, retention or auth. Write `ESCALATION.md` with what you implemented, the finding quoted, why you are not doing it, **the concrete risk of shipping without it**, and what a correct fix would involve. The orchestrator turns that into a Linear issue and posts the impact plus the link as a PR comment.

**Never** silence a finding (no disabled rules, `# noqa`, `eslint-disable`, widened types or exceptions), and never label something Tier 3 to avoid work. Escalating a three-line fix wastes the owner's attention, the scarcest resource here. Equally, never quietly "fix" something that needs a design decision.

### If you are the mediator or remediation agent
Read the actual code before judging — CodeRabbit can be wrong, and the implementing agent can be wrong about CodeRabbit being wrong. Prefer fixing over escalating; prefer escalating over pretending. An escalated Linear issue must stand alone: file paths, the quoted finding, and what "done" looks like.

### Shared worktree
The orchestrator, the sweeper and the build scheduler share one git worktree and all run `git reset --hard`. Every one of them takes `ops/.worktree.lock` first. Never add a background job that touches the worktree without taking that lock — and never remove the `ops/.*.lock` entries from `.gitignore`, because `git clean -fd` deletes untracked directories and a lock that isn't ignored deletes itself.

## Unattended overnight runs

`ops/orchestrator.sh` runs this loop in tmux against the Linear queue. If you are an agent inside it:
- **No credentials exist on that machine** — no Supabase project, no Vercel token, no Groq key, no GPU box, and no `curl`. Write the code, config, migrations and runbook; prove what you can locally against PostgreSQL and the fixtures; then **state plainly in your summary what is unverified**. Never fake a passing test and never invent a credential.
- **You have no network, and dependencies are pre-provisioned.** `uv sync` has already populated `.venv` from `pyproject.toml`/`uv.lock`, and `frontend/node_modules` is already installed. Use them — `uv run pytest`, `uv run ruff check .`, `npm run build` all work offline. **Do not try to install anything**: `uv add`, `pip install`, and `npm install` all need a network you don't have, and a failed install can leave the lockfile inconsistent for every agent after you. If your issue genuinely needs a package that isn't installed, do not work around it silently — implement what you can, say so explicitly in your summary, and name the missing package so the owner can add it between runs.
- Keep `ops/` state files out of commits (`.gitignore` already lists them). Removing those lines breaks the run's memory across issues.
- Scope discipline matters more than usual: nobody is watching, and an out-of-scope refactor lands in someone else's stacked PR.

## Between builds — settle the review debt (owner)

**Hand-run the sweeper after every build, before starting the next one:**

```bash
./ops/review_sweeper.sh
```

Do not rely on the hourly launchd sweeper. Under macOS TCC a LaunchAgent's child shell
cannot read `~/Documents`, so `com.assetauditor.review-sweeper` exits 1 having done
nothing — and says so only in `ops/logs/sweeper.err.log`, which nobody reads. It had
failed five consecutive times before anyone noticed. Until that is fixed, a hand-run is
the **only** thing that settles deferred CodeRabbit reviews.

It matters most exactly where it is easiest to skip. Roughly a third of the remaining
backlog — the LiteLLM tier, prices, Blob writes, metrics wiring, the security audits —
cannot be verified on this machine at all. Tests prove nothing there, so the CodeRabbit
review is the only quality signal those PRs will ever get.

**The one exception:** skip the sweep when CodeRabbit's own quota is exhausted. There
are no reviews left to spend, so it can only no-op; the debt stays banked and the next
sweep settles it. Check first:

```bash
./ops/review_sweeper.sh --status
```

Running it anyway is harmless rather than wrong — it exits cleanly on an empty ledger,
reuses saved findings without spending quota, and stops the moment CodeRabbit reports a
rate limit, leaving the debt intact.

## Interactive tmux sessions (owner, on a subscription budget)
- One session = one `AA-n`/Linear issue, or one tight cluster from the same milestone. Name it after the issue: `tmux new -s ASA-14`.
- Start prompts with the issue ID; let this file and the read-first map carry context instead of pasting docs.
- Prefer editing existing files over regenerating; don't reformat untouched code; keep diffs reviewable.
- Don't re-run the full test suite for a one-module change — run the module's tests, CI covers the rest.
- Stop and ask before: schema migrations beyond the issue's scope, new third-party services, anything touching masking/retention or the zero-cost contract.

## Verification bar for "done"
Issue's done-state met + unit tests for changed logic + relevant `skills/e2e-testing` flow boxes checkable + lint/type clean + CodeRabbit gate passed or its findings explicitly adjudicated. Fixtures changed? Update `data/samples/README.md` reference totals and the golden tests together, in the same commit.
