"""run_applescript — run an AppleScript via osascript. Always GUARDED."""

from __future__ import annotations

from .. import osa


def run_applescript(script: str) -> str:
    script = (script or "").strip()
    if not script:
        return "Error: empty script."
    try:
        out = osa.run_applescript(script)
    except osa.PermissionError_ as exc:
        return (
            "Error: macOS blocked this AppleScript — Automation permission is "
            "likely missing. Open System Settings › Privacy & Security › "
            f"Automation and enable JARVIS. ({exc})"
        )
    except RuntimeError as exc:
        return f"Error: {exc}"
    return out or "(done)"
