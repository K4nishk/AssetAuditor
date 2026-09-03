# Project status — snapshot 2026-09-03

Written so a session with no prior context can pick up cold. Update it when the picture
materially changes; do not let it rot into fiction.

## Where the build is

**32 of 34 queued issues complete** (`ops/.completed_issues` / `ops/queue.tsv`), this one
(KCH-68, AA-31) in flight as the 33rd. Only KCH-72 (AA-36, the final CodeRabbit sweep)
remains — it deliberately depends on every feature issue merging first, so it runs last.
See `ops/logs/NIGHT_REPORT.md` for the full run-by-run history and merge order.

**The kill gate passed.** KCH-50 (AA-15) parsed the text-layer Scotiabank fixture PDF
into the same rows as its CSV fixture, so the "upload your statements" premise is proven
and the tiers above it are worth building.

**Review debt: zero as of this snapshot** (`ops/.review_debt.tsv` does not currently
exist). Earlier in the build the sweeper settled four outstanding debts clean — PRs #5,
#6, #7 and #12. Notably PR #7 (KCH-47, masking) had found two real PII-leak bugs: an
account regex that could not match `Acct# 4821`, and a SIN regex blind to contiguous
9-digit values. Both were fixed and confirmed.

**Resolved:** the two issues once tracked here as "awaiting retry" (KCH-51 LiteLLM tier,
KCH-52 parse-confirm screen) both completed on retry and are in `.completed_issues`; see
the 2026-09-01 decision-log entry and `ops/logs/REVISIT.md` (gitignored, local-only) for
what broke and how it was fixed.

**Open, not yet root-caused:** PR creation has twice failed with a GitHub GraphQL error —
*"Head sha can't be blank, Base sha can't be blank, No commits between `<parent-branch>`
and `<branch>`, Base ref must be a branch"* — on KCH-61 and again on KCH-69, both times
after CodeRabbit's own review completed successfully against the branch. The branch and
its commits exist (`git log` on `feature/kch-69` shows the AA-32 commit); this reads like
a GitHub-side eventual-consistency race between the push and the `createPullRequest` call
immediately after it, not a code problem. Until it's root-caused, treat a "PR create
failed" line in a night report as **the branch is done and reviewed, just needs a PR
opened by hand** — not as a build failure.

## What runs by itself

| Loop | Schedule | Entry point |
|---|---|---|
| Development | every 2 hours at :22 | `ops/run_builder.sh` (launchd `com.assetauditor.builder`) |
| Code review | hourly at :07 | `ops/review_sweeper.sh` (launchd `com.assetauditor.review-sweeper`) |

Both fire on wake and coalesce missed slots. Guards prevent them colliding over the
shared worktree. Nothing merges — human review on `development` is the only merge gate.

## The real ceiling

Not the schedule — **the Claude account spend limit**. The first overnight run exhausted
the monthly cap after 13 issues, which then cascaded into 19 false failures before that
bug was fixed. A spend cap now **halts the run cleanly**, leaving the rest untouched for
the next attempt, and code review continues regardless because the two quotas are
independent.

## What is deliberately unverified

No Supabase project, Vercel token, Groq key or GPU box exists on this machine, and agents
have no network. Work touching them is written, committed and **explicitly flagged
unverified** in the PR body — that is the honest-reporting rule working, not a failure.
Postgres was also down for this stretch (see [Environment-Gotchas](Environment-Gotchas.md)),
so DB-backed tests in AA-18, AA-21 and the lineage work are written but unexecuted.

## Cold-start reading order

1. `ops/README.md` — how to run everything, and the four hard-won rules
2. `CLAUDE.md` — binding conventions, especially the CodeRabbit protocol tiers
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
