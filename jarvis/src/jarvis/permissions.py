"""macOS permission detection and guidance.

Detects Accessibility (needed for the global hotkey), Screen Recording (needed
for screenshots), and points the user at the exact System Settings pane. There is
no clean preflight API for Automation — it is detected lazily on first use (see
osa.PermissionError_), so here we only surface guidance for it.

All framework imports are best-effort: on a machine without pyobjc frameworks the
checks return None ("unknown") rather than crashing.
"""

from __future__ import annotations

import logging
import subprocess

log = logging.getLogger("jarvis.permissions")

# System Settings deep links.
PANES = {
    "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    "automation": "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation",
    "screen_recording": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
}


def check_accessibility() -> bool | None:
    try:
        from ApplicationServices import AXIsProcessTrusted  # type: ignore

        return bool(AXIsProcessTrusted())
    except Exception:
        return None


def check_screen_recording() -> bool | None:
    try:
        from Quartz import CGPreflightScreenCaptureAccess  # type: ignore

        return bool(CGPreflightScreenCaptureAccess())
    except Exception:
        return None


def request_screen_recording() -> bool | None:
    """Trigger the Screen Recording prompt (first-run convenience)."""
    try:
        from Quartz import CGRequestScreenCaptureAccess  # type: ignore

        return bool(CGRequestScreenCaptureAccess())
    except Exception:
        return None


def open_pane(name: str) -> None:
    url = PANES.get(name)
    if not url:
        return
    subprocess.run(["open", url], capture_output=True, text=True)


def report() -> dict:
    """Return a dict of permission -> True/False/None (None = unknown)."""
    return {
        "accessibility": check_accessibility(),
        "screen_recording": check_screen_recording(),
        # Automation can't be preflighted; it prompts on first app-control use.
        "automation": None,
    }


def summary_text(rep: dict | None = None) -> str:
    rep = rep or report()

    def mark(v):
        return {True: "granted", False: "MISSING", None: "unknown (prompts on first use)"}[v]

    return (
        "macOS permissions:\n"
        f"  • Accessibility (hotkey):        {mark(rep['accessibility'])}\n"
        f"  • Screen Recording (screenshots): {mark(rep['screen_recording'])}\n"
        f"  • Automation (app control):       {mark(rep['automation'])}"
    )


def missing(rep: dict | None = None) -> list[str]:
    rep = rep or report()
    return [k for k, v in rep.items() if v is False]
