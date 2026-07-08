"""Configuration loading for JARVIS.

Reads ~/.jarvis/config.toml (creating a default on first run). JARVIS talks to
any OpenAI-compatible chat-completions endpoint; the provider (name, base URL,
model, optional vision model, and API key) is configured in the [provider]
table so switching providers is an edit-and-restart operation.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

JARVIS_DIR = Path.home() / ".jarvis"
CONFIG_PATH = JARVIS_DIR / "config.toml"
PERSONA_PATH = JARVIS_DIR / "persona.md"
ACTIONS_LOG = JARVIS_DIR / "actions.log"
APP_LOG = JARVIS_DIR / "jarvis.log"

# Defaults target Groq's free tier (OpenAI-compatible). A vision-capable model is
# opt-in (many free text models can't see images); leave it unset to disable
# screenshot analysis gracefully rather than crash.
DEFAULT_PROVIDER = "groq"
DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "llama-3.3-70b-versatile"
DEFAULT_VISION_MODEL = ""  # empty = no vision model configured
DEFAULT_HOTKEY = "opt+space"
DEFAULT_LOG_LEVEL = "INFO"

# Written to ~/.jarvis/config.toml if none exists yet.
_DEFAULT_CONFIG_TOML = f"""\
# JARVIS configuration. See config.example.toml in the repo for all options.
hotkey = "{DEFAULT_HOTKEY}"
persona_file = "~/.jarvis/persona.md"
log_level = "{DEFAULT_LOG_LEVEL}"

# JARVIS uses any OpenAI-compatible chat-completions API. Switch providers by
# editing this block and restarting (`jarvis restart`).
[provider]
name = "{DEFAULT_PROVIDER}"
base_url = "{DEFAULT_BASE_URL}"
model = "{DEFAULT_MODEL}"
# vision_model = "meta-llama/llama-4-scout-17b-16e-instruct"  # optional; enables screenshot analysis
# api_key = "gsk_..."   # your Groq key from https://console.groq.com/keys

# To switch to Google Gemini's OpenAI-compatible endpoint later, comment out the
# [provider] block above and uncomment this one:
# [provider]
# name = "gemini"
# base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
# model = "gemini-2.0-flash"
# vision_model = "gemini-2.0-flash"   # Gemini models are natively multimodal
# api_key = "..."   # your Google AI Studio key
"""

# Fallback persona used only if persona.md is missing and the repo copy can't be
# found. The real, editable persona lives in ~/.jarvis/persona.md.
_FALLBACK_PERSONA = (
    "You are JARVIS, a personal assistant on the user's Mac. Address the user as "
    '"sir." Be composed, concise, and dry. Lead with the outcome.'
)


@dataclass
class Config:
    provider: str
    base_url: str
    model: str
    vision_model: str | None
    hotkey: str
    persona_file: Path
    log_level: str
    api_key: str | None

    def persona_text(self) -> str:
        try:
            return self.persona_file.read_text(encoding="utf-8").strip()
        except OSError:
            return _FALLBACK_PERSONA


def _expand(path_str: str) -> Path:
    return Path(os.path.expanduser(path_str)).resolve()


def ensure_dirs(repo_root: Path | None = None) -> None:
    """Create ~/.jarvis and seed config.toml / persona.md if absent.

    repo_root, when provided, is used to copy the repo's persona.md so the user
    starts with the full persona rather than the terse fallback.
    """
    JARVIS_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(_DEFAULT_CONFIG_TOML, encoding="utf-8")
    if not PERSONA_PATH.exists():
        seeded = None
        if repo_root is not None:
            candidate = repo_root / "persona.md"
            if candidate.exists():
                seeded = candidate.read_text(encoding="utf-8")
        PERSONA_PATH.write_text(seeded or _FALLBACK_PERSONA, encoding="utf-8")


def load(repo_root: Path | None = None) -> Config:
    ensure_dirs(repo_root)
    data: dict = {}
    try:
        with CONFIG_PATH.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        # Corrupt or unreadable config: fall back to defaults rather than crash.
        data = {}

    hotkey = str(data.get("hotkey", DEFAULT_HOTKEY))
    persona_file = _expand(str(data.get("persona_file", str(PERSONA_PATH))))
    log_level = str(data.get("log_level", DEFAULT_LOG_LEVEL)).upper()

    provider_cfg = data.get("provider") or {}
    provider = str(provider_cfg.get("name", DEFAULT_PROVIDER))
    base_url = str(provider_cfg.get("base_url", DEFAULT_BASE_URL))
    # `model` lives under [provider]; fall back to a legacy top-level key, then default.
    model = str(provider_cfg.get("model", data.get("model", DEFAULT_MODEL)))
    vision_model = str(provider_cfg.get("vision_model", DEFAULT_VISION_MODEL)).strip() or None

    # Env var wins (handy for dev); the config file is the reliable source under
    # launchd, which does not inherit your shell environment.
    api_key = os.environ.get("JARVIS_API_KEY") or provider_cfg.get("api_key") or None

    return Config(
        provider=provider,
        base_url=base_url,
        model=model,
        vision_model=vision_model,
        hotkey=hotkey,
        persona_file=persona_file,
        log_level=log_level,
        api_key=api_key,
    )
