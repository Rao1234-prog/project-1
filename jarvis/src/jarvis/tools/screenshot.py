"""take_screenshot — capture the screen and hand the image back to the model.

Returns a list of Anthropic content blocks (text + image) so Claude can actually
see and analyse the screenshot, not just a file path.
"""

from __future__ import annotations

import base64
import subprocess
import tempfile
from pathlib import Path

# Keep the encoded image well under the API's per-image limits.
_MAX_BYTES = 4_500_000


def take_screenshot() -> list[dict]:
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
        return [{"type": "text", "text": "Error: screencapture timed out."}]

    if proc.returncode != 0 or not tmp.exists():
        return [
            {
                "type": "text",
                "text": (
                    "Error: screenshot failed. macOS Screen Recording permission "
                    "may be missing (System Settings › Privacy & Security › "
                    f"Screen Recording). {(proc.stderr or '').strip()}"
                ),
            }
        ]

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
    return [
        {"type": "text", "text": "Screenshot captured."},
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": b64,
            },
        },
    ]
