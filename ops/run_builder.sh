#!/usr/bin/env bash
# launchd entry point for autonomous development.
#
# The orchestrator is one-shot: it walks the queue and exits. This wrapper is what
# makes development unattended — launchd calls it on a schedule, it decides whether
# a run is warranted, and starts one only if so. Guards, in order:
#   1. another builder already running        -> exit quietly
#   2. an issue currently in flight           -> exit quietly (worktree lock held)
#   3. nothing left in the queue              -> exit quietly
#   4. otherwise, continue the stack from the newest unmerged feature branch
#
# Reviews are handled independently by ops/review_sweeper.sh on its own schedule —
# a Claude spend cap stops development but never stops code review.

set -uo pipefail

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$OPS_DIR/.." && pwd)"
cd "$REPO_DIR" || exit 0

LOCK="$OPS_DIR/.builder.lock"
LOG="$OPS_DIR/logs/builder.log"
mkdir -p "$OPS_DIR/logs"
say() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

remaining_count() {
  comm -23 \
    <(grep -vE '^[[:space:]]*#|^[[:space:]]*$' "$OPS_DIR/queue.tsv" 2>/dev/null | cut -f2 | sort) \
    <(sort "$OPS_DIR/.completed_issues" 2>/dev/null) | grep -c . || true
}

# Read-only. Touches no lock, starts nothing.
if [ "${1:-}" = "--status" ]; then
  echo "remaining issues : $(remaining_count)"
  if [ -d "$LOCK" ]; then
    echo "builder          : RUNNING (lock age $(( $(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || echo 0) ))s)"
  else
    echo "builder          : idle"
  fi
  [ -d "$OPS_DIR/.worktree.lock" ] && echo "worktree         : HELD (an issue or sweep is in flight)" \
                                   || echo "worktree         : free"
  echo "last log lines   :"
  tail -5 "$LOG" 2>/dev/null | sed 's/^/    /' || echo "    (no log yet)"
  exit 0
fi

# 1. one builder at a time. A run can legitimately last hours, so only reclaim a
#    lock old enough that the process behind it cannot plausibly be alive.
if ! mkdir "$LOCK" 2>/dev/null; then
  age=$(( $(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || echo 0) ))
  if [ "$age" -gt 21600 ]; then
    say "Reclaiming stale builder lock (${age}s)."
    rm -rf "$LOCK"; mkdir "$LOCK" 2>/dev/null || exit 0
  else
    say "Builder already running (lock age ${age}s). Skipping."; exit 0
  fi
fi
trap 'rm -rf "$LOCK" 2>/dev/null' EXIT INT TERM

# 2. never start while an issue is mid-flight in the shared worktree.
if [ -d "$OPS_DIR/.worktree.lock" ]; then
  say "An issue is in flight (worktree lock held). Skipping."; exit 0
fi

# 3. is there anything left to build?
remaining=$(remaining_count)
if [ "${remaining:-0}" -eq 0 ]; then
  say "Queue exhausted — nothing to build."; exit 0
fi

# 4. continue the existing stack. Branches are cut from the newest unmerged feature
#    branch so unmerged upstream work is inherited; if everything has been merged,
#    the orchestrator falls back to development on its own.
top=$(git for-each-ref --sort=-committerdate --format='%(refname:short)' \
        refs/remotes/origin/feature/ 2>/dev/null | head -1 | sed 's|^origin/||')
if [ -n "${top:-}" ] && ! git merge-base --is-ancestor "origin/$top" origin/development 2>/dev/null; then
  export SEED_LAST_BRANCH="$top"
  export SEED_LAST_ISSUE="$(echo "$top" | sed 's|feature/||' | tr '[:lower:]' '[:upper:]')"
  say "Stacking on $SEED_LAST_BRANCH ($SEED_LAST_ISSUE)."
fi

# shellcheck disable=SC1090
[ -f "$OPS_DIR/.env.local" ] && . "$OPS_DIR/.env.local"

say "Starting orchestrator — $remaining issue(s) remaining."
./ops/orchestrator.sh >> "$LOG" 2>&1
rc=$?
say "Orchestrator exited with $rc."
exit 0
