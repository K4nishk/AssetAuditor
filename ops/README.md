# ops/ — autonomous build & review orchestration

Unattended agents implement Linear issues in dependency order and open stacked PRs.
CodeRabbit reviews every branch. **Nothing merges without you.** Two launchd loops keep
it running without supervision; you review PRs at your own pace.

| File | Role |
|---|---|
| `bootstrap_repo.sh` | one-time: git repo, `main`/`development`, GitHub remote, branch protection |
| `seed_linear.py` | parses `mvp.md` → creates Linear issues (idempotent) → writes `queue.tsv` |
| `validate_queue.py` | checks `queue.template.tsv` against mvp.md's declared dependencies |
| `queue.template.tsv` | tier ordering derived from the dependency spine |
| `orchestrator.sh` | the build loop: implement → CodeRabbit gate → mediate → PR. Also the shared library |
| `run_builder.sh` | launchd entry point for autonomous development (guards + starts the orchestrator) |
| `install_builder.sh` | installs/removes the 2-hourly build agent |
| `review_sweeper.sh` | settles deferred CodeRabbit reviews from the debt ledger |
| `install_sweeper.sh` | installs/removes the hourly review agent |
| `remediate_prs.sh` | answers CodeRabbit's **PR comments** on open PRs, bottom-up, in place |

State (all gitignored, all local): `.completed_issues` · `.review_debt.tsv` ·
`.agent_contracts.md` · `.issue_map.tsv` · `queue.tsv` · `.env.local` · `logs/`

## Rules learned the hard way — do not violate

1. **Never launch with uncommitted changes in `ops/`.** The first `git reset --hard`
   silently restores the last *committed* toolchain and deletes untracked scripts, so
   every safety property added since vanishes without a word. This destroyed a full
   session's work on 2026-09-01 and caused the KCH-51 failure.
2. **Lock directories must stay in `.gitignore`.** `git clean -fd` removes untracked
   *directories*, and `run_issue()` runs it two lines after taking `ops/.worktree.lock`
   — an unignored lock deletes itself and mutual exclusion silently stops working.
3. **Every worktree toucher takes `ops/.worktree.lock`.** Orchestrator, sweeper and
   builder share one checkout. Two `git checkout`s at once move the branch under a
   running agent.
4. Both `orchestrator.sh` and `review_sweeper.sh` **re-exec from a copy in `$TMPDIR`**,
   because they rewrite the worktree they live in. State stays in the repo via
   `AA_REAL_OPS`. (This is also what let the scripts be recovered after they were
   deleted mid-run — the relocated copy survived.)

## First run

```bash
gh auth login                      # needs scopes: repo, workflow
coderabbit auth login
./ops/bootstrap_repo.sh
export LINEAR_API_KEY=lin_api_...  # LINEAR_TEAM_KEY is the issue-id PREFIX (KCH), not the team name
export LINEAR_TEAM_KEY=KCH
python3 ops/seed_linear.py         # dry run first
python3 ops/seed_linear.py --apply # creates issues, writes ops/queue.tsv, prints the kill-gate id
```

Persist credentials in `ops/.env.local` (gitignored; both launchd agents source it):

```bash
export LINEAR_API_KEY="lin_api_..."
export LINEAR_TEAM_KEY=KCH
export KILL_GATE_ISSUE=KCH-50
```

## Autonomous mode (recommended)

```bash
./ops/install_sweeper.sh    # reviews: hourly at :07
./ops/install_builder.sh    # builds:  every 2 hours at :22
```

Both fire on wake and coalesce slots missed while asleep. `run_builder.sh` skips if a
build is running, an issue is in flight, or the queue is done, and auto-detects the top
of the stack so `SEED_LAST_BRANCH` never needs setting by hand. Keep the Mac plugged in;
sleep delays work rather than losing it.

Manual run of either loop:

```bash
caffeinate -ims ./ops/run_builder.sh
./ops/review_sweeper.sh            # --status prints the debt ledger
```

Knobs: `MAX_BUDGET_USD` (per issue; builder defaults 12.00), `MAX_TURNS`, `IMPL_MODEL`,
`MEDIATOR_MODEL`, `CR_MAX_ROUNDS` (2), `CR_RL_MAX_WAITS` (0 — never wait on CodeRabbit),
`SKIP_KILL_GATE=1`, `LOCAL_CHECKS`.

## The three review surfaces

Passing one does not answer the others. See CLAUDE.md for the binding protocol.

1. **CLI gate** (`orchestrator.sh`, pre-push) — blocking findings return to the agent up
   to `CR_MAX_ROUNDS` times, then a mediator fixes, dismisses with rationale, or
   escalates to Linear.
2. **SaaS PR review** (`remediate_prs.sh`) — answers comments the GitHub app posts after
   a PR opens. Pushes to the **same branch**, so the stack never deepens.
3. **Deferred review** (`review_sweeper.sh`) — CodeRabbit's free tier allows 8 reviews
   per replenishing window. When it runs dry the build does **not** wait: the debt is
   banked in `.review_debt.tsv` and settled hourly.

**Claude and CodeRabbit quotas are independent.** Claude out of credit → the sweeper
still reviews, publishes findings to the PR and Linear, and parks the branch as
`reviewed_pending_fix` with findings saved (a later sweep fixes without re-spending a
review). CodeRabbit out of quota → the build carries on regardless.

## Morning review — PR approval order

**Approve and squash-merge strictly bottom-up.** The stack is a chain: the first PR
targets `development`, the next targets that PR's branch, and so on. GitHub retargets a
child to `development` as its parent merges and deletes the merged branch. Merging out
of order will conflict. `logs/NIGHT_REPORT.md` prints the exact order, and the same list
is posted on every PR.

Per PR: read the CodeRabbit verdict in the body → check any escalated Linear issues →
sanity-check the four things a tool misses (a skipped lineage event, unmasked text
reaching an LLM, unparameterized SQL, floats on money) → approve → merge.

## Status markers

| Marker | Meaning | Action |
|---|---|---|
| ✅ clean / resolved | reviewed, no blocking findings outstanding | normal review |
| 📝 advisory | findings reported, none blocking | read them before approving |
| 🔁 unverified | reviewed and fixed, confirming re-review never ran | closer look |
| ⏳ deferred | CodeRabbit quota gone; queued for the sweeper | nothing — it self-settles |
| ⏸️ reviewed_pending_fix | review saved, fixer blocked on Claude credit | top up credit |
| ⚠️ escalated | findings survived mediation | triage the linked Linear issues |
| ⛔ unavailable | the gate could not run at all | review by hand |
| 💸 budget | hit `MAX_BUDGET_USD` mid-work — **not a defect** | raise the cap, retry |
| ❌ failed | agent errored, wrong branch, or no commits | read `logs/<ISSUE>_*.json` |
| **KILL GATE FAILED** | AA-15 could not parse the fixture PDF into fixture rows | stop; the premise is unproven |

Failed issues are never written to `.completed_issues`, so they retry automatically.
Non-obvious failures are written up in `logs/REVISIT.md`.

## Logs

`NIGHT_REPORT.md` (what shipped + merge order) · `REVIEW_SWEEPER.md` (debt settled) ·
`REMEDIATION_REPORT.md` (PR comments answered) · `builder.log` · `REVISIT.md` ·
`<ISSUE>_*.json` (raw agent transcripts).
