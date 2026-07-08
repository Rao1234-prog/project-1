#!/usr/bin/env bash
# Uninstall JARVIS: unload and remove the LaunchAgent and the CLI symlink.
# Leaves ~/.jarvis (config, persona, logs) and the venv in place unless --purge.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JARVIS_DIR="$HOME/.jarvis"
LABEL="com.omkaar.jarvis"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

echo "==> Unloading LaunchAgent"
launchctl unload "$PLIST" 2>/dev/null || true
rm -f "$PLIST"

if [ -L /usr/local/bin/jarvis ]; then
  rm -f /usr/local/bin/jarvis
  echo "==> Removed /usr/local/bin/jarvis"
fi

if [ "${1:-}" = "--purge" ]; then
  echo "==> Purging $JARVIS_DIR and $REPO_DIR/.venv"
  rm -rf "$JARVIS_DIR" "$REPO_DIR/.venv"
else
  echo "==> Left ~/.jarvis and .venv in place. Re-run with --purge to remove them."
fi

echo "==> Done."
