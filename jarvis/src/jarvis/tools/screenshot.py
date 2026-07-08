"""take_screenshot — capture the screen and hand the image back to the agent.

On success this returns a provider-agnostic image dict::

    {"type": "image", "path": "...", "media_type": "image/png", "data_url": "data:image/png;base64,..."}

The agent decides what to do with it: route it to a vision model (as an OpenAI
`image_url` message) if one is configured, or report cleanly that visual
analysis isn't available on a text-only provider. On failure it returns a plain
string, which the agent relays as-is.
"""

from __future__ import annotations

import base64
import subprocess
import tempfile
from pathlib import Path

# Keep the encoded image well under typical per-image API limits.
_MAX_BYTES = 4_500_000


def take_screenshot() -> dict | str:
    tmp = Path(tempfile.gettempdir()) / "jarvis_screenshot.png"
    try:
        proc = subprocess.run(
            # -x: no capture sound. Full screen.
            ["screencapture", "-x", str(tmp)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return "Error: screencapture timed out."

    if proc.returncode != 0 or not tmp.exists():
        return (
            "Error: screenshot failed. macOS Screen Recording permission "
            "may be missing (System Settings › Privacy & Security › "
            f"Screen Recording). {(proc.stderr or '').strip()}"
        )

    data = tmp.read_bytes()
    if len(data) > _MAX_BYTES:
        # Downscale via sips (bundled on macOS) to stay within limits.
        subprocess.run(
            ["sips", "--resampleWidth", "1600", str(tmp)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        data = tmp.read_bytes()

    b64 = base64.standard_b64encode(data).decode("ascii")
    return {
        "type": "image",
        "path": str(tmp),
        "media_type": "image/png",
        "data_url": f"data:image/png;base64,{b64}",
    }
