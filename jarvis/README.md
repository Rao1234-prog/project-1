# JARVIS — a menu bar assistant for macOS

A small, always-there assistant that lives in your menu bar. Press a global
hotkey (⌥ + Space by default), type a request, and an LLM answers — and can act
on your Mac through a set of gated tools (run scripts, check status, open apps,
search files, take and analyse screenshots). Destructive actions require a native
confirmation before they run.

JARVIS is **provider-agnostic**: it talks to any OpenAI-compatible
chat-completions API. The default is [Groq](https://console.groq.com)'s free
tier (no credit card), but switching to Google Gemini's compat endpoint, OpenAI,
or a local server is a config edit — see [Configuration](#configuration--jarvisconfigtoml).

Built for Apple Silicon (tested target: MacBook Air 15" M3) on current macOS,
Python 3.11+. It is deliberately architected so **voice (wake word + STT/TTS)
can be added later without a rewrite** — see [Tier 2](#tier-2-adding-voice).

> **Tier 1 scope:** typed input only. No voice, no wake word, no always-on
> microphone, no telemetry. The only network call is to your configured LLM
> provider.

---

## What it can do

| Tool | What it does | Gate |
|------|--------------|------|
| `system_status` | Battery, Wi-Fi, free disk, volume | SAFE |
| `take_screenshot` | Capture the screen; the image is analysed by a vision model (if configured) | SAFE |
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
- build and code-sign **`JARVIS.app`** — a small bundle around a compiled Mach-O
  launcher (`bundle/launcher.c`) that gives JARVIS a stable, signable identity
  (`com.omkaar.jarvis`) macOS can attach permissions to (see
  [Permissions](#permissions));
- seed `~/.jarvis/config.toml` and `~/.jarvis/persona.md` (without overwriting
  existing files);
- link the `jarvis` CLI into `/usr/local/bin` (if writable);
- install and load the launchd LaunchAgent (pointing at `JARVIS.app`) so JARVIS
  starts at login.

### 2. Set your API key

Get a free Groq key at **https://console.groq.com/keys** (no credit card). Put
it in `~/.jarvis/config.toml` under `[provider]`:

```toml
[provider]
name = "groq"
base_url = "https://api.groq.com/openai/v1"
model = "qwen/qwen3-32b"
api_key = "gsk_..."
```

The config file is the reliable place because **launchd doesn't inherit your
shell environment** — a key exported in `~/.zshrc` is invisible to the
login-item instance. (For development you can instead export `JARVIS_API_KEY`,
which overrides the config value.) **Never commit a real key**; `~/.jarvis/` is
outside the repo, and the file is kept at mode `600`.

### 3. Grant permissions

First launch walks you through these; see [Permissions](#permissions) below.

### 4. Use it

Look for **🤖** in the menu bar. Press **⌥ + Space** (or menu → *Ask JARVIS…*),
type a request, hit **Ask**. Short answers appear as a notification; long ones in
a scrollable window.

### Running without launchd (for development)

With the key already in `~/.jarvis/config.toml`:

```bash
PYTHONPATH=src ./.venv/bin/python -m jarvis.main
```

(Or `export JARVIS_API_KEY=gsk_...` to override the config value for a session.)

---

## Configuration — `~/.jarvis/config.toml`

```toml
hotkey = "opt+space"          # opt/cmd/ctrl/shift + a key or "space"
persona_file = "~/.jarvis/persona.md"
log_level = "INFO"            # DEBUG | INFO | WARNING | ERROR

[provider]
name = "groq"                                     # free-text label
base_url = "https://api.groq.com/openai/v1"       # any OpenAI-compatible endpoint
model = "qwen/qwen3-32b"                          # must support tool/function calling
# vision_model = "meta-llama/llama-4-scout-17b-16e-instruct"  # optional; enables screenshots
api_key = "gsk_..."
```

- **hotkey** tokens: `opt`/`option`/`alt` (⌥), `cmd`/`command` (⌘),
  `ctrl`/`control` (⌃), `shift` (⇧), and any single character or `space`.
  e.g. `cmd+shift+j`.
- **persona** — edit `~/.jarvis/persona.md` to change JARVIS's voice. It is
  loaded verbatim as the system prompt.

### Choosing a model

The chat `model` **must support function/tool calling** or JARVIS can't drive
any tools. Groq's free catalogue changes over time — check the current list and
each model's capabilities before committing:

```bash
curl -s https://api.groq.com/openai/v1/models \
  -H "Authorization: Bearer $GROQ_KEY" | python3 -m json.tool
```

Prefer a large instruction-tuned model that both emits well-formed tool calls
**and** reliably chooses to call them. The default `qwen/qwen3-32b` was picked
after live testing on Groq: it produced valid tool calls on every attempt, while
`llama-3.3-70b-versatile` intermittently emitted malformed tool syntax (a `400
tool_use_failed`) and `openai/gpt-oss-120b` usually answered in prose instead of
invoking the tool. JARVIS retries a `tool_use_failed` a couple of times and then
reports a clean message rather than crashing, but a reliable model avoids the
problem in the first place. (Qwen3 is a reasoning model; Groq returns its
chain-of-thought in a separate field, so JARVIS's replies stay clean.)

`qwen/qwen3-32b` is text-only, so screenshots need a separate `vision_model` (a
multimodal model such as a Llama 4 Scout variant). If you leave `vision_model`
unset, JARVIS still *takes* screenshots but tells you visual analysis isn't
available rather than crashing.

### Switching providers

Point `base_url`/`model`/`api_key` at any OpenAI-compatible service. The example
config ships with a commented-out **Google Gemini** block
(`generativelanguage.googleapis.com/v1beta/openai`) — uncomment it, drop in a
[Google AI Studio](https://aistudio.google.com/apikey) key, and `jarvis restart`.
Gemini models are natively multimodal, so the same id works for `vision_model`.

### Rate limits & data privacy

Free tiers throttle. Groq's free tier is roughly **30 requests/minute** plus a
daily token cap (see your console for current numbers). JARVIS retries HTTP 429s
with short exponential backoff (honouring `Retry-After`) and, if still limited,
replies *"We're being rate-limited by the provider, sir — give it a moment"*
rather than erroring out.

> **Privacy note.** Your prompts — and any screenshot you ask JARVIS to
> analyse — are sent to your configured provider. Free tiers may retain or use
> submitted data to improve their services. Be mindful of what's on screen
> before asking JARVIS to look, and prefer a paid/enterprise tier or a local
> model if you handle sensitive material.

---

## Permissions

JARVIS asks for these the first time it needs them. If something isn't working,
menu → **Status** shows the current state.

| Permission | Needed for | System Settings pane |
|------------|-----------|----------------------|
| **Accessibility** | the global hotkey | Privacy & Security › Accessibility |
| **Screen Recording** | `take_screenshot` | Privacy & Security › Screen Recording |
| **Automation** | AppleScript control of apps | Privacy & Security › Automation |

If the hotkey doesn't fire, it's almost always Accessibility — grant it to
**JARVIS.app** in Privacy & Security › Accessibility (click **+**, press
**⌘⇧G**, paste the repo's `JARVIS.app` path, toggle it on), then restart the
service. Automation is requested the first time JARVIS controls a specific app;
JARVIS detects the denial and tells you which pane to open rather than crashing.

**Why a `.app` bundle (and not raw Python under launchd).** macOS TCC only
durably attaches Accessibility / Screen Recording to an app with a stable,
code-signed identity. A `uv`/CLI Python binary is ad-hoc, linker-signed with
Identifier `-`, so it appears **greyed-out and unselectable** in the privacy
panes and never keeps a grant. `install.sh` builds `JARVIS.app` — a compiled
Mach-O launcher signed as `com.omkaar.jarvis` — which `fork()`s the venv Python
as a *child* so the signed process stays the "responsible process" and the
Python child **inherits** the grant. This is why launchd points at the bundle,
not at Python directly.

> **If you rebuild the bundle** (re-run `install.sh`, edit `launcher.c`), its
> code signature changes and the old grant goes stale — the panes will show
> JARVIS but permissions read as missing. Fix: **remove** the JARVIS entry with
> **–** and **re-add** `JARVIS.app`, in both Accessibility and Screen Recording,
> then `jarvis restart`.

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
  agent.py       OpenAI-compatible API + tool loop; rolling history; 429 backoff
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
   args matching its schema and returns a `str` (or, like `screenshot.py`, an
   image dict `{"type": "image", "data_url": ...}` the agent routes to a vision
   model).
2. **Declare it** in `src/jarvis/tools/registry.py`: add an entry to
   `TOOL_DEFINITIONS` (name, a *prescriptive* description of when to call it, and
   an `input_schema`) and register the handler in `HANDLERS`.
3. **Classify it** in `src/jarvis/safety.py`: add the tool name to `_ALWAYS_SAFE`
   or `_ALWAYS_GUARDED`, or give it content-based classification in `classify()`
   (as `run_shell` does). If you forget, it defaults to GUARDED (fail-closed).
4. `jarvis restart`.

---

## Verifying on your Mac

This scaffold's pure logic (safety classifier, hotkey parser, the OpenAI-format
tool loop against a mocked client, 429 backoff, the no-vision screenshot path) is
unit-tested and runs on any OS, but the macOS integration must be exercised on
your machine. Recommended first run, matching the build stages:

1. `./install.sh`, set your Groq key in `~/.jarvis/config.toml`, then run in the
   foreground: `PYTHONPATH=src ./.venv/bin/python -m jarvis.main`.
2. Confirm **🤖** appears in the menu bar with no Dock icon.
3. Press **⌥ + Space** — confirm the input box appears (grant Accessibility if
   not, then relaunch).
4. Ask something simple ("what's my battery?") — confirm the notification.
5. Ask for something GUARDED ("delete a test file with rm") — confirm the
   approval dialog appears and shows the exact command, and that declining is
   respected. Check `~/.jarvis/actions.log`.
6. Ask "what's on my screen?" — confirm the Screen Recording prompt, then a
   description.

Then verify it as a **login service** (the way you'll actually run it):

7. `jarvis restart` to run under launchd, then grant **JARVIS.app** in both
   Accessibility and Screen Recording (see [Permissions](#permissions)). Confirm
   `stdout.log` reports both as `granted` — that proves the signed bundle's grant
   propagates to the Python child.
8. Press **⌥ + Space** and ask a question — confirm the reply arrives with no
   Terminal open. (On Groq's free tier a lone `429` can delay a multi-call answer
   by the backoff interval; a no-tool question like "introduce yourself" returns
   promptly.)
9. **KeepAlive:** `kill -9` the bundle process and confirm launchd respawns a
   single clean pair within a couple seconds (`jarvis status`), with no orphaned
   duplicate.
10. **At rest:** the idle process should sit at ~0% CPU (event-driven, no
    polling) and ~35 MB RSS — check in Activity Monitor.

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
