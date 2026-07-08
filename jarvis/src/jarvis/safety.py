"""The SAFE / GUARDED safety gate.

Every tool call is classified here, between the model's request and execution:

- SAFE   : read-only or trivially reversible. Runs immediately.
- GUARDED: destructive, bulk, outbound, or arbitrary. A native confirmation
           dialog shows exactly what will run; executes only on approval.

The model cannot bypass this — classification is done from the tool name and
arguments, not from anything the model says. Every executed (or denied) action is
appended to ~/.jarvis/actions.log with a timestamp.
"""

from __future__ import annotations

import logging
import shlex
from datetime import datetime
from enum import Enum

from . import ui
from .config import ACTIONS_LOG

log = logging.getLogger("jarvis.safety")


class Level(str, Enum):
    SAFE = "SAFE"
    GUARDED = "GUARDED"


# Tools whose every invocation is read-only / trivially reversible.
_ALWAYS_SAFE = {
    "system_status",
    "take_screenshot",
    "search_files",
    "open_app",
    "set_volume",
    "set_brightness",
}

# Tools that always require confirmation regardless of arguments.
#  - run_applescript: AppleScript can do anything (delete, send, reconfigure);
#    intent is too hard to parse safely, so always confirm.
#  - quit_app: can discard unsaved work; not trivially reversible.
_ALWAYS_GUARDED = {
    "run_applescript",
    "quit_app",
}

# run_shell is classified by content. Allowlist of read-only commands; anything
# else (or any shell chaining/redirection) is GUARDED. An allowlist is safer than
# a denylist — an unknown command is treated as dangerous by default.
_SAFE_SHELL_COMMANDS = {
    "ls", "pwd", "whoami", "id", "date", "uptime", "df", "cat", "head", "tail",
    "echo", "which", "ps", "hostname", "uname", "sw_vers", "printenv", "stat",
    "file", "wc", "sort", "uniq", "basename", "dirname", "true",
}

# Shell metacharacters that enable chaining/redirection/expansion. Their presence
# forces GUARDED even for an otherwise-allowlisted command.
_SHELL_META = set(";|&<>`$(){}*?!\n\\")


def _classify_shell(command: str) -> Level:
    command = command.strip()
    if not command:
        return Level.GUARDED
    if any(ch in command for ch in _SHELL_META):
        return Level.GUARDED
    try:
        tokens = shlex.split(command)
    except ValueError:
        return Level.GUARDED
    if not tokens:
        return Level.GUARDED
    return Level.SAFE if tokens[0] in _SAFE_SHELL_COMMANDS else Level.GUARDED


def classify(tool_name: str, tool_input: dict) -> tuple[Level, str]:
    """Return (level, human-readable description of exactly what will run)."""
    if tool_name == "run_shell":
        command = str(tool_input.get("command", ""))
        return _classify_shell(command), f"$ {command}"
    if tool_name == "run_applescript":
        script = str(tool_input.get("script", ""))
        return Level.GUARDED, "AppleScript:\n" + script
    if tool_name == "quit_app":
        name = str(tool_input.get("name", ""))
        return Level.GUARDED, f"Quit application: {name}"
    if tool_name in _ALWAYS_SAFE:
        return Level.SAFE, _describe_safe(tool_name, tool_input)
    if tool_name in _ALWAYS_GUARDED:
        return Level.GUARDED, f"{tool_name}({tool_input})"
    # Unknown tool: fail closed.
    return Level.GUARDED, f"{tool_name}({tool_input})"


def _describe_safe(tool_name: str, tool_input: dict) -> str:
    if tool_name == "open_app":
        return f"Open application: {tool_input.get('name', '')}"
    if tool_name == "set_volume":
        return f"Set volume to {tool_input.get('level', '')}"
    if tool_name == "set_brightness":
        return f"Set brightness to {tool_input.get('level', '')}"
    if tool_name == "search_files":
        return f"Search files: {tool_input.get('query', '')}"
    return tool_name


def gate(tool_name: str, tool_input: dict) -> tuple[bool, Level]:
    """Decide whether a tool call may run.

    Returns (allowed, level). SAFE runs immediately; GUARDED prompts the user.
    The decision is logged either way.
    """
    level, description = classify(tool_name, tool_input)
    if level is Level.SAFE:
        _log_action(tool_name, level, "auto", description)
        return True, level

    approved = ui.confirm(description)
    _log_action(tool_name, level, "approved" if approved else "denied", description)
    return approved, level


def _log_action(tool_name: str, level: Level, outcome: str, detail: str) -> None:
    """Append a timestamped line to ~/.jarvis/actions.log."""
    ts = datetime.now().isoformat(timespec="seconds")
    one_line = detail.replace("\n", "\\n")
    line = f"{ts} | {level.value:7} | {outcome:8} | {tool_name} | {one_line}\n"
    try:
        ACTIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with ACTIONS_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError as exc:
        log.error("could not write action log: %s", exc)
