"""run_shell — execute a shell command via subprocess.

Classification (SAFE read-only allowlist vs GUARDED) lives in safety.py. By the
time the handler runs, the call has already passed the gate.
"""

from __future__ import annotations

import subprocess

_TIMEOUT = 60.0
_MAX_OUTPUT = 8000  # characters returned to the model


def run_shell(command: str) -> str:
    command = (command or "").strip()
    if not command:
        return "Error: empty command."
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {_TIMEOUT:.0f}s."
    out = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
    out = out.strip() or "(no output)"
    if len(out) > _MAX_OUTPUT:
        out = out[:_MAX_OUTPUT] + "\n… (truncated)"
    return f"exit={proc.returncode}\n{out}"
