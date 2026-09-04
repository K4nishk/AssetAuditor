# Project status — snapshot 2026-09-04

Written so a session with no prior context can pick up cold. Update it when the picture
materially changes; do not let it rot into fiction.

## Where the build is

**All 34 queued issues are complete** (`ops/.completed_issues` / `ops/queue.tsv`). The
2026-09-03 run finished the queue at 19:26. See `ops/logs/NIGHT_REPORT.md` for the full
run-by-run history and merge order.

**One PR is open: #34** (`feature/kch-69` → `development`). It carries the last three
issues at once — KCH-69 (AA-32 demo mode), KCH-68 (AA-31 blog) and KCH-72 (AA-36 findings
sweep) — because #32 and #33 were merged into `feature/kch-69` while it had no PR of its
own to merge into. #28–#31 are already in `development`.

**The kill gate passed.** KCH-50 (AA-15) parsed the text-layer Scotiabank fixture PDF
into the same rows as its CSV fixture, so the "upload your statements" premise is proven
and the tiers above it are worth building.

**Review debt: zero as of this snapshot** (`ops/.review_debt.tsv` does not currently
exist). Earlier in the build the sweeper settled four outstanding debts clean — PRs #5,
#6, #7 and #12. Notably PR #7 (KCH-47, masking) had found two real PII-leak bugs: an
account regex that could not match `Acct# 4821`, and a SIN regex blind to contiguous
9-digit values. Both were fixed and confirmed.

**Two issues opened out of the final run (2026-09-03), both still open:**

- **KCH-73** — the orchestrator runs from the feature branch it is building, so
  `e8c1127`'s base-retarget fix was never in the executing copy and KCH-69 lost its PR
  exactly as KCH-61 had. Also covers a CodeRabbit re-review that cleared a blocking
  finding on byte-identical code, and an agent escalation destroyed by the next issue's
  `git clean -fd`.
- **KCH-74** — `app/routes/demo.py` deletes blob prefixes outside the request
  transaction, so any rollback leaves rows pointing at deleted objects. Needs an owner
  decision on the retention scheme **before #34 merges**.

## The real ceiling

**The Claude account monthly spend limit.** Every run since 2026-09-01 has ended on it —
seven consecutive halts, not one on a code failure. A spend cap halts cleanly and leaves
the remaining queue untouched, so each session resumes where the last stopped. Budget,
not correctness, is what sets the pace.

## What runs by itself — nothing, currently

| Loop | Intended | Reality |
|---|---|---|
| Development | launchd every 2h at :22 | **agent unloaded**; run by hand in `tmux` |
| Code review | launchd hourly at :07 | **has never worked** — see below |

**The review sweeper has never run successfully under launchd.** macOS TCC blocks a
LaunchAgent's child shell from reading `~/Documents`, so it exits 1 having done nothing
and writes only to `ops/logs/sweeper.err.log`, which nobody reads. It failed 20+ times
before anyone noticed. A hand-run from your own terminal works, because your terminal
has TCC access. See [Environment-Gotchas](Environment-Gotchas.md#macos-tcc-and-launchd).

Until that is fixed, **hand-run `./ops/review_sweeper.sh` between builds** — the rule is
in `CLAUDE.md` and `ops/README.md`.

## The PR gate — `coderabbit/cli-gate`

The CLI gate runs **pre-push**, so a PR's fix commits are already in its first commit set
and its findings live in gitignored `ops/logs/`. From GitHub the body badge was an
unverifiable claim with no check behind it. `ops/pr_gate.sh` republishes the full
findings→fix trail per PR and sets a `coderabbit/cli-gate` commit status. Make it a
required status check on `development` and an unreviewed branch cannot merge.

A finding that has been triaged into its own issue can be recorded in
`ops/deferred_findings.tsv`, which subtracts it from the blocking count. It is never
hidden: the PR comment lists it with its issue, and the status says how many were
deferred.

## Lessons that changed the tooling (2026-09-02/03)

- **An aborted review is not a clean review.** A CodeRabbit run that dies (rate limit,
  connection drop) reports zero findings — indistinguishable from finding nothing. This
  falsely marked PR #6 and #7 "settled clean" and cleared their debt. Everything now
  requires a `complete` record before reading a verdict.
- **Merging a PR mid-run deletes the base branch under the in-flight issue.** Cost
  KCH-61 its PR. The orchestrator now retargets to `development` when its base vanishes.
- **A failed `gh pr create` used to be silent** — the error string became the PR number,
  gate evidence went to a nonexistent PR, and the issue was still recorded complete. Now
  reported loudly with the command to open it by hand.
- **Agents were told to commit each fix round and never checked.** KCH-55 amended
  instead, so three rounds left no trace. `verify_fix_commit()` now commits the work or
  warns that the trail is lost.
- **Non-blocking findings were never required to be addressed.** 10 are outstanding;
  KCH-72 (AA-36) sweeps them before ship.

## What is deliberately unverified

No Supabase project, Vercel token, Groq key or GPU box exists on this machine, and agents
have no network. Roughly a third of the backlog — the LiteLLM tier, prices, Blob writes,
metrics wiring, the security audits — is written, committed and **explicitly flagged
unverified** in the PR body. That is the honest-reporting rule working, not a failure.
For those PRs the CodeRabbit review is the only quality signal they will ever get, which
is why the sweeper being dead mattered more than it looked.

## Cold-start reading order

1. `ops/README.md` — how to run everything, and the five hard-won rules
2. `CLAUDE.md` — binding conventions, especially the CodeRabbit protocol
3. [Environment-Gotchas](Environment-Gotchas.md) — the machine's landmines
4. `docs/adr/ADR_v1.1.0.md` — architecture of record
5. `ops/logs/NIGHT_REPORT.md` — what shipped and the merge order
6. `mvp.md` — the issue specs; `data/samples/README.md` — the golden numbers

## Standing decisions worth not relitigating

- CodeRabbit stays on the **free tier**; review is deferred, never skipped, never blocking.
- Stacked PRs are expected to pile up; merging bottom-up self-heals the stack.
- Everything runs at **$0/month** (Assumption A16) — leaving a free tier is an ADR-level
  decision, not a purchase.
- `mvp.md` is the spec source of truth; Linear tracks state only.
- Implementation runs on `claude-sonnet-5`; `claude-opus-5` is reserved for mediation.
  There is no per-issue budget cap — `MAX_TURNS` and the account limit are the bounds.
