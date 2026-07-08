# JARVIS — macOS menu bar assistant — Build Plan

A JARVIS-style menu bar assistant for macOS (Apple Silicon, current macOS).
Type a request via a global hotkey; an LLM answers and can drive the Mac through
a small set of gated tools. Provider-agnostic: any OpenAI-compatible
chat-completions API (default Groq free tier). Architected so voice (wake word +
STT/TTS) can be bolted on later without a rewrite.

## Target machine
MacBook Air 15" 2024 (M3, Apple Silicon), current macOS. Python 3.11+.

## Non-goals for this build
No voice, no wake word, no always-on microphone. No telemetry. No network calls
other than the configured LLM provider's API.

## Module layout (`src/jarvis/`)
- `main.py`      — entry point: menu bar app (rumps), wiring, first-run checks.
- `config.py`    — load `~/.jarvis/config.toml`, resolve API key, defaults.
- `hotkey.py`    — global hotkey listener (pynput), configurable chord.
- `agent.py`     — OpenAI-compatible chat-completions API + tool loop, with 429
                   backoff and vision-model routing for screenshots.
                   **Input-source-agnostic**: one `handle_request(text)` entry
                   (typed today, transcription later). Rolling history (~10
                   exchanges).
- `safety.py`    — SAFE/GUARDED classifier + confirmation gate + action log.
- `ui.py`        — `respond(text)` (single output seam for TTS later), input
                   dialog, confirm dialog, notifications, scrollable output.
- `permissions.py` — detect Accessibility / Automation / Screen Recording;
                   point the user at the exact System Settings pane.
- `osa.py`       — low-level `osascript` helpers (dialogs, notifications).
- `tools/`       — one module per tool category:
  - `applescript.py` (run_applescript)
  - `shell.py` (run_shell)
  - `apps.py` (open_app, quit_app)
  - `system.py` (system_status, set_volume, set_brightness)
  - `files.py` (search_files via mdfind)
  - `screenshot.py` (take_screenshot → image back to the model)
  - `registry.py` — tool definitions + name→handler map.

## Abstraction seams for Tier 2 (voice)
- **Input**: `agent.handle_request(text)` takes a plain string. A Whisper
  transcription is just another string — no change to the loop.
- **Output**: everything user-facing goes through `ui.respond(text)`. Adding TTS
  = speak inside `respond()`; nothing else changes.

## Safety model
Every tool call is classified before execution:
- **SAFE** (read-only / trivially reversible): runs immediately.
  `system_status`, `take_screenshot`, `search_files`, `open_app`,
  `set_volume`, `set_brightness`, and read-only allowlisted `run_shell`.
- **GUARDED** (destructive / bulk / outbound / arbitrary): a native macOS
  confirmation dialog shows exactly what will run; executes only on approval.
  `run_applescript`, `quit_app`, non-allowlisted `run_shell`.
`run_shell` uses an **allowlist** of read-only commands + a metacharacter block —
anything else is GUARDED (safer than a denylist). Every executed action is
logged with a timestamp to `~/.jarvis/actions.log`. The model cannot bypass the
gate: classification happens in `safety.py`, between the model's tool request
and execution.

## Config (`~/.jarvis/config.toml`)
`hotkey`, `persona_file`, `log_level`, and a `[provider]` table: `name`,
`base_url`, `model`, optional `vision_model`, and `api_key` (env `JARVIS_API_KEY`
takes precedence). Switching providers (Groq → Gemini/OpenAI/local) is an
edit-and-restart of `[provider]`.

## Persona
`persona.md` (editable) holds the JARVIS register — addresses the user as "sir",
composed, dry, concise, leads with the outcome.

## Launch at login
launchd LaunchAgent `~/Library/LaunchAgents/com.omkaar.jarvis.plist`.
`install.sh` / `uninstall.sh` manage it. `jarvis` CLI: start / stop / restart /
logs / status.

## Build order (incremental — verify on the Mac between stages)
1. Menu bar icon + hotkey + hardcoded echo response.  ← verify hotkey fires
2. LLM API layer (OpenAI-compatible; persona + rolling history).
3. Tools.
4. Safety gate (SAFE/GUARDED + confirm dialog + action log).
5. launchd + install/uninstall + `jarvis` CLI.
6. README + Tier 2 upgrade notes.

> Note: this scaffold was authored in a Linux cloud session, which cannot run
> macOS menu-bar / hotkey / permission APIs. Each stage must be exercised on the
> target Mac. See README "Verifying on your Mac".
