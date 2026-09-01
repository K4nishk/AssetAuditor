#!/usr/bin/env bash
# Installs the autonomous build loop as a launchd user agent.
#
# launchd, not cron, and deliberately so:
#   * cron has been deprecated on macOS since 2005, and under TCC it fails
#     silently when a job touches ~/Documents — exactly where this repo lives.
#   * cron jobs scheduled while the Mac is asleep simply never run.
#   * launchd with StartCalendarInterval fires on WAKE and coalesces the
#     intervals missed while asleep into a single run — so a laptop that sleeps
#     from 02:00 to 09:00 still sweeps its review debt on waking, once.
#     (StartInterval does not: kqueue drops those events.)
# run_builder.sh's own lock guards against the coalesced double-fire.
#
#   ./ops/install_builder.sh            # install + start
#   ./ops/install_builder.sh --uninstall
#   ./ops/install_builder.sh --status

set -uo pipefail

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$OPS_DIR/.." && pwd)"
LABEL="com.assetauditor.builder"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
ENV_FILE="$OPS_DIR/.env.local"

case "${1:-}" in
  --uninstall)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null
    rm -f "$PLIST"; echo "Uninstalled $LABEL"; exit 0 ;;
  --status)
    launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -E "state|last exit|runs" || echo "Not loaded."
    echo; "$OPS_DIR/run_builder.sh" --status; exit 0 ;;
esac

[ -f "$ENV_FILE" ] || { echo "FATAL: $ENV_FILE missing — the builder needs LINEAR_API_KEY and LINEAR_TEAM_KEY."; exit 1; }

mkdir -p "$HOME/Library/LaunchAgents" "$OPS_DIR/logs"

# The agent runs a login shell so PATH picks up claude, gh, coderabbit and uv the
# same way an interactive terminal does; launchd's own PATH is famously minimal.
if ! cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>cd '$REPO_DIR' &amp;&amp; source '$ENV_FILE' &amp;&amp; exec ./ops/run_builder.sh</string>
  </array>
  <!-- Every 2 hours at :22. StartCalendarInterval (not StartInterval) so a build
       missed while the Mac slept runs once on wake instead of being dropped. -->
  <key>StartCalendarInterval</key>
  <array>
$(for h in 0 2 4 6 8 10 12 14 16 18 20 22; do printf '    <dict><key>Hour</key><integer>%d</integer><key>Minute</key><integer>22</integer></dict>\n' "$h"; done)
  </array>
  <key>StandardOutPath</key><string>$OPS_DIR/logs/builder.out.log</string>
  <key>StandardErrorPath</key><string>$OPS_DIR/logs/builder.err.log</string>
  <key>WorkingDirectory</key><string>$REPO_DIR</string>
  <key>ProcessType</key><string>Background</string>
  <key>LowPriorityIO</key><true/>
  <key>Nice</key><integer>5</integer>
</dict>
</plist>
PLIST_EOF
then
  echo "FATAL: could not write $PLIST — the agent was NOT installed."
  echo "       (If a previous run booted it out, it is now unloaded: reinstall from a"
  echo "        normal terminal, outside any sandbox.)"
  exit 1
fi

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if launchctl bootstrap "gui/$(id -u)" "$PLIST"; then
  echo "Installed and loaded: $LABEL"
  echo "  runs   : every 2 hours at :22 (and once on wake if slots were missed)"
  echo "  logs   : ops/logs/builder.log · ops/logs/NIGHT_REPORT.md"
  echo "  ledger : ./ops/run_builder.sh --status"
  echo "  stop   : ./ops/install_builder.sh --uninstall"
  echo
  echo "Run it once now to confirm it works:  ./ops/run_builder.sh"
else
  echo "WARN: could not load the agent. Check: launchctl print gui/$(id -u)/$LABEL"
  exit 1
fi
