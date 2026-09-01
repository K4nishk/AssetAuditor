---
tags: [architecture, ops, review]
created: 2026-09-01
---

# Review Debt & the Hourly Sweeper

## The problem
CodeRabbit's free tier allows **8 reviews per replenishing window**. The queue needs far more — 32 issues, each wanting 1–3 rounds, plus re-reviews on remediation. On 2026-08-31 this surfaced as PR #7 (masking): the review ran, found two real PII-leak bugs, an agent fixed them, and the *confirming* re-review hit the quota — so the PR was reported as if never reviewed.

Two options, both rejected: **block and wait** (idles the build for hours on a free-tier limit) or **skip the review** (ships unreviewed code, breaking the provenance-first promise).

## The design: bank the debt, settle it later
Review becomes an **asynchronous obligation**, not a synchronous gate.

```
orchestrator: quota gone? ──► record debt ──► push + PR (⏳ deferred) ──► next issue
                                  │                              (never waits)
                                  ▼
ops/.review_debt.tsv   pr · branch · linear_id · state · recorded · attempts · findings
                                  │
launchd, hourly ──► ops/review_sweeper.sh ──► review ──► fixer agent (tier rules)
                                                   ──► local checks ──► push
                                                   ──► PR comment + Linear comment
                                                   ──► tier-3 ⇒ new Linear issue
```

**States:** `never_reviewed`, `unverified` (reviewed and fixed, confirming pass never ran), `reviewed_pending_fix` (review done and saved, fixer blocked on Claude credit — a later sweep fixes from the stored findings and spends no new review).

**Independent quotas.** Claude running dry parks branches as `reviewed_pending_fix` and reviews continue; CodeRabbit running dry defers reviews and the build continues. Neither ever stops the other.

**Oldest debt first** — the longest-unreviewed PR is closest to merge and has had most time to be built upon. **Greedy then polite** — settle as many as quota allows, stop cleanly on the first rate-limit.

## Why launchd, not cron
See [[../40-research/Scheduling-launchd]]. `StartCalendarInterval` fires on **wake from sleep** and coalesces missed intervals; cron does neither, is deprecated on macOS since 2005, and fails silently under TCC when touching `~/Documents` — where this repo lives.

## Gotchas
- **Idempotency is mandatory.** Coalesced wake-firing means two sweeps can start together; without `ops/.sweeper.lock` one finding becomes two PR comments and two Linear issues.
- **Lock files must be gitignored.** `git clean -fd` deletes untracked *directories*, and `run_issue()` runs it right after taking `ops/.worktree.lock` — an unignored lock deletes itself and mutual exclusion silently stops working. This caused the KCH-51 failure on 2026-09-01.
- The sweeper **pushes to the existing branch** — never a new PR, so the stack never deepens because a review arrived late.
- Debt is local only (gitignored); it does not travel to another machine.

Related: [[../10-mental-models/Provenance-First]] · [[Security-Model]] · `ops/README.md`
