"""User-facing input and output.

`respond(text)` is the single output seam for the whole app: short answers become
notifications, long ones a scrollable dialog. To add TTS later, speak inside this
function — nothing else changes.
"""

from __future__ import annotations

import logging

from . import osa

log = logging.getLogger("jarvis.ui")

# Above this length (or with several line breaks) we use a scrollable dialog
# instead of a one-shot notification.
_SHORT_LIMIT = 220


def respond(text: str, title: str = "JARVIS") -> None:
    """Deliver JARVIS's answer to the user. The single output seam (TTS goes here)."""
    text = (text or "").strip()
    if not text:
        return
    is_long = len(text) > _SHORT_LIMIT or text.count("\n") > 2
    if is_long:
        # choose-from-list gives a scrollable window; one line per item.
        lines: list[str] = []
        for raw in text.splitlines() or [text]:
            lines.extend(_wrap(raw, 100) or [""])
        osa.choose_from_list(lines, title=title, prompt="JARVIS")
    else:
        osa.display_notification(text, title=title)


def ask_for_request() -> str | None:
    """Open the input box the hotkey/menu triggers. Returns the typed text."""
    return osa.input_text("What can I do for you, sir?", title="JARVIS")


def confirm(command_description: str, title: str = "JARVIS — confirm") -> bool:
    """Native confirmation for a GUARDED action. Shows exactly what will run."""
    body = "JARVIS wants to run:\n\n" + command_description
    clicked = osa.display_dialog(
        body,
        title=title,
        buttons=("Cancel", "Approve"),
        default_button="Cancel",
        icon="caution",
    )
    return clicked == "Approve"


def notify(text: str, title: str = "JARVIS") -> None:
    osa.display_notification(text, title=title)


def info_dialog(text: str, title: str = "JARVIS") -> None:
    osa.display_dialog(text, title=title, buttons=("OK",), default_button="OK")


def _wrap(line: str, width: int) -> list[str]:
    if len(line) <= width:
        return [line]
    out: list[str] = []
    while len(line) > width:
        cut = line.rfind(" ", 0, width)
        if cut <= 0:
            cut = width
        out.append(line[:cut])
        line = line[cut:].lstrip()
    if line:
        out.append(line)
    return out
