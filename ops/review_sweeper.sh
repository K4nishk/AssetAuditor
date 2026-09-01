#!/usr/bin/env bash
# AssetAuditor review-debt sweeper — runs hourly under launchd.
#
# The build never waits for CodeRabbit. On the free tier its 8-review quota runs
# out long before our queue does, so the orchestrator banks unfinished reviews as
# debt (ops/.review_debt.tsv) and keeps building. This settles that debt whenever
# quota has replenished, and closes the loop the owner asked for:
#
#   review  →  fixer agent (tier-classified per CLAUDE.md)  →  local checks
#           →  push to the same branch  →  PR comment  →  Linear comment
#           →  tier-3 findings escalated as new Linear issues
#
# It is deliberately greedy-then-polite: it settles as many debts as the quota
# allows, and the moment CodeRabbit says "rate limit" it stops cleanly and leaves
# the rest for the next hour. Nothing merges; nothing blocks development.
#
#   ./ops/review_sweeper.sh            # settle as much debt as quota allows
#   ./ops/review_sweeper.sh --status   # print the ledger and exit
#   ./ops/review_sweeper.sh --once <pr>

set -uo pipefail

if [ "${AA_RELOCATED:-0}" != "1" ]; then
  _src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  _run="${TMPDIR:-/tmp}/aa-ops-$$"
  mkdir -p "$_run" && cp "$_src"/*.sh "$_run"/ 2>/dev/null
  export AA_RELOCATED=1 AA_REAL_OPS="$_src"
  export REPO_DIR="${REPO_DIR:-$(cd "$_src/.." && pwd)}"
  exec bash "$_run/$(basename "${BASH_SOURCE[0]}")" "$@"
fi

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCHESTRATOR_LIB_ONLY=1 . "$OPS_DIR/orchestrator.sh"

SWEEP_LOG="$LOG_DIR/REVIEW_SWEEPER.md"
LOCK="$STATE_HOME/.sweeper.lock"
LOCAL_CHECKS="${LOCAL_CHECKS:-uv run pytest -q && uv run ruff check .}"

cd "$REPO_DIR" || exit 1

# launchd fires on wake and coalesces missed intervals, so two runs can overlap
# after a long sleep. Without this guard the same PR gets two review comments and
# two Linear escalations for one finding.
acquire_lock() {
  if mkdir "$LOCK" 2>/dev/null; then trap 'rmdir "$LOCK" 2>/dev/null' EXIT INT TERM; return 0; fi
  local age; age=$(( $(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || echo 0) ))
  if [ "$age" -gt 5400 ]; then   # 90 min: longer than any real sweep
    log "Stale lock (${age}s) — reclaiming."
    rmdir "$LOCK" 2>/dev/null; mkdir "$LOCK" 2>/dev/null || return 1
    trap 'rmdir "$LOCK" 2>/dev/null' EXIT INT TERM; return 0
  fi
  log "Another sweeper is running (lock age ${age}s). Exiting."
  return 1
}

show_status() {
  echo "Review debt ledger: $REVIEW_DEBT"
  if [ ! -s "$REVIEW_DEBT" ]; then echo "  (empty — every PR has been reviewed)"; return 0; fi
  printf "  %-6s %-24s %-10s %-20s %s\n" PR BRANCH ISSUE STATE RECORDED
  while IFS=$'\t' read -r pr branch issue state epoch attempts stored; do
    [ -z "${pr:-}" ] && continue
    printf "  %-6s %-24s %-10s %-20s %s (attempts: %s)\n" "#$pr" "$branch" "$issue" "$state" \
      "$(date -r "${epoch:-0}" '+%Y-%m-%d %H:%M' 2>/dev/null)" "${attempts:-0}"
  done < "$REVIEW_DEBT"
}

# ---- Settle one debt ---------------------------------------------------------

settle() {
  local pr="$1" branch="$2" issue="$3" state="$4" stored_findings="${5:-}"
  log "── settling #$pr ($branch, $issue, was: $state) ──"

  # The orchestrator may be mid-issue in this same worktree. Wait a little, then
  # give up gracefully — this PR keeps its debt and the next hourly sweep retries.
  if ! acquire_worktree "${SWEEP_LOCK_WAIT:-600}"; then
    log "#$pr: worktree busy (orchestrator working) — deferring to the next sweep."
    return 3
  fi
  # -e: the locks must survive a clean run from a branch whose .gitignore predates them.
  git reset --hard -q
  git clean -fd -q -e .worktree.lock -e .builder.lock -e .sweeper.lock
  git fetch origin --prune --quiet
  if ! git rev-parse --verify "origin/$branch" >/dev/null 2>&1; then
    log "#$pr: branch gone (merged or deleted) — clearing debt."
    clear_review_debt "$branch"; release_worktree; return 0
  fi
  git checkout -B "$branch" "origin/$branch" --quiet || { log "#$pr: checkout failed"; release_worktree; return 1; }

  local base; base=$(gh pr view "$pr" --json baseRefName --jq .baseRefName 2>/dev/null)
  [ -z "$base" ] && base="development"
  local base_ref="origin/$base"
  git rev-parse --verify "$base_ref" >/dev/null 2>&1 || base_ref="origin/development"

  local findings
  if [ "$state" = "reviewed_pending_fix" ] && [ -n "$stored_findings" ] && [ -s "$stored_findings" ]; then
    # CodeRabbit already reviewed this branch; only the fixer was outstanding.
    # Reusing the saved findings spends no review quota at all.
    findings="$stored_findings"
    log "#$pr: reusing findings from the earlier review (no quota spent)."
  else
    findings="$LOG_DIR/sweep_pr${pr}_$(date +%s).json"
    coderabbit review --agent --committed --base "$base_ref" -c CLAUDE.md > "$findings" 2>&1
    # CodeRabbit's OWN quota is the only thing that halts a sweep: no point asking
    # it for more reviews this hour. Claude's quota must never stop us (see below).
    if cr_is_rate_limited "$findings"; then
      log "CodeRabbit quota exhausted. Stopping this sweep; $issue stays in the ledger."
      release_worktree; return 2
    fi
  fi

  local blocking total
  blocking=$(cr_blocking_count "$findings"); total=$(cr_total_findings "$findings")
  log "#$pr: $blocking blocking / $total total finding(s)."

  local issue_uuid; issue_uuid=$(resolve_issue "$issue" 2>/dev/null | jq -r '.id // empty')

  if [ "${total:-0}" -eq 0 ]; then
    clear_review_debt "$branch"
    gh pr comment "$pr" --body "## 🐇 Deferred CodeRabbit review — now complete

This PR was opened while CodeRabbit's free-tier quota was exhausted, so its review was deferred rather than skipped. The hourly sweeper has now reviewed it: **no findings**.

_Reviewed against \`$base_ref\`. Nothing merged._" >/dev/null 2>&1
    [ -n "$issue_uuid" ] && comment_on_issue "$issue_uuid" "🐇 Deferred review settled for PR #$pr — clean, no findings."
    sweep_report "- ✅ **#$pr** \`$issue\` — deferred review settled, clean"
    release_worktree; return 0
  fi

  # ---- Findings exist: hand them to a fixer agent -------------------------
  # Claude's quota and CodeRabbit's quota are INDEPENDENT resources and must stay
  # that way. If Claude is exhausted we still keep the review — publish the
  # findings to the PR and Linear, park the branch as reviewed_pending_fix with the
  # findings saved, and carry on reviewing the next PR. Reviews keep flowing while
  # the owner tops up credits; no review is ever lost or re-spent.
  if [ "${CLAUDE_CAPPED:-0}" = "1" ]; then
    park_pending_fix "$pr" "$branch" "$issue" "$findings" "$blocking" "$total" "$issue_uuid"
    release_worktree; return 0
  fi

  rm -f "$REPO_DIR/REMEDIATION_OUT.json" "$REPO_DIR/CONTRACT_DELTA.md"
  local fixlog="$LOG_DIR/sweep_pr${pr}_fixer.json"
  run_agent "You are the fixer agent for pull request #$pr — Linear issue $issue, branch \`$branch\`.

This branch shipped before CodeRabbit could review it (its free-tier quota was exhausted).
The review has now run and found issues. Nobody has seen them yet. Address them.

READ CLAUDE.md FIRST — 'CodeRabbit review protocol' is binding. Classify every finding by
CONTRACT IMPACT, not severity:
  Tier 1 — no contract impact: fix it now, fixing the pattern rather than the flagged line.
  Tier 2 — changes something you published in CONTRACT_OUT.md (signature, table, column,
           event field, route): fix it, then write CONTRACT_DELTA.md stating the old shape,
           the new shape, and which downstream issues consume it.
  Tier 3 — high risk (needs redesign, an ADR decision, or touches provenance, masking,
           retention or auth): DO NOT fix. Record it for escalation with the risk of
           shipping without it.

CodeRabbit findings ($blocking blocking of $total):
$(head -c 14000 "$findings")

Rules: never silence a finding; stay inside this PR's scope; run the local checks
($LOCAL_CHECKS) before finishing; commit on this branch as
'fix($issue): address deferred CodeRabbit review'. Do NOT push, do NOT open or merge a PR.

Write /REMEDIATION_OUT.json in the repo root (do not commit it):
{\"summary\":\"one line\",
 \"fixed\":[{\"finding\":\"...\",\"what_changed\":\"...\"}],
 \"contract_changed\":[{\"finding\":\"...\",\"what_changed\":\"...\",\"affects\":\"AA-n\"}],
 \"escalated\":[{\"finding\":\"...\",\"why_not_fixed\":\"...\",\"risk_of_shipping\":\"...\",
                \"issue_title\":\"...\",\"issue_body\":\"...\"}]}" \
    "$fixlog" "$IMPL_MODEL"

  if is_spend_capped "$fixlog"; then
    # Claude is out of credit. This does NOT stop the sweep — CodeRabbit reviews
    # continue for every remaining PR; only the fixing is postponed.
    log "Claude spend cap reached. Reviews continue; fixes postponed for #$pr."
    CLAUDE_CAPPED=1
    park_pending_fix "$pr" "$branch" "$issue" "$findings" "$blocking" "$total" "$issue_uuid"
    release_worktree; return 0
  fi

  local checks_ok="yes"
  eval "$LOCAL_CHECKS" > "$LOG_DIR/sweep_pr${pr}_checks.txt" 2>&1 || checks_ok="no"

  local out="$REPO_DIR/REMEDIATION_OUT.json"
  local n_fixed=0 n_contract=0 n_esc=0 summary="" esc_links=""
  if [ -f "$out" ] && jq -e . "$out" >/dev/null 2>&1; then
    n_fixed=$(jq '[.fixed[]?] | length' "$out")
    n_contract=$(jq '[.contract_changed[]?] | length' "$out")
    n_esc=$(jq '[.escalated[]?] | length' "$out")
    summary=$(jq -r '.summary // ""' "$out")
  fi

  # Tier 2 → relay the new contract so downstream agents stop building on the old shape
  if [ -f "$REPO_DIR/CONTRACT_DELTA.md" ]; then
    { echo ""; echo "### CONTRACT CHANGE — $issue (PR #$pr, deferred review, $(date '+%Y-%m-%d'))"
      echo "_Supersedes the earlier contract block for $issue. Build against this._"; echo ""
      cat "$REPO_DIR/CONTRACT_DELTA.md"; } >> "$CONTRACTS_FILE"
    log "#$pr: contract delta relayed to downstream agents."
    rm -f "$REPO_DIR/CONTRACT_DELTA.md"
  fi

  # Tier 3 → new Linear issues, linked to the parent
  if [ "${n_esc:-0}" -gt 0 ]; then
    while IFS= read -r row; do
      local t b created
      t=$(jq -r '.issue_title // empty' <<<"$row"); [ -z "$t" ] && t="CodeRabbit follow-up from $issue (PR #$pr)"
      b="$(jq -r '.issue_body // ""' <<<"$row")

---
**Escalated from PR #$pr ($issue) by the hourly review sweeper.**

**Finding:** $(jq -r '.finding // ""' <<<"$row")
**Why it was not implemented:** $(jq -r '.why_not_fixed // ""' <<<"$row")
**Risk of shipping without it:** $(jq -r '.risk_of_shipping // ""' <<<"$row")"
      created=$(create_linear_issue "$t" "$b" "$issue_uuid")
      [ -n "$created" ] && esc_links="$esc_links
- **$(jq -r '.finding' <<<"$row")** → $created
  - not implemented because: $(jq -r '.why_not_fixed' <<<"$row")
  - risk of shipping without it: $(jq -r '.risk_of_shipping' <<<"$row")"
    done < <(jq -c '.escalated[]?' "$out")
  fi

  git add -u >/dev/null 2>&1
  git diff --cached --quiet || git commit -q -m "fix($issue): address deferred CodeRabbit review"
  git log "origin/$branch".."$branch" --oneline 2>/dev/null | grep -q . && git push origin "$branch" --quiet

  local verdict="✅ **All findings addressed.**"
  [ "${n_esc:-0}" -gt 0 ] && verdict="⚠️ **Findings addressed, except those escalated below.**"
  [ "$checks_ok" = "no" ] && verdict="⛔ **Fixes applied but the local checks FAILED — do not merge.** See \`ops/logs/sweep_pr${pr}_checks.txt\`."

  gh pr comment "$pr" --body "## 🐇 Deferred CodeRabbit review — now complete

This PR shipped before CodeRabbit could review it (free-tier quota exhausted). The hourly sweeper has now reviewed it and acted on the results.

$verdict

$summary

| | count |
|---|---|
| Fixed in this branch | $n_fixed |
| Fixed + contract updated for downstream agents | $n_contract |
| Escalated (not implemented) | $n_esc |

$([ -n "$esc_links" ] && printf '### Escalated — tracked in Linear, not fixed here\n%s\n' "$esc_links")
_Local checks ran before the push. Nothing merged._" >/dev/null 2>&1

  [ -n "$issue_uuid" ] && comment_on_issue "$issue_uuid" "🐇 Deferred review settled for PR #$pr — $n_fixed fixed, $n_contract contract-affecting, $n_esc escalated."

  clear_review_debt "$branch"
  local icon="✅"; [ "${n_esc:-0}" -gt 0 ] && icon="⚠️"; [ "$checks_ok" = "no" ] && icon="⛔"
  sweep_report "- $icon **#$pr** \`$issue\` — fixed $n_fixed · contract $n_contract · escalated $n_esc"
  rm -f "$out"
  release_worktree
  return 0
}

sweep_report() { echo "$*" >> "$SWEEP_LOG"; }

# Review kept, fix deferred: publish what CodeRabbit found so the owner and the
# Linear issue both have it, and store the findings so the next sweep can fix
# without spending another review.
park_pending_fix() {
  local pr="$1" branch="$2" issue="$3" findings="$4" blocking="$5" total="$6" issue_uuid="$7"
  local keep="$LOG_DIR/pending_fix_pr${pr}.json"
  [ "$findings" != "$keep" ] && cp -f "$findings" "$keep" 2>/dev/null
  record_review_debt "$pr" "$branch" "$issue" "reviewed_pending_fix" "$keep"
  gh pr comment "$pr" --body "## 🐇 CodeRabbit review complete — fixes pending

CodeRabbit has now reviewed this PR (**$blocking blocking of $total finding(s)**), but the fixer agent could not run: the Claude account is at its spend limit.

**Nothing is lost.** The findings are saved and this PR is queued as \\`reviewed_pending_fix\\`. As soon as credits are topped up, the hourly sweeper fixes what it can, updates any changed contract for downstream agents, and escalates anything high-risk as a Linear issue — without spending another CodeRabbit review.

<details><summary>Findings (raw)</summary>

\\`\\`\\`
$(head -c 5000 "$findings")
\\`\\`\\`
</details>" >/dev/null 2>&1
  [ -n "$issue_uuid" ] && comment_on_issue "$issue_uuid" "🐇 PR #$pr reviewed by CodeRabbit ($blocking blocking / $total total). Fixes postponed — Claude spend limit reached. Queued as reviewed_pending_fix."
  sweep_report "- ⏸️ **#$pr** \\`$issue\\` — reviewed ($total finding(s)); fixes pending Claude credit"
  log "#$pr: parked as reviewed_pending_fix."
}

# ---- Main --------------------------------------------------------------------

case "${1:-}" in
  --status) show_status; exit 0 ;;
esac

command -v coderabbit >/dev/null || { log "coderabbit CLI absent — nothing to do."; exit 0; }
gh auth status >/dev/null 2>&1  || { log "gh not authenticated — cannot sweep."; exit 1; }
[ -s "$REVIEW_DEBT" ] || { log "No review debt. Nothing to sweep."; exit 0; }
acquire_lock || exit 0

{ echo ""; echo "## Sweep — $(date '+%Y-%m-%d %H:%M')"; } >> "$SWEEP_LOG"
log "Review sweeper starting. Debt: $(grep -c . "$REVIEW_DEBT") PR(s)."

settled=0; halted=0
# Oldest debt first: the longest-unreviewed PR is the one most likely to be
# merged next, and the one whose findings have had most time to be built upon.
CLAUDE_CAPPED=0
while IFS=$'\t' read -r pr branch issue state epoch attempts stored; do
  [ -z "${pr:-}" ] && continue
  if [ -n "${SWEEP_ONLY_PR:-}" ] && [ "$pr" != "$SWEEP_ONLY_PR" ]; then continue; fi
  settle "$pr" "$branch" "$issue" "$state" "${stored:-}"
  case $? in
    0) settled=$((settled+1)) ;;
    2) halted=1; break ;;
    3) log "Deferred #$pr (worktree busy)." ;;
    *) awk -F'\t' -v b="$branch" 'BEGIN{OFS="\t"} $2==b{$6=$6+1} {print}' "$REVIEW_DEBT" > "$REVIEW_DEBT.tmp" \
         && mv -f "$REVIEW_DEBT.tmp" "$REVIEW_DEBT" ;;
  esac
done < <(sort -t$'\t' -k5,5n "$REVIEW_DEBT")

remaining=$(grep -c . "$REVIEW_DEBT" 2>/dev/null || echo 0)
sweep_report "Settled $settled this hour; $remaining still owed$([ "$halted" = "1" ] && echo " (stopped early: quota exhausted)")."
log "Sweep done. Settled $settled, remaining $remaining."
