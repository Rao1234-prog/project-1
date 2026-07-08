"""Low-level osascript (AppleScript) helpers.

Everything that shows a native dialog or notification, or runs AppleScript, goes
through here so the rest of the app never shells out to osascript directly.
"""

from __future__ import annotations

import logging
import subprocess

log = logging.getLogger("jarvis.osa")

# osascript error text/return code seen when Automation permission is missing.
_PERMISSION_HINTS = ("not authorized", "not allowed", "-1743", "-10004")


class PermissionError_(RuntimeError):
    """Raised when osascript fails in a way that looks like a missing macOS
    Automation/Accessibility permission rather than a script error."""


def _run(args: list[str], timeout: float = 120.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_applescript(script: str, timeout: float = 120.0) -> str:
    """Run an AppleScript string, returning stdout. Raises on failure."""
    proc = _run(["osascript", "-e", script], timeout=timeout)
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        if any(h in err.lower() for h in _PERMISSION_HINTS):
            raise PermissionError_(err)
        raise RuntimeError(err or f"osascript exited {proc.returncode}")
    return (proc.stdout or "").strip()


def _quote(text: str) -> str:
    """Quote a Python string for embedding in an AppleScript string literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def display_notification(text: str, title: str = "JARVIS", subtitle: str = "") -> None:
    script = f"display notification {_quote(text)} with title {_quote(title)}"
    if subtitle:
        script += f" subtitle {_quote(subtitle)}"
    try:
        run_applescript(script)
    except Exception as exc:  # notifications must never crash the app
        log.warning("notification failed: %s", exc)


def display_dialog(
    text: str,
    title: str = "JARVIS",
    buttons: tuple[str, ...] = ("OK",),
    default_button: str | None = None,
    icon: str = "note",
) -> str | None:
    """Show a dialog. Returns the clicked button, or None if cancelled."""
    btns = "{" + ", ".join(_quote(b) for b in buttons) + "}"
    script = (
        f"display dialog {_quote(text)} with title {_quote(title)} "
        f"buttons {btns} with icon {icon}"
    )
    if default_button:
        script += f" default button {_quote(default_button)}"
    try:
        out = run_applescript(script)
    except RuntimeError:
        # User pressed Cancel (osascript exits non-zero with "User canceled").
        return None
    # Output looks like: "button returned:OK"
    for part in out.split(", "):
        if part.startswith("button returned:"):
            return part.split(":", 1)[1]
    return None


def input_text(prompt: str, title: str = "JARVIS", default: str = "") -> str | None:
    """Prompt for a line of text. Returns the text, or None if cancelled/empty."""
    script = (
        f"display dialog {_quote(prompt)} with title {_quote(title)} "
        f'default answer {_quote(default)} buttons {{"Cancel", "Ask"}} '
        'default button "Ask" with icon note'
    )
    try:
        out = run_applescript(script)
    except RuntimeError:
        return None  # cancelled
    answer = ""
    for part in out.split(", "):
        if part.startswith("text returned:"):
            answer = part.split(":", 1)[1]
    answer = answer.strip()
    return answer or None


def choose_from_list(lines: list[str], title: str = "JARVIS", prompt: str = "") -> None:
    """Show a scrollable list window (used for long responses)."""
    if not lines:
        return
    items = "{" + ", ".join(_quote(line) for line in lines) + "}"
    script = (
        f"choose from list {items} with title {_quote(title)} "
        f"with prompt {_quote(prompt)} OK button name \"Done\" "
        "cancel button name \"Close\" with empty selection allowed"
    )
    try:
        run_applescript(script)
    except Exception as exc:
        log.warning("choose from list failed: %s", exc)
