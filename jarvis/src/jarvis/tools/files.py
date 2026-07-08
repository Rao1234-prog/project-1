"""search_files — Spotlight search via mdfind, scoped to the home folder."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_MAX_RESULTS = 40


def search_files(query: str, folder: str | None = None) -> str:
    query = (query or "").strip()
    if not query:
        return "Error: empty query."

    home = Path.home().resolve()
    if folder:
        scope = Path(os.path.expanduser(folder)).resolve()
        # Keep the search inside the home folder.
        try:
            scope.relative_to(home)
        except ValueError:
            scope = home
        if not scope.exists():
            scope = home
    else:
        scope = home

    try:
        proc = subprocess.run(
            ["mdfind", "-onlyin", str(scope), query],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return "Error: search timed out."
    if proc.returncode != 0:
        return f"Error: {(proc.stderr or '').strip() or 'mdfind failed'}"

    results = [line for line in proc.stdout.splitlines() if line.strip()]
    if not results:
        return f"No files matching {query!r} under {scope}."
    shown = results[:_MAX_RESULTS]
    body = "\n".join(shown)
    suffix = "" if len(results) <= _MAX_RESULTS else f"\n… and {len(results) - _MAX_RESULTS} more"
    return f"{len(results)} match(es) under {scope}:\n{body}{suffix}"
