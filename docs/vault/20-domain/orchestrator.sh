#!/usr/bin/env bash
# Aegis backlog orchestrator
# Runs Claude Code headlessly against Linear backlog issues, in dependency order,
# and self-resumes after usage-limit windows reset.
#
# Prereqs:
#   - `claude` CLI installed and logged in (claude login)
#   - `gh` CLI installed and authenticated (gh auth login)
#   - LINEAR_API_KEY env var set (Linear Settings -> API -> Personal API keys)
#   - REPO_DIR points at a local clone of your Aegis repo with `main` up to date
#   - queue.tsv (same directory) with tier-ordered Linear issue IDs
#
# Run inside tmux so it survives you closing the laptop:
#   tmux new -s aegis
#   ./orchestrator.sh
#   (ctrl-b, d to detach; tmux attach -t aegis to check back in)

set -uo pipefail

REPO_DIR="${REPO_DIR:-/Users/ishq_kan/Documents/Github/Aegis}"
QUEUE_FILE="${QUEUE_FILE:-/Users/ishq_kan/Documents/Github/Aegis/queue.tsv}"
LOG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/logs"
STATE_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.completed_issues"
MAX_TURNS=90
MAX_BUDGET_USD=3.00
LINEAR_API="https://api.linear.app/graphql"
CONTRACTS_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.agent_contracts.md"

# Explicit allowlist. Anything not named here is denied outright — headless runs
# never prompt, so a missing entry silently cripples the agent rather than
# pausing for a human. Deliberately excludes rm, sudo, aws and curl-to-anywhere.
ALLOWED_TOOLS="Read,Write,Edit,Grep,Glob,\
Bash(git:*),Bash(gh:*),\
Bash(pytest:*),Bash(ruff:*),Bash(mypy:*),Bash(python3:*),Bash(python:*),\
Bash(pip:*),Bash(pip3:*),Bash(uv:*),Bash(npm:*),Bash(npx:*),Bash(node:*),\
Bash(psql:*),Bash(pg_dump:*),Bash(pg_restore:*),Bash(createdb:*),Bash(dropdb:*),\
Bash(redis-cli:*),Bash(docker:*),Bash(docker-compose:*),\
Bash(pip-audit:*),Bash(gitleaks:*),Bash(trivy:*),Bash(syft:*),\
Bash(uvicorn:*),Bash(make:*),Bash(mkdir:*),Bash(ls:*),Bash(cat:*),Bash(cp:*),Bash(mv:*),\
Bash(echo:*),Bash(head:*),Bash(tail:*),Bash(wc:*),Bash(diff:*),Bash(jq:*),Bash(sed:*),Bash(grep:*)"

mkdir -p "$LOG_DIR"
touch "$STATE_FILE"
touch "$CONTRACTS_FILE"

# Rolling pointer to the previous issue's branch, for stacked-PR bases.
# SEED_LAST_BRANCH lets a fresh run continue an existing stack instead of
# re-basing on development and losing the unmerged work below it.
LAST_BRANCH="${SEED_LAST_BRANCH:-}"
LAST_ISSUE="${SEED_LAST_ISSUE:-upstream}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# ---- Linear GraphQL helpers -------------------------------------------------

linear_query() {
  local query="$1"
  curl -s -X POST "$LINEAR_API" \
    -H "Authorization: $LINEAR_API_KEY" \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg q "$query" '{query: $q}')"
}

get_issue_details() {
  local identifier="$1"
  linear_query "{ issue(id: \"$identifier\") { id identifier title description state { name } } }"
}

# Resolve human-readable identifier (e.g. KCH-9) to Linear's internal issue UUID + current state
resolve_issue() {
  local identifier="$1"
  linear_query "{ issues(filter: { number: { eq: ${identifier##*-} }, team: { key: { eq: \"${identifier%-*}\" } } }) { nodes { id identifier title description state { id name } } } }" \
    | jq -r '.data.issues.nodes[0]'
}

set_issue_state() {
  local issue_uuid="$1" state_name="$2" team_key="$3"
  local state_id
  state_id=$(linear_query "{ team(id: \"$team_key\") { states { nodes { id name } } } }" \
    | jq -r --arg s "$state_name" '.data.team.states.nodes[] | select(.name==$s) | .id')
  [ -z "$state_id" ] && { log "WARN: could not resolve state '$state_name'"; return 1; }
  linear_query "mutation { issueUpdate(id: \"$issue_uuid\", input: { stateId: \"$state_id\" }) { success } }" >/dev/null
}

comment_on_issue() {
  local issue_uuid="$1" body="$2"
  linear_query "mutation { commentCreate(input: { issueId: \"$issue_uuid\", body: \"$body\" }) { success } }" >/dev/null
}

# ---- Stacked-PR merge order --------------------------------------------------

# Walks the open PRs from the one based on development upward, so reviewers get
# an explicit bottom-up merge order instead of reverse-engineering the stack.
merge_sequence() {
  gh pr list --state open --json number,title,headRefName,baseRefName 2>/dev/null | jq -r '
    . as $prs
    | def walk($cur):
        if $cur == null then empty
        else "\($cur.number)\t\($cur.headRefName)\t\($cur.title)",
             walk($prs | map(select(.baseRefName == $cur.headRefName)) | first)
        end;
      walk($prs | map(select(.baseRefName == "development")) | first)
  ' | awk -F'\t' '{printf "%d. **#%s** `%s` — %s\n", NR, $1, $2, substr($3,1,60)}'
}

post_merge_sequence() {
  local pr_number="$1"
  local seq
  seq=$(merge_sequence)
  [ -z "$seq" ] && return 0
  gh pr comment "$pr_number" --body "### Stacked PR — merge in this order

$seq

Merge strictly top-to-bottom. Each PR shows only its own diff, and GitHub
retargets the next one to \`development\` automatically as its parent merges
(head branches are auto-deleted). Merging out of order will conflict." >/dev/null 2>&1 \
    && log "Posted merge sequence on PR #$pr_number."
}

# ---- Rate limit detection ---------------------------------------------------

# Parses Claude Code's own "limit resets at HH:MM" style message out of its output.
# Returns epoch seconds to sleep until, or empty if no limit was hit.
extract_reset_epoch() {
  local output="$1"
  local reset_str
  reset_str=$(echo "$output" | grep -ioE "resets? at [0-9:apm ]+" | head -1 | sed -E 's/resets? at //I')
  if [ -n "$reset_str" ]; then
    date -j -f "%I:%M%p" "$(echo "$reset_str" | tr -d ' ' | tr 'a-z' 'A-Z')" "+%s" 2>/dev/null || true
  fi
}

# Only trust the CLI's own error envelope. Grepping the whole transcript for
# "rate limit" false-positives on any issue that is *about* rate limiting — it
# threw away two successful KCH-19 runs on 2026-08-09 and slept 5h each time.
is_rate_limited() {
  local logfile="$1"
  [ -f "$logfile" ] || return 1
  [ "$(jq -r '.is_error // false' "$logfile" 2>/dev/null)" = "true" ] || return 1
  jq -r '.result // ""' "$logfile" 2>/dev/null \
    | grep -qiE "usage limit|rate limit|reached your.*limit"
}

# ---- Core: run one issue ----------------------------------------------------

run_issue() {
  local identifier="$1"
  log "Fetching $identifier from Linear..."
  local issue_json
  issue_json=$(resolve_issue "$identifier")
  local issue_uuid title description
  issue_uuid=$(echo "$issue_json" | jq -r '.id')
  title=$(echo "$issue_json" | jq -r '.title')
  description=$(echo "$issue_json" | jq -r '.description // ""')

  if [ -z "$issue_uuid" ] || [ "$issue_uuid" = "null" ]; then
    log "ERROR: could not resolve $identifier, skipping"
    return 2
  fi

  local team_key="${identifier%-*}"
  local branch="feature/$(echo "$identifier" | tr '[:upper:]' '[:lower:]')"

  cd "$REPO_DIR" || { log "ERROR: REPO_DIR not found"; return 2; }

  # Agents routinely leave uncommitted edits behind. Without wiping them the
  # checkout below fails, and since we don't run with `set -e` the script would
  # carry on and cut this issue's branch off the *previous* issue's branch.
  # `clean -fd` (no -x) leaves .git/info/exclude'd orchestrator files alone.
  git reset --hard -q
  git clean -fd -q

  # Stacked-PR base selection. If the previous issue's branch exists and has not
  # yet been merged into development, cut this branch from it so the agent can
  # actually import that (unmerged) work — and target the PR at it, so the diff
  # stays incremental. GitHub retargets the child PR to development automatically
  # when the parent merges.
  git fetch origin --prune --quiet
  local base_branch="development"
  local base_ref="origin/development"
  if [ -n "${LAST_BRANCH:-}" ] && git rev-parse --verify "origin/$LAST_BRANCH" >/dev/null 2>&1; then
    if git merge-base --is-ancestor "origin/$LAST_BRANCH" origin/development 2>/dev/null; then
      log "$identifier: dependency $LAST_BRANCH already merged into development; basing on development."
    else
      base_branch="$LAST_BRANCH"
      base_ref="origin/$LAST_BRANCH"
      log "$identifier: stacking on unmerged dependency $LAST_BRANCH."
    fi
  fi

  if ! git checkout -B "$branch" "$base_ref" --quiet; then
    log "ERROR: could not cut $branch from $base_ref. Skipping $identifier."
    return 2
  fi

  # Belt and braces: never run an agent on a branch we didn't mean to create.
  local on_branch
  on_branch=$(git rev-parse --abbrev-ref HEAD)
  if [ "$on_branch" != "$branch" ]; then
    log "ERROR: expected to be on $branch but HEAD is $on_branch. Skipping $identifier."
    return 2
  fi

  set_issue_state "$issue_uuid" "In Progress" "$team_key"
  comment_on_issue "$issue_uuid" "🤖 Automated agent starting work on this issue on branch \`$branch\`."

  # Contracts: what earlier agents shipped and are awaiting merge on. Downstream
  # agents build against these rather than rediscovering or reimplementing them.
  local contracts=""
  if [ -s "$CONTRACTS_FILE" ]; then
    contracts="
## Contracts from upstream agents (already on this branch)

The work below is committed on the branch you are on but is still awaiting human
review. Treat these as settled interfaces: import and build on them, do not
reimplement or refactor them.

$(cat "$CONTRACTS_FILE")
"
  fi

  local prompt="You are implementing Linear issue $identifier: $title

Description:
$description
$contracts
Implement this fully within the existing repo conventions. Write tests. Run the
test suite and linter before finishing. Do not touch files outside the scope of
this issue. Commit your work with a clear conventional-commit message referencing
$identifier. Do not push or open a PR yourself.

Local services available for integration tests: PostgreSQL on localhost:5432
(superuser '$(whoami)', create your own scratch database) and Redis on
localhost:6379. Prefer a real integration test over mocking when the issue is
about database or cache behaviour.

You do NOT have curl. To exercise HTTP routes use FastAPI's TestClient (or
httpx) from inside pytest — that is the better test anyway. Available CLI tools
include: pytest, ruff, mypy, python3, pip, uv, npm/npx/node, psql, pg_dump,
createdb/dropdb, redis-cli, pip-audit, gitleaks, trivy, git, gh, jq, docker.
There is no AWS CLI and no cloud credentials on this machine: for anything that
needs S3 or live AWS, write the scripts, Terraform and runbook, prove them
against local Postgres or a container, and say plainly in your summary what
could not be verified without cloud access. Do not fake a passing test.

Before you finish, append a short contract block to CONTRACT_OUT.md in the repo
root (do NOT commit that file) describing the public interface you are shipping:
module paths, key classes/functions with signatures, and any new DB tables or
columns. Keep it under 20 lines. Downstream agents will build against it."

  local logfile="$LOG_DIR/${identifier}_$(date +%s).json"
  log "Running claude -p for $identifier (branch $branch)..."

  claude -p "$prompt" \
    --output-format json \
    --allowedTools "$ALLOWED_TOOLS" \
    --permission-mode acceptEdits \
    --max-turns "$MAX_TURNS" \
    --max-budget-usd "$MAX_BUDGET_USD" \
    > "$logfile" 2>&1
  local exit_code=$?

  local output
  output=$(cat "$logfile")

  if is_rate_limited "$logfile"; then
    log "Hit usage limit while working on $identifier."
    local resume_epoch
    resume_epoch=$(extract_reset_epoch "$output")
    if [ -n "$resume_epoch" ]; then
      local now=$(date +%s)
      local wait_secs=$(( resume_epoch - now ))
      [ "$wait_secs" -lt 0 ] && wait_secs=$((5*3600))  # fallback: 5h
      log "Sleeping $((wait_secs/60)) minutes until window resets..."
      comment_on_issue "$issue_uuid" "⏸️ Agent paused: usage limit reached. Will auto-resume this issue after the credit window resets."
      sleep "$wait_secs"
    else
      log "Could not parse reset time, falling back to 5h sleep."
      sleep $((5*3600))
    fi
    return 1  # signal caller to retry this same issue
  fi

  if [ "$exit_code" -ne 0 ]; then
    log "$identifier failed (exit $exit_code). Log: $logfile"
    comment_on_issue "$issue_uuid" "⚠️ Automated agent run failed (exit code $exit_code). Needs human review. Log excerpt:\n\`\`\`\n$(echo "$output" | tail -30)\n\`\`\`"
    set_issue_state "$issue_uuid" "Backlog" "$team_key"
    return 2
  fi

  # Agents sometimes create a branch of their own and commit there. Pushing
  # "$branch" in that case ships whatever it happened to inherit, not the work.
  local head_after
  head_after=$(git rev-parse --abbrev-ref HEAD)
  if [ "$head_after" != "$branch" ]; then
    log "$identifier: agent left HEAD on '$head_after', not '$branch'. Not pushing."
    comment_on_issue "$issue_uuid" "⚠️ Agent committed on \`$head_after\` instead of \`$branch\`. Work is on the local machine but no PR was opened — needs a human."
    set_issue_state "$issue_uuid" "Backlog" "$team_key"
    return 2
  fi

  if ! git diff --quiet || ! git diff --cached --quiet; then
    log "$identifier: WARNING — agent left uncommitted changes; they will not be in the PR."
  fi

  # Success: push branch, open PR against the base we actually branched from,
  # do NOT merge. A stacked base means GitHub shows only the incremental diff and
  # retargets this PR to development once the parent merges.
  if git log "$base_ref".."$branch" --oneline | grep -q .; then
    git push -u origin "$branch" --quiet
    local stack_note=""
    [ "$base_branch" != "development" ] && stack_note="

> **Stacked PR.** Based on \`$base_branch\` ($LAST_ISSUE), which is still awaiting review.
> GitHub will retarget this to \`development\` automatically once the parent merges.
> Review the parent first."
    local pr_url
    pr_url=$(gh pr create --title "$identifier: $title" \
      --body "Automated implementation of $identifier by unattended agent. **Review before merge.**$stack_note" \
      --base "$base_branch" --head "$branch" 2>&1 | tail -1)
    post_merge_sequence "$(basename "$pr_url")"
    comment_on_issue "$issue_uuid" "✅ Agent finished. PR opened: $pr_url (base: \`$base_branch\`) — awaiting human review, will NOT auto-merge."
    set_issue_state "$issue_uuid" "In Review" "$team_key"
    echo "$identifier" >> "$STATE_FILE"
    log "$identifier done -> $pr_url"

    # Record the contract so later agents build on this instead of guessing.
    if [ -f "$REPO_DIR/CONTRACT_OUT.md" ]; then
      {
        echo ""
        echo "### $identifier — $title"
        echo "_Branch \`$branch\`, PR $pr_url, awaiting merge._"
        echo ""
        cat "$REPO_DIR/CONTRACT_OUT.md"
      } >> "$CONTRACTS_FILE"
      rm -f "$REPO_DIR/CONTRACT_OUT.md"
      log "$identifier: contract recorded for downstream agents."
    else
      log "$identifier: WARNING — agent shipped no CONTRACT_OUT.md; downstream agents get no interface summary."
    fi

    LAST_BRANCH="$branch"
    LAST_ISSUE="$identifier"
  else
    log "$identifier produced no commits — treating as incomplete, leaving in Backlog."
    set_issue_state "$issue_uuid" "Backlog" "$team_key"
    comment_on_issue "$issue_uuid" "⚠️ Agent run completed but produced no commits. Needs human look."
    return 2
  fi

  return 0
}

# ---- Main loop ---------------------------------------------------------------

log "Starting Aegis orchestrator. Repo: $REPO_DIR"

while IFS=$'\t' read -r tier identifier parallel; do
  [[ "$tier" =~ ^#.*$ || -z "$tier" ]] && continue
  grep -qx "$identifier" "$STATE_FILE" && { log "Skipping $identifier (already done)"; continue; }

  # Tier 2 is the FALSIFY kill gate — a failure here halts the run rather than
  # spending the remaining tiers' credits on a foundation that didn't hold up.
  if [ "$tier" = "2" ]; then
    log "*** Reached KILL GATE ($identifier). Loop STOPS here if it does not produce a PR. ***"
  fi

  attempts=0
  failed=0
  until run_issue "$identifier"; do
    rc=$?
    attempts=$((attempts+1))
    if [ "$rc" -eq 2 ]; then
      log "$identifier needs human review, moving on (not retrying automatically)."
      failed=1
      break
    fi
    if [ "$attempts" -ge 3 ]; then
      log "$identifier: rate-limited 3x in a row — something's off, stopping loop for human check."
      exit 1
    fi
    # rc == 1: rate limit, already slept inside run_issue, loop will retry
  done

  # A kill-gate agent that succeeds at proving the thesis wrong still exits 0 and
  # opens a PR, so exit status alone cannot be trusted here — read the verdict it
  # wrote. This is the case that slipped through on 2026-08-09 (KCH-7: PIVOT).
  if [ "$tier" = "2" ] && [ "$failed" -eq 0 ]; then
    verdict_file="$REPO_DIR/docs/falsification.md"
    if [ -f "$verdict_file" ] && grep -qiE "^\s*\*\*Verdict:\*\*\s*(PIVOT|KILL)" "$verdict_file"; then
      log "*** KILL GATE VERDICT = PIVOT/KILL ($identifier). The thesis did not hold. ***"
      failed=1
    elif [ ! -f "$verdict_file" ]; then
      log "*** KILL GATE ($identifier) wrote no docs/falsification.md — treating as failed. ***"
      failed=1
    else
      log "*** KILL GATE PASSED ($identifier). ***"
    fi
  fi

  if [ "$tier" = "2" ] && [ "$failed" -eq 1 ]; then
    log "*** KILL GATE FAILED ($identifier). Stopping before tiers 3+ spend more credits."
    log "*** Reassess, then re-run. Set SKIP_KILL_GATE=1 to override. ***"
    [ "${SKIP_KILL_GATE:-0}" = "1" ] || exit 1
  fi

done < "$QUEUE_FILE"

log "Queue exhausted. All tiers processed or flagged for review."