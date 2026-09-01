# Project status — snapshot 2026-09-01

Written so a session with no prior context can pick up cold. Update it when the picture
materially changes; do not let it rot into fiction.

## Where the build is

**13 of 32 queued issues complete. 19 remain.** 12 stacked PRs are open against
`development`, none merged (PR #1 / AA-1 was merged earlier).

**The kill gate passed.** KCH-50 (AA-15) parsed the text-layer Scotiabank fixture PDF
into the same rows as its CSV fixture, so the "upload your statements" premise is proven
and the tiers above it are worth building.

**Review debt: zero.** The sweeper settled all four outstanding debts clean — PRs #5, #6,
#7 and #12. Notably PR #7 (KCH-47, masking) had found two real PII-leak bugs: an account
regex that could not match `Acct# 4821`, and a SIN regex blind to contiguous 9-digit
values. Both were fixed and confirmed.

**Two issues awaiting retry** (neither is in `.completed_issues`, so both retry
automatically):
- **KCH-51 (AA-16, LiteLLM tier)** — failed for an infrastructure reason, not a code one.
  The agent detected the git worktree changing under it and correctly refused to work.
  The sweeper and orchestrator raced over the shared worktree because the lock directory
  was not gitignored, so `git clean -fd` deleted it moments after it was taken. Fixed;
  see the 2026-09-01 decision-log entry. (A per-run copy also lands in
  `ops/logs/REVISIT.md`, which is gitignored and therefore local-only.)
- **KCH-52 (AA-17, parse-confirm screen)** — hit the `$10` per-issue budget cap mid-work.
  Not a defect. The per-issue cap has since been removed entirely: runs are bounded by
  `MAX_TURNS` and by the account spend limit, which halts the run cleanly. Implementation
  now runs on `claude-sonnet-5`, with `claude-opus-5` reserved for mediation.

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
