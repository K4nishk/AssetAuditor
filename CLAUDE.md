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

Every branch is reviewed by CodeRabbit **before** its PR opens (`coderabbit review --agent --committed --base <base> -c CLAUDE.md`). The review carries project-wide context, so treat it as a senior reviewer who has read this whole repo, not a linter.

**When findings come back to you:**
1. Take them seriously. Fix the **underlying pattern**, not the one flagged line — if the same mistake appears in three files, fix all three.
2. **Never silence a finding**: no disabling lint rules, no `# noqa`/`eslint-disable`, no loosening a type or widening an exception to make it go away. That is a failed review, not a passed one.
3. Re-run tests and the linter after each fix round; both must pass.
4. Commit as `fix(<ISSUE>): address CodeRabbit round N`.
5. If a finding is genuinely wrong, out of scope for this issue, or would violate a hard rule above, **do not force a fix**. Write your reasoning for that specific finding into `CR_DISPUTE.md` (never commit it) and leave the code alone.

**Escalation.** After two fix rounds, a separate mediator agent rules on whatever still blocks: fix it, dismiss it as a false positive or accepted risk with a written rationale, or **escalate it as a new Linear issue** linked to the parent for the owner's morning triage. If you are the mediator: read the actual code before judging, be decisive, don't escalate a three-line fix and don't quietly "fix" something that needs a design decision.

## Unattended overnight runs

`ops/orchestrator.sh` runs this loop in tmux against the Linear queue. If you are an agent inside it:
- **No credentials exist on that machine** — no Supabase project, no Vercel token, no Groq key, no GPU box, and no `curl`. Write the code, config, migrations and runbook; prove what you can locally against PostgreSQL and the fixtures; then **state plainly in your summary what is unverified**. Never fake a passing test and never invent a credential.
- Keep `ops/` state files out of commits (`.gitignore` already lists them). Removing those lines breaks the run's memory across issues.
- Scope discipline matters more than usual: nobody is watching, and an out-of-scope refactor lands in someone else's stacked PR.

## Interactive tmux sessions (owner, on a subscription budget)
- One session = one `AA-n`/Linear issue, or one tight cluster from the same milestone. Name it after the issue: `tmux new -s ASA-14`.
- Start prompts with the issue ID; let this file and the read-first map carry context instead of pasting docs.
- Prefer editing existing files over regenerating; don't reformat untouched code; keep diffs reviewable.
- Don't re-run the full test suite for a one-module change — run the module's tests, CI covers the rest.
- Stop and ask before: schema migrations beyond the issue's scope, new third-party services, anything touching masking/retention or the zero-cost contract.

## Verification bar for "done"
Issue's done-state met + unit tests for changed logic + relevant `skills/e2e-testing` flow boxes checkable + lint/type clean + CodeRabbit gate passed or its findings explicitly adjudicated. Fixtures changed? Update `data/samples/README.md` reference totals and the golden tests together, in the same commit.
