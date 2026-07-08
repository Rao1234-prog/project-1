"""system_status / set_volume / set_brightness."""

from __future__ import annotations

import shutil
import subprocess

from .. import osa


def _run(args: list[str], timeout: float = 15.0) -> str:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return f"(error: {exc})"
    return (proc.stdout or proc.stderr or "").strip()


def _battery() -> str:
    raw = _run(["pmset", "-g", "batt"])
    # e.g. "... 46%; discharging; 2:03 remaining ..."
    for line in raw.splitlines():
        if "%" in line:
            return line.strip().lstrip("-").strip()
    return raw or "unknown"


def _wifi() -> str:
    ssid = _run(["networksetup", "-getairportnetwork", "en0"])
    # "Current Wi-Fi Network: MyNet" or "You are not associated with an AirPort network."
    if ":" in ssid:
        return ssid.split(":", 1)[1].strip()
    return ssid or "unknown"


def _disk() -> str:
    raw = _run(["df", "-h", "/"])
    lines = raw.splitlines()
    if len(lines) >= 2:
        cols = lines[1].split()
        if len(cols) >= 4:
            return f"{cols[3]} free of {cols[1]}"
    return raw or "unknown"


def _volume() -> str:
    try:
        out = osa.run_applescript("output volume of (get volume settings)")
        return f"{out}%"
    except Exception:
        return "unknown"


def system_status() -> str:
    return (
        f"Battery: {_battery()}\n"
        f"Wi-Fi: {_wifi()}\n"
        f"Disk: {_disk()}\n"
        f"Volume: {_volume()}"
    )


def _clamp(level) -> int:
    try:
        n = int(round(float(level)))
    except (TypeError, ValueError):
        return -1
    return max(0, min(100, n))


def set_volume(level) -> str:
    n = _clamp(level)
    if n < 0:
        return "Error: level must be a number 0–100."
    try:
        osa.run_applescript(f"set volume output volume {n}")
    except Exception as exc:
        return f"Error: could not set volume: {exc}"
    return f"Volume set to {n}%."


def set_brightness(level) -> str:
    """Set display brightness 0–100.

    macOS has no built-in brightness CLI; this uses the optional `brightness`
    tool (`brew install brightness`). If it's absent we say so rather than fail
    silently.
    """
    n = _clamp(level)
    if n < 0:
        return "Error: level must be a number 0–100."
    binary = shutil.which("brightness")
    if not binary:
        return (
            "Error: brightness control needs the `brightness` CLI "
            "(`brew install brightness`). Volume control works without it."
        )
    proc = subprocess.run(
        [binary, f"{n / 100:.2f}"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if proc.returncode != 0:
        return f"Error: could not set brightness: {(proc.stderr or '').strip()}"
    return f"Brightness set to {n}%."
