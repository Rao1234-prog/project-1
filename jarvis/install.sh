#!/usr/bin/env bash
# Install JARVIS: create the virtualenv, seed ~/.jarvis, and install the launchd
# LaunchAgent so JARVIS starts at login.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JARVIS_DIR="$HOME/.jarvis"
LABEL="com.omkaar.jarvis"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
VENV="$REPO_DIR/.venv"

echo "==> JARVIS install"
echo "    repo:   $REPO_DIR"
echo "    venv:   $VENV"

# 1. Virtualenv + dependencies (uv if available, else python venv).
if command -v uv >/dev/null 2>&1; then
  echo "==> Creating venv with uv"
  uv venv --python 3.11 "$VENV"
  uv pip install --python "$VENV/bin/python" -r "$REPO_DIR/requirements.txt"
else
  echo "==> uv not found; using python3 venv"
  python3 -m venv "$VENV"
  "$VENV/bin/python" -m pip install --upgrade pip
  "$VENV/bin/python" -m pip install -r "$REPO_DIR/requirements.txt"
fi
PYTHON="$VENV/bin/python"

# 2. Seed ~/.jarvis (config.toml + persona.md) without overwriting existing files.
echo "==> Seeding $JARVIS_DIR"
mkdir -p "$JARVIS_DIR"
[ -f "$JARVIS_DIR/config.toml" ] || cp "$REPO_DIR/config.example.toml" "$JARVIS_DIR/config.toml"
[ -f "$JARVIS_DIR/persona.md" ]  || cp "$REPO_DIR/persona.md"          "$JARVIS_DIR/persona.md"

# 3. Install the `jarvis` CLI onto PATH if we can.
if [ -w /usr/local/bin ]; then
  ln -sf "$REPO_DIR/bin/jarvis" /usr/local/bin/jarvis
  echo "==> Linked jarvis CLI -> /usr/local/bin/jarvis"
else
  echo "==> /usr/local/bin not writable; add $REPO_DIR/bin to your PATH to use 'jarvis'"
fi

# 4. Write the LaunchAgent plist with absolute paths.
echo "==> Writing $PLIST"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON}</string>
        <string>-m</string>
        <string>jarvis.main</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONPATH</key>
        <string>${REPO_DIR}/src</string>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ProcessType</key>
    <string>Interactive</string>
    <key>StandardOutPath</key>
    <string>${JARVIS_DIR}/stdout.log</string>
    <key>StandardErrorPath</key>
    <string>${JARVIS_DIR}/stderr.log</string>
</dict>
</plist>
PLIST_EOF

# 5. Load it.
echo "==> Loading LaunchAgent"
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

cat <<'DONE'

==> Done.

Next steps:
  1. Set your provider API key in ~/.jarvis/config.toml under [provider]
     (e.g. a Groq key from https://console.groq.com/keys). launchd can't read
     your shell env, so the config file is the reliable place.
  2. Grant permissions when prompted: Accessibility (hotkey), Screen Recording
     (screenshots). Automation is requested the first time JARVIS controls an app.
  3. Look for the 🤖 in your menu bar. Press ⌥ + Space to ask.

Manage it with:  jarvis start | stop | restart | logs | status
DONE
