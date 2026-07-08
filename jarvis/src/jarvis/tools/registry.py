"""Tool definitions (sent to the model) and the name→handler map.

To add a tool: write a handler in a tools/ module, add its Anthropic tool
definition to TOOL_DEFINITIONS, register it in HANDLERS, and classify it in
safety.py. See README "Adding a new tool".
"""

from __future__ import annotations

from typing import Callable

from . import applescript, apps, files, screenshot, shell, system

# Anthropic tool definitions. Descriptions are prescriptive about *when* to call
# each tool — the model relies on them heavily.
TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "run_applescript",
        "description": (
            "Run an AppleScript on the Mac via osascript. Use for controlling "
            "applications, UI scripting, or anything AppleScript exposes. This "
            "always requires the user's confirmation before it runs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "The AppleScript source to run."}
            },
            "required": ["script"],
        },
    },
    {
        "name": "run_shell",
        "description": (
            "Run a shell command. Read-only commands (ls, cat, df, ps, …) run "
            "immediately; anything that modifies the system, chains commands, or "
            "reaches the network requires the user's confirmation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to run."}
            },
            "required": ["command"],
        },
    },
    {
        "name": "open_app",
        "description": "Open (launch or focus) a macOS application by name, e.g. 'Safari'.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Application name."}},
            "required": ["name"],
        },
    },
    {
        "name": "quit_app",
        "description": (
            "Quit a running macOS application by name. Requires the user's "
            "confirmation, since unsaved work could be lost."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Application name."}},
            "required": ["name"],
        },
    },
    {
        "name": "system_status",
        "description": (
            "Report system status: battery level/state, current Wi-Fi network, "
            "free disk space, and output volume. Call when the user asks about "
            "battery, storage, network, or overall status."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_volume",
        "description": "Set the system output volume to a level from 0 to 100.",
        "input_schema": {
            "type": "object",
            "properties": {
                "level": {"type": "integer", "description": "Volume 0–100.", "minimum": 0, "maximum": 100}
            },
            "required": ["level"],
        },
    },
    {
        "name": "set_brightness",
        "description": (
            "Set the display brightness to a level from 0 to 100. Requires the "
            "optional `brightness` CLI to be installed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "level": {"type": "integer", "description": "Brightness 0–100.", "minimum": 0, "maximum": 100}
            },
            "required": ["level"],
        },
    },
    {
        "name": "search_files",
        "description": (
            "Search for files with Spotlight (mdfind), scoped to the user's home "
            "folder. Use when the user asks to find a file or documents."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search text or Spotlight query."},
                "folder": {
                    "type": "string",
                    "description": "Optional subfolder of the home directory to limit the search to.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "take_screenshot",
        "description": (
            "Capture the screen and return the image for you to analyse. Use when "
            "the user asks what's on screen or to describe/inspect the display. "
            "Requires macOS Screen Recording permission."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
]

# name -> handler. Handlers take keyword args matching their input_schema and
# return either a str or a list of content blocks.
HANDLERS: dict[str, Callable] = {
    "run_applescript": lambda script="": applescript.run_applescript(script),
    "run_shell": lambda command="": shell.run_shell(command),
    "open_app": lambda name="": apps.open_app(name),
    "quit_app": lambda name="": apps.quit_app(name),
    "system_status": lambda: system.system_status(),
    "set_volume": lambda level=None: system.set_volume(level),
    "set_brightness": lambda level=None: system.set_brightness(level),
    "search_files": lambda query="", folder=None: files.search_files(query, folder),
    "take_screenshot": lambda: screenshot.take_screenshot(),
}


def execute(name: str, tool_input: dict):
    """Run a tool handler by name. Returns str or list-of-blocks."""
    handler = HANDLERS.get(name)
    if handler is None:
        return f"Error: unknown tool {name!r}."
    try:
        return handler(**(tool_input or {}))
    except TypeError as exc:
        return f"Error: bad arguments for {name}: {exc}"
    except Exception as exc:  # a tool crashing must not take down the agent
        return f"Error while running {name}: {exc}"
