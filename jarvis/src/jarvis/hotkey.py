"""Global hotkey listener (pynput).

Parses a human-friendly chord string ("opt+space", "cmd+shift+j") into pynput's
GlobalHotKeys format and invokes a callback when pressed.
"""

from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger("jarvis.hotkey")

# Human token -> pynput modifier/key token.
_MODIFIERS = {
    "opt": "<alt>", "option": "<alt>", "alt": "<alt>", "⌥": "<alt>",
    "cmd": "<cmd>", "command": "<cmd>", "⌘": "<cmd>",
    "ctrl": "<ctrl>", "control": "<ctrl>", "⌃": "<ctrl>",
    "shift": "<shift>", "⇧": "<shift>",
}
_NAMED_KEYS = {"space": "<space>", "enter": "<enter>", "return": "<enter>", "tab": "<tab>"}


def to_pynput(hotkey: str) -> str:
    """Convert e.g. 'opt+space' -> '<alt>+<space>' for GlobalHotKeys."""
    parts = [p.strip().lower() for p in hotkey.split("+") if p.strip()]
    out: list[str] = []
    for p in parts:
        if p in _MODIFIERS:
            out.append(_MODIFIERS[p])
        elif p in _NAMED_KEYS:
            out.append(_NAMED_KEYS[p])
        elif len(p) == 1:
            out.append(p)
        else:
            # Unknown token — pass through and let pynput report it.
            out.append(p)
    return "+".join(out)


class HotkeyListener:
    """Wraps a pynput GlobalHotKeys listener on a background thread."""

    def __init__(self, hotkey: str, on_activate: Callable[[], None]):
        self.hotkey = hotkey
        self.pynput_hotkey = to_pynput(hotkey)
        self._on_activate = on_activate
        self._listener = None

    def start(self) -> None:
        from pynput import keyboard  # imported lazily (macOS-only at runtime)

        def _fire():
            try:
                self._on_activate()
            except Exception:
                log.exception("hotkey callback failed")

        self._listener = keyboard.GlobalHotKeys({self.pynput_hotkey: _fire})
        self._listener.start()
        log.info("hotkey listening on %s (%s)", self.hotkey, self.pynput_hotkey)

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
