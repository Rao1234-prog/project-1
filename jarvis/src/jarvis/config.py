"""Configuration loading for JARVIS.

Reads ~/.jarvis/config.toml (creating a default on first run) and resolves the
Anthropic API key from the environment or the config file.
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

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_HOTKEY = "opt+space"
DEFAULT_LOG_LEVEL = "INFO"

# Written to ~/.jarvis/config.toml if none exists yet.
_DEFAULT_CONFIG_TOML = f"""\
# JARVIS configuration. See config.example.toml in the repo for all options.
model = "{DEFAULT_MODEL}"
hotkey = "{DEFAULT_HOTKEY}"
persona_file = "~/.jarvis/persona.md"
log_level = "{DEFAULT_LOG_LEVEL}"

[anthropic]
# api_key = "sk-ant-..."   # prefer the ANTHROPIC_API_KEY environment variable
"""

# Fallback persona used only if persona.md is missing and the repo copy can't be
# found. The real, editable persona lives in ~/.jarvis/persona.md.
_FALLBACK_PERSONA = (
    "You are JARVIS, a personal assistant on the user's Mac. Address the user as "
    '"sir." Be composed, concise, and dry. Lead with the outcome.'
)


@dataclass
class Config:
    model: str
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

    model = str(data.get("model", DEFAULT_MODEL))
    hotkey = str(data.get("hotkey", DEFAULT_HOTKEY))
    persona_file = _expand(str(data.get("persona_file", str(PERSONA_PATH))))
    log_level = str(data.get("log_level", DEFAULT_LOG_LEVEL)).upper()

    # Env var wins; config value is a fallback only.
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        api_key = (data.get("anthropic") or {}).get("api_key") or None

    return Config(
        model=model,
        hotkey=hotkey,
        persona_file=persona_file,
        log_level=log_level,
        api_key=api_key,
    )
