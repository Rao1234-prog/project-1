#!/usr/bin/env bash
# Install JARVIS: create the virtualenv, build a code-signed .app bundle, seed
# ~/.jarvis, and install the launchd LaunchAgent so JARVIS starts at login.
#
# Why a .app bundle instead of running python directly under launchd:
# macOS TCC (Accessibility, Screen Recording) can only durably attribute a grant
# to an app that has a stable, code-signed identity (a CFBundleIdentifier). A raw
# uv/CLI python binary is ad-hoc/linker-signed with Identifier "-", so it shows up
# greyed-out and unselectable in the System Settings privacy panes and never keeps
# a grant. The bundle here wraps a tiny compiled Mach-O launcher (bundle/launcher.c)
# signed as com.omkaar.jarvis; it forks the venv python as a CHILD so the signed
# process stays the "responsible process" and the python child inherits the grant.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JARVIS_DIR="$HOME/.jarvis"
LABEL="com.omkaar.jarvis"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
VENV="$REPO_DIR/.venv"
APP="$REPO_DIR/JARVIS.app"
APP_EXEC="$APP/Contents/MacOS/jarvis"

echo "==> JARVIS install"
echo "    repo:   $REPO_DIR"
echo "    venv:   $VENV"
echo "    bundle: $APP"

# 1. Virtualenv + dependencies (uv if available, else python venv).
#    --clear so a re-run cleanly rebuilds a stale/broken venv instead of failing.
if command -v uv >/dev/null 2>&1; then
  echo "==> Creating venv with uv"
  uv venv --python 3.11 --clear "$VENV"
  uv pip install --python "$VENV/bin/python" -r "$REPO_DIR/requirements.txt"
else
  echo "==> uv not found; using python3 venv"
  python3 -m venv --clear "$VENV"
  "$VENV/bin/python" -m pip install --upgrade pip
  "$VENV/bin/python" -m pip install -r "$REPO_DIR/requirements.txt"
fi
PYTHON="$VENV/bin/python"

# 2. Build the code-signed .app bundle.
#    The launcher is path-relative (it derives the repo from its own location),
#    so the bundle is relocatable and contains no hard-coded user paths.
echo "==> Building $APP"
mkdir -p "$APP/Contents/MacOS"
cat > "$APP/Contents/Info.plist" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>JARVIS</string>
    <key>CFBundleDisplayName</key>
    <string>JARVIS</string>
    <key>CFBundleIdentifier</key>
    <string>${LABEL}</string>
    <key>CFBundleExecutable</key>
    <string>jarvis</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>LSMinimumSystemVersion</key>
    <string>13.0</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSPrincipalClass</key>
    <string>NSApplication</string>
</dict>
</plist>
PLIST_EOF

echo "==> Compiling launcher (bundle/launcher.c)"
cc -O2 -Wall -o "$APP_EXEC" "$REPO_DIR/bundle/launcher.c"

echo "==> Code-signing bundle as ${LABEL}"
codesign --force --deep --sign - --identifier "$LABEL" "$APP"
codesign -dv "$APP" 2>&1 | grep -E "Identifier|Signature" || true

# 3. Seed ~/.jarvis (config.toml + persona.md) without overwriting existing files.
echo "==> Seeding $JARVIS_DIR"
mkdir -p "$JARVIS_DIR"
[ -f "$JARVIS_DIR/config.toml" ] || cp "$REPO_DIR/config.example.toml" "$JARVIS_DIR/config.toml"
[ -f "$JARVIS_DIR/persona.md" ]  || cp "$REPO_DIR/persona.md"          "$JARVIS_DIR/persona.md"

# 4. Install the `jarvis` CLI onto PATH if we can.
if [ -w /usr/local/bin ]; then
  ln -sf "$REPO_DIR/bin/jarvis" /usr/local/bin/jarvis
  echo "==> Linked jarvis CLI -> /usr/local/bin/jarvis"
else
  echo "==> /usr/local/bin not writable; add $REPO_DIR/bin to your PATH to use 'jarvis'"
fi

# 5. Write the LaunchAgent plist. It runs the signed bundle executable (NOT python
#    directly) so TCC attributes Accessibility/Screen Recording to the bundle.
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
        <string>${APP_EXEC}</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
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

# 6. Load it.
echo "==> Loading LaunchAgent"
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

cat <<'DONE'

==> Done.

Next steps:
  1. Set your provider API key in ~/.jarvis/config.toml under [provider]
     (e.g. a Groq key from https://console.groq.com/keys). launchd can't read
     your shell env, so the config file is the reliable place.
  2. Grant permissions in System Settings > Privacy & Security:
       - Accessibility   (global hotkey)
       - Screen Recording (screenshots)
     Add JARVIS.app via the "+" button (Cmd-Shift-G, paste the repo's
     JARVIS.app path). Automation is requested the first time JARVIS controls
     an app. If you rebuild the bundle, remove and re-add JARVIS in these panes
     so TCC records the new signature.
  3. Look for the icon in your menu bar. Press Opt + Space to ask.

Manage it with:  jarvis start | stop | restart | logs | status
DONE
