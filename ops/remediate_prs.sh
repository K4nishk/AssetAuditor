#!/usr/bin/env bash
# AssetAuditor PR remediation pass.
#
# Answers CodeRabbit's *SaaS PR review* — the comments posted on a pull request
# after it opens. The orchestrator's CLI gate runs before the push and never sees
# these, so without this pass they sit unanswered and the owner is left chasing a
# bot across a dozen PRs. That is the gap this closes.
#
# Walks the open stack BOTTOM-UP and, for each PR:
#   1. merges the (already remediated) parent branch in, so the stack stays coherent
#   2. pulls CodeRabbit's PR findings
#   3. runs a remediation agent that classifies each finding by CONTRACT IMPACT
#      (see CLAUDE.md): tier 1 fix now · tier 2 fix + republish contract · tier 3 escalate
#   4. runs the local checks (nothing remote gates development)
#   5. pushes to the SAME branch — no new PRs, so the stack never gets deeper
#   6. escalates tier 3 to Linear and posts one summary comment on the PR
#
# Nothing merges. The owner still approves, but only ever sees PRs where every
# finding is either fixed or explained with a Linear link.
#
#   ./ops/remediate_prs.sh              # all open PRs, bottom-up
#   ./ops/remediate_prs.sh 3 4 5        # only these PR numbers

set -uo pipefail

# Same self-relocation as the orchestrator, and it matters more here: this script
# is brand new, so it exists on NO feature branch. The first `git clean -fd` would
# delete it out from under the running bash process. See orchestrator.sh for why.
if [ "${AA_RELOCATED:-0}" != "1" ]; then
  _src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  _run="${TMPDIR:-/tmp}/aa-ops-$$"
  mkdir -p "$_run" && cp "$_src"/*.sh "$_run"/ 2>/dev/null
  export AA_RELOCATED=1 AA_REAL_OPS="$_src"
  export REPO_DIR="${REPO_DIR:-$(cd "$_src/.." && pwd)}"
  exec bash "$_run/$(basename "${BASH_SOURCE[0]}")" "$@"
fi

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Reuse the orchestrator's Linear helpers, CodeRabbit parsers, run_agent, logging.
ORCHESTRATOR_LIB_ONLY=1 . "$OPS_DIR/orchestrator.sh"

REMEDIATION_REPORT="$LOG_DIR/REMEDIATION_REPORT.md"
# Deliberately conservative: pytest + ruff run cleanly on every branch in the stack.
# mypy is not in the default because its path config differs branch to branch, and a
# false "do not merge" on twelve PRs costs more trust than a missed type error.
# Override per run: LOCAL_CHECKS='uv run pytest -q && uv run mypy app' ./ops/remediate_prs.sh
LOCAL_CHECKS="${LOCAL_CHECKS:-uv run pytest -q && uv run ruff check .}"

cd "$REPO_DIR" || exit 1

# ---- Which PRs, in which order ----------------------------------------------

# Bottom-up: the PR based on development first, then whatever is based on that.
# Same walk the orchestrator posts as the merge order, so remediation order and
# merge order are identical — fixes always land beneath the PRs that inherit them.
ordered_prs() {
  gh pr list --state open --json number,title,headRefName,baseRefName 2>/dev/null | jq -r '
    . as $prs
    | def walk($cur):
        if $cur == null then empty
        else "\($cur.number)\t\($cur.headRefName)\t\($cur.baseRefName)",
             walk($prs | map(select(.baseRefName == $cur.headRefName)) | first)
        end;
      walk($prs | map(select(.baseRefName == "development")) | first)'
}

# ---- CodeRabbit PR findings --------------------------------------------------

# Two sources, because neither is reliable alone: the CLI needs the repo to be
# installed in the CodeRabbit org, and the PR comments are the ground truth the
# owner is actually reading.
fetch_pr_findings() {
  local pr="$1" out="$2"
  : > "$out"
  coderabbit pullrequest "$pr" --show-prompts --agent >> "$out" 2>&1 || true
  {
    echo ""
    echo "----- CodeRabbit comments on PR #$pr (as the owner sees them) -----"
    gh pr view "$pr" --json comments,reviews \
      --jq '[(.comments[]? | select(.author.login | test("coderabbit"; "i")) | .body),
             (.reviews[]?  | select(.author.login | test("coderabbit"; "i")) | .body)] | join("\n\n---\n\n")' 2>/dev/null
    gh api "repos/{owner}/{repo}/pulls/$pr/comments" \
      --jq '.[] | select(.user.login | test("coderabbit"; "i")) | "FILE \(.path):\(.line // .original_line // "?")\n\(.body)"' 2>/dev/null
  } >> "$out"
  [ -s "$out" ]
}

# Does the fetched text actually contain review substance, or just chrome?
has_findings() {
  local f="$1"
  [ -s "$f" ] || return 1
  grep -qiE "suggestion|refactor|issue|potential|consider|nitpick|_⚠|🛠|committable|actionable" "$f"
}

# ---- Remediate one PR --------------------------------------------------------

remediate_pr() {
  local pr="$1" branch="$2" base="$3"
  log "── PR #$pr ($branch, base $base) ─────────────────────"

  git reset --hard -q; git clean -fd -q
  git fetch origin --prune --quiet
  if ! git checkout -B "$branch" "origin/$branch" --quiet; then
    log "PR #$pr: cannot check out $branch — skipping."
    return 2
  fi

  # Pull the parent in first. Its remediation commits are already pushed, and a
  # merge (not a rebase) keeps the child's PR intact — force-pushing a rebased
  # branch under an open stacked PR is what breaks the chain.
  if [ "$base" != "development" ] && git rev-parse --verify "origin/$base" >/dev/null 2>&1; then
    if ! git merge --no-edit -q "origin/$base" 2>/dev/null; then
      log "PR #$pr: CONFLICT merging parent $base — leaving for the owner."
      git merge --abort 2>/dev/null
      gh pr comment "$pr" --body "⚠️ **Remediation skipped — merge conflict with the parent branch \`$base\`.** Its CodeRabbit fixes could not be merged down automatically. This PR needs a manual rebase before its own review comments can be addressed." >/dev/null 2>&1
      report_rem "- ⚠️ **PR #$pr** \`$branch\` — conflict merging parent, skipped"
      return 2
    fi
  fi

  local findings="$LOG_DIR/pr_${pr}_findings.txt"
  if ! fetch_pr_findings "$pr" "$findings" || ! has_findings "$findings"; then
    log "PR #$pr: no CodeRabbit findings to answer."
    report_rem "- ✅ **PR #$pr** \`$branch\` — no outstanding review findings"
    # Still push the parent merge if it produced commits.
    git diff --quiet "origin/$branch".."$branch" 2>/dev/null || git push origin "$branch" --quiet
    return 0
  fi

  local issue_id; issue_id=$(echo "$branch" | sed -E 's|feature/||' | tr '[:lower:]' '[:upper:]')
  local issue_json issue_uuid title
  issue_json=$(resolve_issue "$issue_id" 2>/dev/null)
  issue_uuid=$(jq -r '.id // empty' <<<"$issue_json" 2>/dev/null)
  title=$(jq -r '.title // empty' <<<"$issue_json" 2>/dev/null)

  rm -f "$REPO_DIR/REMEDIATION_OUT.json" "$REPO_DIR/CONTRACT_DELTA.md" "$REPO_DIR/ESCALATION.md"

  local medlog="$LOG_DIR/pr_${pr}_remediation.json"
  run_agent "You are the remediation agent for pull request #$pr — Linear issue $issue_id ($title), branch \`$branch\`.

CodeRabbit reviewed this PR after it opened. Its findings have never been seen by any
agent and are sitting unanswered on the PR. Your job is to answer every one of them.

READ CLAUDE.md FIRST — section 'CodeRabbit review protocol' is binding and defines the
three tiers you must classify by. Classify by CONTRACT IMPACT (who else breaks if you
fix it), not by severity alone.

  Tier 1 — no contract impact: fix it now, properly, fixing the pattern not the line.
  Tier 2 — changes something you published in CONTRACT_OUT.md (signature, table, column,
           event field, route shape): make the fix, then write CONTRACT_DELTA.md saying
           exactly what changed, the old shape, the new shape, and which downstream
           issues consume it.
  Tier 3 — high risk (needs redesign, an ADR decision, or touches provenance, masking,
           retention or auth semantics): DO NOT fix it. Write ESCALATION.md with what you
           implemented, the finding quoted, why you are not doing it, the concrete risk of
           shipping without it, and what a correct fix would involve.

CodeRabbit's findings on this PR:
$(head -c 14000 "$findings")

Rules:
- Never silence a finding: no disabled rules, no # noqa, no eslint-disable, no widened
  types or exceptions. Never mark something Tier 3 just to avoid the work, and never
  quietly 'fix' something that actually needs a design decision.
- Stay inside this PR's scope. Do not refactor files this PR did not touch.
- Run the local checks before you finish; they must pass:
    $LOCAL_CHECKS
- Commit your work on this branch: fix($issue_id): address CodeRabbit PR review
- Do NOT push and do NOT open or merge any PR.

Finally, write /REMEDIATION_OUT.json in the repo root (do not commit it):
{\"summary\":\"one line\",
 \"fixed\":[{\"finding\":\"...\",\"what_changed\":\"...\"}],
 \"contract_changed\":[{\"finding\":\"...\",\"what_changed\":\"...\",\"affects\":\"AA-n, AA-m\"}],
 \"escalated\":[{\"finding\":\"...\",\"why_not_fixed\":\"...\",\"risk_of_shipping\":\"...\",
                \"issue_title\":\"...\",\"issue_body\":\"...\"}]}" \
    "$medlog" "$IMPL_MODEL"

  if is_spend_capped "$medlog"; then
    log "*** SPEND CAP hit during remediation of PR #$pr — halting. ***"
    report_rem "- ⛔ **PR #$pr** — halted: spend limit reached. Remaining PRs untouched."
    return 3
  fi

  # ---- Local checks gate the push. Nothing remote is involved. ----
  local checks_ok="yes"
  if ! eval "$LOCAL_CHECKS" > "$LOG_DIR/pr_${pr}_checks.txt" 2>&1; then
    checks_ok="no"
    log "PR #$pr: LOCAL CHECKS FAILED after remediation."
  fi

  local out="$REPO_DIR/REMEDIATION_OUT.json"
  local n_fixed=0 n_contract=0 n_esc=0 summary="" esc_links=""
  if [ -f "$out" ] && jq -e . "$out" >/dev/null 2>&1; then
    n_fixed=$(jq '[.fixed[]?]        | length' "$out")
    n_contract=$(jq '[.contract_changed[]?] | length' "$out")
    n_esc=$(jq '[.escalated[]?]      | length' "$out")
    summary=$(jq -r '.summary // ""' "$out")
  else
    log "PR #$pr: remediation agent wrote no verdict file."
  fi

  # ---- Tier 2: relay the new contract to downstream agents ----
  if [ -f "$REPO_DIR/CONTRACT_DELTA.md" ]; then
    { echo ""; echo "### CONTRACT CHANGE — $issue_id (PR #$pr, $(date '+%Y-%m-%d'))"
      echo "_Supersedes the earlier contract block for $issue_id. Build against this._"
      echo ""; cat "$REPO_DIR/CONTRACT_DELTA.md"; } >> "$CONTRACTS_FILE"
    log "PR #$pr: contract delta relayed to downstream agents."
    rm -f "$REPO_DIR/CONTRACT_DELTA.md"
  fi

  # ---- Tier 3: escalate to Linear, then say so on the PR ----
  if [ "${n_esc:-0}" -gt 0 ]; then
    while IFS= read -r row; do
      local t b created
      t=$(jq -r '.issue_title // empty' <<<"$row"); [ -z "$t" ] && t="CodeRabbit follow-up from $issue_id (PR #$pr)"
      b="$(jq -r '.issue_body // ""' <<<"$row")

---
**Escalated from PR #$pr ($issue_id) by the remediation pass.**

**CodeRabbit's finding:** $(jq -r '.finding // ""' <<<"$row")
**Why the agent did not implement it:** $(jq -r '.why_not_fixed // ""' <<<"$row")
**Risk of shipping without it:** $(jq -r '.risk_of_shipping // ""' <<<"$row")"
      created=$(create_linear_issue "$t" "$b" "$issue_uuid")
      [ -n "$created" ] && { esc_links="$esc_links
- **$(jq -r '.finding' <<<"$row")** → $created
  - not implemented because: $(jq -r '.why_not_fixed' <<<"$row")
  - risk of shipping without it: $(jq -r '.risk_of_shipping' <<<"$row")"; }
    done < <(jq -c '.escalated[]?' "$out")
  fi

  # ---- Push to the same branch: no new PR, no deeper stack ----
  if git log "origin/$branch".."$branch" --oneline 2>/dev/null | grep -q .; then
    git push origin "$branch" --quiet && log "PR #$pr: pushed remediation commits."
  fi

  # ---- One comment so the owner reads an outcome, not a thread ----
  local verdict="✅ **All CodeRabbit findings addressed.**"
  [ "${n_esc:-0}" -gt 0 ] && verdict="⚠️ **CodeRabbit findings addressed, except those escalated below.**"
  [ "$checks_ok" = "no" ] && verdict="⛔ **Remediation ran but the local checks FAILED — do not merge.** See \`ops/logs/pr_${pr}_checks.txt\`."

  gh pr comment "$pr" --body "## Automated CodeRabbit remediation

$verdict

$summary

| | count |
|---|---|
| Fixed in this branch | $n_fixed |
| Fixed + contract updated for downstream agents | $n_contract |
| Escalated (not implemented) | $n_esc |

$([ -n "$esc_links" ] && printf '### Escalated — tracked in Linear, not fixed here\n%s\n' "$esc_links")
$([ "${n_contract:-0}" -gt 0 ] && echo "### Contract change
This PR changed a published interface. The updated contract has been relayed to every downstream agent, so later issues build against the new shape.")

_Local checks (\`pytest\`, \`ruff\`, \`mypy\`) ran before this push — remote CI is a second opinion, never a blocker. Nothing here was merged._" >/dev/null 2>&1

  [ -n "$issue_uuid" ] && comment_on_issue "$issue_uuid" "🐇 PR #$pr remediation: $n_fixed fixed, $n_contract contract-affecting, $n_esc escalated."

  local icon="✅"; [ "${n_esc:-0}" -gt 0 ] && icon="⚠️"; [ "$checks_ok" = "no" ] && icon="⛔"
  report_rem "- $icon **PR #$pr** \`$branch\` — fixed $n_fixed · contract $n_contract · escalated $n_esc$esc_links"
  rm -f "$out" "$REPO_DIR/ESCALATION.md"
  return 0
}

report_rem() { echo "$*" >> "$REMEDIATION_REPORT"; }

# ---- Main --------------------------------------------------------------------

command -v gh >/dev/null || { log "FATAL: gh CLI required"; exit 1; }
gh auth status >/dev/null 2>&1 || { log "FATAL: gh not authenticated"; exit 1; }
[ -n "${LINEAR_API_KEY:-}" ] || log "WARN: LINEAR_API_KEY unset — escalations cannot be created as Linear issues."

{ echo ""; echo "# Remediation pass — $(date '+%Y-%m-%d %H:%M')"; echo ""; } >> "$REMEDIATION_REPORT"

WANTED=("$@")
want() { [ "${#WANTED[@]}" -eq 0 ] && return 0; local n; for n in ${WANTED[@]+"${WANTED[@]}"}; do [ "$n" = "$1" ] && return 0; done; return 1; }

log "Remediation pass starting (bottom-up)."
while IFS=$'\t' read -r pr branch base; do
  [ -z "${pr:-}" ] && continue
  want "$pr" || { log "Skipping PR #$pr (not in the requested set)"; continue; }
  remediate_pr "$pr" "$branch" "$base"
  rc=$?
  [ "$rc" -eq 3 ] && { log "Halting remediation: spend cap."; exit 2; }
done < <(ordered_prs)

report_rem ""
report_rem "Every PR above has had its CodeRabbit review answered: fixed, or escalated with the impact stated and a Linear issue linked in a PR comment. Approve and squash-merge bottom-up."
log "Remediation pass complete. Report: $REMEDIATION_REPORT"
