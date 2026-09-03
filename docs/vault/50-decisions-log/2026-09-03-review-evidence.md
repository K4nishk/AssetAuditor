---
tags: [decision-log]
date: 2026-09-03
---

# 2026-09-03 — Making the review gate provable, and trusting it less

The build reached 30 of 34 issues. The decisions this session were all one shape: **a
check that cannot be verified, or that reports success when it did not run, is worse
than no check at all.** Four separate instances of that turned up in two days.

Decisions:

1. **The CLI gate must publish its evidence to the PR.** The gate runs pre-push, so fix
   commits are already in a PR's first commit set and the findings sit in gitignored
   `ops/logs/` — GitHub only ever saw a one-line badge nobody could check, with no status
   behind it. `ops/pr_gate.sh` now republishes the per-round findings→fix trail and sets
   a `coderabbit/cli-gate` commit status. Needs no Claude, so it works under a spend cap.

2. **An aborted review is not a clean review.** A CodeRabbit run that dies on a rate
   limit or a dropped connection emits zero findings, which is indistinguishable from
   finding nothing. This had already marked PRs #6 and #7 "settled clean" and cleared
   their review debt — #7 being the masking PR, with two unconfirmed `major` findings in
   `worker/masking.py`. Nothing may now read a verdict from a round without a `complete`
   record (`cr_completed()`), in the orchestrator, the sweeper and the gate alike.

3. **A triaged finding may be deferred, on the record, rather than blocking forever.**
   AA-16 went five review rounds, each surfacing new material, while gating four PRs and
   everything above them. `ops/deferred_findings.tsv` records issue, round, file, Linear
   id and rationale; the gate subtracts it and **says so on the PR**. Tracked, not
   gitignored — it is an argument on the record, not a mute override. First use: AA-35.

4. **Non-blocking findings are swept before ship, not ignored.** The gate blocks on
   `critical|major|blocker|high` only, so `minor` findings accumulated unread — 81 raised
   across the build, 10 still outstanding. AA-36 (KCH-72, tier 10) sweeps them last.

5. **Trust the agent, then verify it.** The fix agent was told to commit each round and
   nothing checked; KCH-55 amended into its feature commit instead, so three rounds left
   no trace. `verify_fix_commit()` commits the work when the agent left it uncommitted,
   and warns when it amended rather than fabricating a trail.

6. **Merging is a concurrent writer.** Merging a PR deletes its branch, which pulls the
   base out from under whatever issue is stacked on it — `gh pr create` then fails with
   "Base ref must be a branch". A vanished base now retargets to `development`, since a
   deleted base is by definition already merged there.

Bugs found and fixed the same day:
- `pr_gate.sh` called `render_issue` inside `$(...)`, so the subshell's `GATE_STATE`
  never reached the caller and every status POST sent an empty state. Every one of 16
  failed with "State is not included in the list".
- The same script resolved `REPO_DIR` from its own path, which lands in `$TMPDIR` when
  the orchestrator re-execs — so every "answered by" cell rendered blank.
- A failed `gh pr create` fell straight through: the error string became the PR number,
  gate evidence went to a PR that did not exist, and the issue was still written to
  `.completed_issues`. KCH-61 read as lost work for a day; it had been pushed all along.

The through-line: every one of these made something look **better** than it was. That is
the failure mode to design against here — not the check that fails loudly.
