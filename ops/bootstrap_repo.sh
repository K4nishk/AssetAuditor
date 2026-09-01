#!/usr/bin/env bash
# One-time repo bootstrap for AssetAuditor.
#
# Creates the git repo, commits the planning baseline, establishes main and
# development, pushes to GitHub, and turns on branch protection so nothing can
# reach development without a reviewed pull request — including the overnight
# agents, which never merge anything by design.
#
#   main         production-deployment code
#   development  demoable MVP; the only merge target for feature work
#   feature/<linear-issue>   agent branches, stacked, deleted after merge
#
# Safe to re-run: every step is guarded.

set -uo pipefail

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$OPS_DIR/.." && pwd)"
REPO_NAME="${REPO_NAME:-AssetAuditor}"
VISIBILITY="${VISIBILITY:-public}"   # CLARIFICATIONS Q15: public, mock data only

log() { echo "[bootstrap] $*"; }
cd "$REPO_DIR" || exit 1

command -v gh >/dev/null || { log "FATAL: gh CLI not found"; exit 1; }
gh auth status >/dev/null 2>&1 || { log "FATAL: gh not authenticated. Run: gh auth login"; exit 1; }

# ---- .gitignore -------------------------------------------------------------
# Orchestrator state must be ignored, not tracked: the run does `git clean -fd`
# between issues, which spares ignored files but deletes untracked ones. Losing
# .completed_issues mid-run would replay finished issues onto new branches.
if [ ! -f .gitignore ]; then
  cat > .gitignore <<'EOF'
# --- orchestrator state (must stay ignored: `git clean -fd` runs between issues) ---
ops/logs/
ops/.completed_issues
ops/.agent_contracts.md
ops/.issue_map.tsv
ops/queue.tsv
CONTRACT_OUT.md
CR_DISPUTE.md
MEDIATION_OUT.json

# --- python ---
__pycache__/
*.py[cod]
.venv/
.pytest_cache/
.ruff_cache/
.mypy_cache/

# --- node ---
node_modules/
dist/
.vite/

# --- env & secrets ---
.env
.env.local
*.pem

# --- os ---
.DS_Store
EOF
  log "Wrote .gitignore"
else
  log ".gitignore exists — leaving it alone (ensure the ops/ entries are present)"
fi

# ---- git init + baseline commit on main -------------------------------------
if [ ! -d .git ]; then
  git init -q -b main
  log "Initialised git repo on main"
fi

# This machine has no global git identity, so the first commit below would abort
# with "Please tell me who you are". Set it repo-locally rather than touching the
# user's global config; override by exporting GIT_AUTHOR_NAME / GIT_AUTHOR_EMAIL.
if ! git config --get user.email >/dev/null 2>&1; then
  git config user.name  "${GIT_AUTHOR_NAME:-$(gh api user --jq .login 2>/dev/null || echo K4nishk)}"
  git config user.email "${GIT_AUTHOR_EMAIL:-chawlas.k620@gmail.com}"
  log "Set repo-local git identity: $(git config --get user.name) <$(git config --get user.email)>"
fi

git add -A
if git diff --cached --quiet; then
  log "Nothing new to commit"
else
  git commit -q -m "chore: planning baseline — ADRs, vault, mvp.md, fixtures, wireframes, ops

Planning phase output as approved 2026-08-31: ADR v1.1.0 architecture of record,
Obsidian vault, Linear-method issue list, institution fixtures with golden numbers,
wireframes and templates, e2e testing skill, and the overnight orchestration suite."
  log "Committed planning baseline"
fi

# ---- GitHub remote ----------------------------------------------------------
if ! git remote get-url origin >/dev/null 2>&1; then
  log "Creating GitHub repo ($VISIBILITY)..."
  gh repo create "$REPO_NAME" "--$VISIBILITY" --source=. --remote=origin --push \
    --description "Canada-first finances & portfolio auditor — provenance-tracked ETL, contribution rooms, diversification dashboards." \
    || { log "FATAL: gh repo create failed"; exit 1; }
else
  log "origin already set: $(git remote get-url origin)"
  git push -u origin main --quiet || log "WARN: could not push main"
fi

# ---- development branch -----------------------------------------------------
if git rev-parse --verify origin/development >/dev/null 2>&1; then
  log "origin/development already exists"
else
  git checkout -q -B development main
  git push -u origin development --quiet && log "Created and pushed development"
fi
git checkout -q development

# ---- branch protection ------------------------------------------------------
# Human review is the only merge gate. The orchestrator never merges; these rules
# make that structural rather than a matter of the script behaving itself.
protect() {
  local branch="$1" reviews="$2"
  gh api -X PUT "repos/{owner}/{repo}/branches/$branch/protection" \
    -H "Accept: application/vnd.github+json" \
    -F "required_pull_request_reviews[required_approving_review_count]=$reviews" \
    -F "required_pull_request_reviews[dismiss_stale_reviews]=true" \
    -F "enforce_admins=false" \
    -F "required_status_checks=null" \
    -F "restrictions=null" \
    -F "allow_force_pushes=false" \
    -F "allow_deletions=false" >/dev/null 2>&1 \
    && log "Protected $branch (>=$reviews approving review, no force-push, no deletion)" \
    || log "WARN: could not protect $branch — private repos need GitHub Pro for protection rules. Set it in Settings → Branches."
}
protect development 1
protect main 1

# Feature branches are deleted automatically once merged; stacked children get
# retargeted to development by GitHub as their parent merges.
gh api -X PATCH "repos/{owner}/{repo}" -F delete_branch_on_merge=true >/dev/null 2>&1 \
  && log "Enabled delete-branch-on-merge"

cat <<EOF

[bootstrap] Done.
  repo:        $(git remote get-url origin 2>/dev/null)
  branches:    main (production) · development (demoable MVP)
  protection:  PR + 1 approving review required on both

Next:
  1. export LINEAR_API_KEY=lin_api_...   LINEAR_TEAM_KEY=<team key>
  2. python3 ops/seed_linear.py            # dry run — check the parse
  3. python3 ops/seed_linear.py --apply    # create issues, generate ops/queue.tsv
  4. coderabbit auth login                 # the review gate needs this
  5. tmux new -s assetauditor && ./ops/orchestrator.sh
EOF
