# JARVIS — a menu bar assistant for macOS

A small, always-there assistant that lives in your menu bar. Press a global
hotkey (⌥ + Space by default), type a request, and Claude answers — and can act
on your Mac through a set of gated tools (run scripts, check status, open apps,
search files, take and analyse screenshots). Destructive actions require a native
confirmation before they run.

Built for Apple Silicon (tested target: MacBook Air 15" M3) on current macOS,
Python 3.11+. It is deliberately architected so **voice (wake word + STT/TTS)
can be added later without a rewrite** — see [Tier 2](#tier-2-adding-voice).

> **Tier 1 scope:** typed input only. No voice, no wake word, no always-on
> microphone, no telemetry. The only network call is to the Anthropic API.

---

## What it can do

| Tool | What it does | Gate |
|------|--------------|------|
| `system_status` | Battery, Wi-Fi, free disk, volume | SAFE |
| `take_screenshot` | Capture the screen; the image is analysed by Claude | SAFE |
| `search_files` | Spotlight (`mdfind`) search, scoped to your home folder | SAFE |
| `open_app` | Launch/focus an app | SAFE |
| `set_volume` / `set_brightness` | Adjust output volume / display brightness | SAFE |
| `run_shell` | Shell command — **read-only allowlist runs immediately** | SAFE / GUARDED |
| `run_applescript` | Arbitrary AppleScript | GUARDED |
| `quit_app` | Quit an app | GUARDED |

**The safety gate.** Every tool call is classified before it runs
(`src/jarvis/safety.py`):

- **SAFE** — read-only or trivially reversible. Runs immediately.
- **GUARDED** — destructive, bulk, outbound, or arbitrary. A native macOS dialog
  shows *exactly* what will run and it executes only if you approve.

`run_shell` uses an **allowlist** of read-only commands (`ls`, `cat`, `df`, `ps`,
…) plus a shell-metacharacter block; anything else — chaining, redirection,
`rm`, `sudo`, network commands — is GUARDED. An allowlist is safer than a
denylist: an unrecognised command is treated as dangerous by default. The model
cannot bypass the gate — classification is done from the tool name and arguments,
not from anything the model says. Every executed or denied action is logged with
a timestamp to `~/.jarvis/actions.log`.

---

## Setup

### 1. Get the code and install

```bash
cd jarvis
./install.sh
```

`install.sh` will:
- create a virtualenv (`uv` if available, else `python3 -m venv`) in `./.venv`
  and install dependencies;
- seed `~/.jarvis/config.toml` and `~/.jarvis/persona.md` (without overwriting
  existing files);
- link the `jarvis` CLI into `/usr/local/bin` (if writable);
- install and load the launchd LaunchAgent so JARVIS starts at login.

### 2. Set your API key

The key is read from the `ANTHROPIC_API_KEY` environment variable first, then
from `~/.jarvis/config.toml`. **Never hardcode it.**

```bash
export ANTHROPIC_API_KEY=sk-ant-...      # e.g. add to ~/.zshrc
```

Because launchd doesn't inherit your shell environment, for launch-at-login the
most reliable option is to put the key in `~/.jarvis/config.toml`:

```toml
[anthropic]
api_key = "sk-ant-..."
```

### 3. Grant permissions

First launch walks you through these; see [Permissions](#permissions) below.

### 4. Use it

Look for **🤖** in the menu bar. Press **⌥ + Space** (or menu → *Ask JARVIS…*),
type a request, hit **Ask**. Short answers appear as a notification; long ones in
a scrollable window.

### Running without launchd (for development)

```bash
export ANTHROPIC_API_KEY=sk-ant-...
PYTHONPATH=src ./.venv/bin/python -m jarvis.main
```

---

## Configuration — `~/.jarvis/config.toml`

```toml
model = "claude-sonnet-4-6"   # any Claude model id
hotkey = "opt+space"          # opt/cmd/ctrl/shift + a key or "space"
persona_file = "~/.jarvis/persona.md"
log_level = "INFO"            # DEBUG | INFO | WARNING | ERROR

[anthropic]
# api_key = "sk-ant-..."      # prefer the env var; this is a fallback
```

- **hotkey** tokens: `opt`/`option`/`alt` (⌥), `cmd`/`command` (⌘),
  `ctrl`/`control` (⌃), `shift` (⇧), and any single character or `space`.
  e.g. `cmd+shift+j`.
- **persona** — edit `~/.jarvis/persona.md` to change JARVIS's voice. It is
  loaded verbatim as the system prompt.

---

## Permissions

JARVIS asks for these the first time it needs them. If something isn't working,
menu → **Status** shows the current state.

| Permission | Needed for | System Settings pane |
|------------|-----------|----------------------|
| **Accessibility** | the global hotkey | Privacy & Security › Accessibility |
| **Screen Recording** | `take_screenshot` | Privacy & Security › Screen Recording |
| **Automation** | AppleScript control of apps | Privacy & Security › Automation |

If the hotkey doesn't fire, it's almost always Accessibility — grant it to the
app running JARVIS (Terminal/your launcher, or the Python binary under launchd),
then relaunch. Automation is requested the first time JARVIS controls a specific
app; JARVIS detects the denial and tells you which pane to open rather than
crashing.

> `set_brightness` needs the optional `brightness` CLI: `brew install brightness`.
> Everything else uses only built-in macOS tools.

---

## Managing the service — the `jarvis` CLI

```
jarvis start      # load + start the LaunchAgent
jarvis stop       # stop + unload
jarvis restart    # reload (pick up config/code changes)
jarvis logs       # tail action log, app log, and stderr
jarvis status     # is it loaded?
```

Logs live in `~/.jarvis/`: `actions.log` (every gated action, timestamped),
`jarvis.log` (app log), `stdout.log` / `stderr.log` (launchd output).

Uninstall: `./uninstall.sh` (add `--purge` to also remove `~/.jarvis` and the
venv).

---

## Architecture

```
src/jarvis/
  main.py        entry point: menu bar (rumps), wiring, first-run permission check
  config.py      ~/.jarvis/config.toml + API-key resolution
  hotkey.py      global hotkey listener (pynput); chord-string parser
  agent.py       Anthropic API + tool loop; rolling history
  safety.py      SAFE/GUARDED classifier + confirmation gate + action log
  ui.py          respond(text) — the single output seam — + dialogs/notifications
  permissions.py detect Accessibility / Screen Recording; open the right pane
  osa.py         low-level osascript helpers
  tools/         one module per tool category + registry.py (defs + dispatch)
```

Design notes:
- **Near-zero idle footprint.** No polling loops — just the pynput hotkey
  listener and the rumps event loop, both event-driven.
- **Input is abstracted.** `Agent.handle_request(text)` takes a plain string.
- **Output is abstracted.** Everything user-facing goes through `ui.respond()`.
- **Rolling history.** The last ~10 exchanges are kept as clean user/assistant
  text (intermediate tool calls aren't persisted), so follow-ups work.

---

## Adding a new tool

1. **Write the handler** in a `src/jarvis/tools/` module. It receives keyword
   args matching its schema and returns a `str` (or a list of Anthropic content
   blocks, like `screenshot.py`, to hand back an image).
2. **Declare it** in `src/jarvis/tools/registry.py`: add an entry to
   `TOOL_DEFINITIONS` (name, a *prescriptive* description of when to call it, and
   an `input_schema`) and register the handler in `HANDLERS`.
3. **Classify it** in `src/jarvis/safety.py`: add the tool name to `_ALWAYS_SAFE`
   or `_ALWAYS_GUARDED`, or give it content-based classification in `classify()`
   (as `run_shell` does). If you forget, it defaults to GUARDED (fail-closed).
4. `jarvis restart`.

---

## Verifying on your Mac

This scaffold was authored in a Linux CI environment, which cannot run macOS
menu-bar / hotkey / permission APIs — so the pure logic (safety classifier,
hotkey parser, the tool loop against a mocked client) is unit-tested, but the
macOS integration must be exercised on your machine. Recommended first run,
matching the build stages:

1. `./install.sh`, set the key, then run in the foreground:
   `PYTHONPATH=src ./.venv/bin/python -m jarvis.main`.
2. Confirm **🤖** appears in the menu bar with no Dock icon.
3. Press **⌥ + Space** — confirm the input box appears (grant Accessibility if
   not, then relaunch).
4. Ask something simple ("what's my battery?") — confirm the notification.
5. Ask for something GUARDED ("delete a test file with rm") — confirm the
   approval dialog appears and shows the exact command, and that declining is
   respected. Check `~/.jarvis/actions.log`.
6. Ask "what's on my screen?" — confirm the Screen Recording prompt, then a
   description.

---

## Tier 2: adding voice

The two seams that make this a drop-in rather than a rewrite:

- **Input** — `Agent.handle_request(text)` already takes a string. A Whisper
  transcription is just another string. Add a wake-word listener that, on
  detection, records audio, transcribes it, and calls `handle_request(transcript)`
  — the agent/tool/safety path is untouched.
- **Output** — everything user-facing goes through `ui.respond(text)`. Add
  text-to-speech inside `respond()` and every answer is spoken; nothing else
  changes.

Suggested components for a future session:
- **Wake word:** [Porcupine](https://picovoice.ai/platform/porcupine/) (on-device,
  low-power). Run its listener alongside the hotkey listener; on wake, start
  capture. Keep it opt-in and clearly indicated — this is the first always-on
  microphone path, so gate it behind a config flag and a menu toggle.
- **STT:** [whisper.cpp](https://github.com/ggerganov/whisper.cpp) for on-device
  transcription (no audio leaves the Mac). Feed the transcript to
  `handle_request()`.
- **TTS:** start with macOS `say` (zero deps) inside `respond()`; swap for a
  neural TTS later. Add a config option to choose notification vs. speech vs.
  both.

New permission to plan for: **Microphone** (Privacy & Security › Microphone) —
extend `permissions.py` the same way Screen Recording is handled. Keep the
"no always-on microphone" default until the user explicitly enables wake word.
