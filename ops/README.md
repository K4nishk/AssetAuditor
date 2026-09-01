# ops/ — overnight build orchestration

Unattended agents implement Linear issues in dependency order, every branch passes a
CodeRabbit gate before its PR opens, and **nothing merges without you**. You wake up to
a stack of reviewed-by-machine, unreviewed-by-human PRs in a defined merge order, plus
Linear issues for anything the agents could not service.

| File | Role |
|---|---|
| `bootstrap_repo.sh` | one-time: git repo, `main`/`development`, GitHub remote, branch protection |
| `seed_linear.py` | parses `mvp.md` → creates Linear issues (idempotent) → writes `queue.tsv` |
| `queue.template.tsv` | tier ordering derived from mvp.md's dependency spine |
| `orchestrator.sh` | the overnight loop: implement → CodeRabbit gate → mediate → PR |
| `logs/NIGHT_REPORT.md` | generated each run: what shipped, what escalated, merge order |

## First run

```bash
cd ~/Documents/Github/AssetAuditor
gh auth login                      # current token is invalid — this is required
coderabbit auth login              # the review gate depends on it
./ops/bootstrap_repo.sh

export LINEAR_API_KEY=lin_api_...  # the same key the Aegis project uses
export LINEAR_TEAM_KEY=ASA         # or whichever team you want AssetAuditor in
python3 ops/seed_linear.py         # dry run — check the parse before writing anything
python3 ops/seed_linear.py --apply # creates 32 issues, writes ops/queue.tsv
export KILL_GATE_ISSUE=ASA-15      # the id seed_linear.py prints for AA-15
```

## Every night

```bash
tmux new -s assetauditor
export LINEAR_API_KEY=... LINEAR_TEAM_KEY=ASA KILL_GATE_ISSUE=ASA-15
./ops/orchestrator.sh
# ctrl-b, d to detach.  tmux attach -t assetauditor to look in.
```

The loop is resumable: completed issues are recorded in `ops/.completed_issues` and
skipped on the next run, so a night that ends at tier 4 continues from tier 4.

Useful knobs (all optional): `IMPL_MODEL` / `MEDIATOR_MODEL` to pick models per role,
`MAX_TURNS`, `MAX_BUDGET_USD`, `CR_MAX_ROUNDS` (default 2), `CR_REQUIRED=1` to refuse
pushing when CodeRabbit cannot run, `SKIP_KILL_GATE=1` to continue past a failed gate.

## What happens per issue

1. **Branch** `feature/<linear-issue>` cut from the previous issue's branch if that is
   still unmerged (stacked), otherwise from `development`.
2. **Implement** — `claude -p` with an allowlisted toolset, the issue spec from Linear,
   and the contracts published by earlier agents. It commits; it never pushes.
3. **CodeRabbit gate** — `coderabbit review --agent --committed --base <base> -c CLAUDE.md`.
   The `-c CLAUDE.md` is what gives the review project-wide judgement: it flags in
   AssetAuditor's own terms (provenance, masking, `Decimal`, parameterized SQL, RLS).
   Critical/major findings go back to the implementing agent, up to `CR_MAX_ROUNDS`
   times, with an explicit instruction to fix the pattern rather than silence the rule.
   Where the agent believes a finding is wrong, it writes its reasoning to `CR_DISPUTE.md`
   instead of forcing a bad fix.
4. **Mediation** — anything still blocking goes to a second `claude` agent acting as
   mediator, which reads the code and rules on each finding: `fixed_now`, `escalate`,
   `false_positive`, or `accepted_risk`. Escalations become **new Linear issues** linked
   to the parent, for your morning triage.
5. **PR** — pushed, opened against its stacked base, with the CodeRabbit verdict in the
   body, a bottom-up merge-order comment, and CodeRabbit's own SaaS review pulled back
   into the Linear thread. Linear moves to **In Review**. Nothing merges.

## Morning review — PR approval order

**Approve and merge strictly bottom-up.** The stack is a chain: PR #1 targets
`development`, PR #2 targets PR #1's branch, and so on. Each PR's diff is only its own
work; GitHub retargets a child to `development` automatically as its parent merges, and
deletes the merged head branch. Merging out of order will conflict.

`ops/logs/NIGHT_REPORT.md` prints the exact order each morning, and the same list is
posted as a comment on every PR. For each PR, in order:

1. **Read the CodeRabbit review on the PR.** The PR body says what the gate did:
   `clean` (no findings), `resolved` (findings fixed before opening), `escalated`
   (findings that survived — read them first), or `unavailable` (⛔ treat the branch as
   entirely unreviewed by machine).
2. **Check escalated Linear issues** linked to the parent issue. Decide: fix now, accept
   into the backlog, or reject the finding. This is the one judgement the agents
   deliberately deferred to you.
3. **Sanity-check provenance and privacy claims yourself** — no lineage event skipped,
   no unmasked text reaching an LLM call, no unparameterized SQL, `Decimal` on money.
   These are the four things a review tool is least likely to catch in context.
4. **Approve** and **squash-merge** into `development`.
5. Confirm the head branch was deleted and the next PR retargeted, then move to the next.

Only promote `development` → `main` when you want a production deployment; that PR is a
normal human-authored one, reviewed the same way.

## When something goes wrong

| Symptom in the night report | What it means | Action |
|---|---|---|
| ❌ agent failed (exit N) | the implementing agent errored out | read `ops/logs/<ISSUE>_*.json`; issue was returned to Backlog |
| ❌ no commits produced | agent finished but shipped nothing | usually an under-specified issue — sharpen it in mvp.md and re-seed |
| ❌ wrong branch | agent committed off-branch; nothing pushed | work is local; inspect with `git log --all` |
| ⛔ CodeRabbit unavailable | the gate could not run | `coderabbit auth login`, then review that PR by hand |
| ⚠️ escalated | findings survived mediation | triage the linked Linear issues before approving |
| Loop stopped: rate-limited 3× | usage window keeps closing | resume later; `.completed_issues` protects finished work |
| **KILL GATE FAILED** | AA-15 could not parse the fixture PDF into the fixture rows | stop and reconsider the parsing tier before spending more budget |

The kill gate is deliberate: if a text-layer bank statement cannot be parsed
deterministically into the same rows as its CSV fixture, the "upload your statements"
premise is unproven, and every tier above it would be building on sand.
