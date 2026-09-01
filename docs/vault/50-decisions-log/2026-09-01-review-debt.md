---
tags: [decision-log]
date: 2026-09-01
---

# 2026-09-01 — Free-tier CodeRabbit, deferred review, autonomous loops

The first overnight run shipped 13 issues and 12 PRs, then hit two limits: our own monthly spend cap (which cascaded into 19 false failures) and CodeRabbit's free-tier review quota (which made a reviewed-and-fixed PR look unreviewed).

Decisions:
1. **Stay on CodeRabbit's free tier.** 8 reviews per window is an operating condition, not a problem to pay away.
2. **Review is deferred, never skipped, and never blocks.** Quota exhaustion records debt in `ops/.review_debt.tsv`; the queue continues. See [[../30-architecture/Review-Debt-Sweeper]].
3. **Claude and CodeRabbit quotas are independent.** A Claude spend cap parks branches as `reviewed_pending_fix` and reviews keep running; findings are published to the PR and Linear immediately and fixed later without re-spending a review.
4. **Hourly launchd sweeper** settles debt; **2-hourly builder** (`run_builder.sh`) continues development. Rationale over cron and hosted schedulers: [[../40-research/Scheduling-launchd]].
5. **A spend cap halts the run** instead of failing every remaining issue; **per-issue budget exhaustion** (`error_max_budget_usd`) is reported distinctly so a $-cap cutoff never reads as broken code.
   - *Superseded later the same day:* the per-issue cap (`MAX_BUDGET_USD`, `--max-budget-usd`) was removed outright, along with its detector and the 💸 marker. Runs are now bounded by `MAX_TURNS` and the account spend limit only — decision 5's first clause still holds, its second no longer applies. Cost is managed by model tier instead: `IMPL_MODEL=claude-sonnet-5` for all code-writing agents, `MEDIATOR_MODEL=claude-opus-5` for adjudication only.
6. `never_reviewed`, `unverified` and `reviewed_pending_fix` are tracked separately — conflating the first two misrepresented PR #7.

Bugs found and fixed the same day:
- CodeRabbit's real finding shape (`type:"finding"`, no `message` field) was never matched by the structured parser — counts worked only by grepping for "major".
- Non-JSON trailing lines broke `jq` on the findings file.
- `coderabbit pullrequest` requires `--show-prompts`.
- Both ops scripts now re-exec from `$TMPDIR`, because they rewrite the worktree they live in.
- **Lock directories were not gitignored**, so `git clean -fd` deleted `ops/.worktree.lock` immediately after it was taken — mutual exclusion was silently inert. This is what broke KCH-51: the sweeper and orchestrator raced, the tree changed under the agent, and it correctly refused to work. See `ops/logs/REVISIT.md`.
- **Launching with uncommitted `ops/` changes** let the first `git reset --hard` restore the previous toolchain, silently discarding every fix made that session.
