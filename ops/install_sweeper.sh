#!/usr/bin/env bash
# Installs the hourly review sweeper as a launchd user agent.
#
# launchd, not cron, and deliberately so:
#   * cron has been deprecated on macOS since 2005, and under TCC it fails
#     silently when a job touches ~/Documents — exactly where this repo lives.
#   * cron jobs scheduled while the Mac is asleep simply never run.
#   * launchd with StartCalendarInterval fires on WAKE and coalesces the
#     intervals missed while asleep into a single run — so a laptop that sleeps
#     from 02:00 to 09:00 still sweeps its review debt on waking, once.
#     (StartInterval does not: kqueue drops those events.)
# The sweeper's own lock guards against the coalesced double-fire.
#
#   ./ops/install_sweeper.sh            # install + start
#   ./ops/install_sweeper.sh --uninstall
#   ./ops/install_sweeper.sh --status

set -uo pipefail

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$OPS_DIR/.." && pwd)"
LABEL="com.assetauditor.review-sweeper"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
ENV_FILE="$OPS_DIR/.env.local"

case "${1:-}" in
  --uninstall)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null
    rm -f "$PLIST"; echo "Uninstalled $LABEL"; exit 0 ;;
  --status)
    launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -E "state|last exit|runs" || echo "Not loaded."
    echo; "$OPS_DIR/review_sweeper.sh" --status; exit 0 ;;
esac

[ -f "$ENV_FILE" ] || { echo "FATAL: $ENV_FILE missing — the sweeper needs LINEAR_API_KEY and LINEAR_TEAM_KEY."; exit 1; }

mkdir -p "$HOME/Library/LaunchAgents" "$OPS_DIR/logs"

# The agent runs a login shell so PATH picks up claude, gh, coderabbit and uv the
# same way an interactive terminal does; launchd's own PATH is famously minimal.
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>cd '$REPO_DIR' &amp;&amp; source '$ENV_FILE' &amp;&amp; exec ./ops/review_sweeper.sh</string>
  </array>
  <!-- Top of every hour. StartCalendarInterval (not StartInterval) so a sweep
       missed while the Mac slept runs once on wake instead of being dropped. -->
  <key>StartCalendarInterval</key>
  <array>
$(for h in $(seq 0 23); do printf '    <dict><key>Hour</key><integer>%d</integer><key>Minute</key><integer>7</integer></dict>\n' "$h"; done)
  </array>
  <key>StandardOutPath</key><string>$OPS_DIR/logs/sweeper.out.log</string>
  <key>StandardErrorPath</key><string>$OPS_DIR/logs/sweeper.err.log</string>
  <key>WorkingDirectory</key><string>$REPO_DIR</string>
  <key>ProcessType</key><string>Background</string>
  <key>LowPriorityIO</key><true/>
  <key>Nice</key><integer>5</integer>
</dict>
</plist>
PLIST_EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl load "$PLIST" 2>/dev/null; then
  echo "Installed and loaded: $LABEL"
  echo "  runs   : every hour at :07 (and once on wake if hours were missed)"
  echo "  logs   : ops/logs/REVIEW_SWEEPER.md · ops/logs/sweeper.{out,err}.log"
  echo "  ledger : ./ops/review_sweeper.sh --status"
  echo "  stop   : ./ops/install_sweeper.sh --uninstall"
  echo
  echo "Run it once now to confirm it works:  ./ops/review_sweeper.sh"
else
  echo "WARN: could not load the agent. Check: launchctl print gui/$(id -u)/$LABEL"
  exit 1
fi
