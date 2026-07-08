"""open_app / quit_app."""

from __future__ import annotations

import subprocess

from .. import osa


def open_app(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return "Error: no application name."
    proc = subprocess.run(
        ["open", "-a", name],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        return f"Error: could not open {name!r}: {(proc.stderr or '').strip()}"
    return f"Opened {name}."


def quit_app(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return "Error: no application name."
    try:
        osa.run_applescript(f'tell application "{name}" to quit')
    except osa.PermissionError_ as exc:
        return (
            f"Error: macOS blocked quitting {name!r} — Automation permission is "
            f"likely missing (System Settings › Privacy & Security › Automation). ({exc})"
        )
    except RuntimeError as exc:
        return f"Error: could not quit {name!r}: {exc}"
    return f"Quit {name}."
