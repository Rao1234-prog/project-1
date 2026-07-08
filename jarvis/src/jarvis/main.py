"""JARVIS menu bar app entry point.

Wires config, logging, permissions, the hotkey listener, and the rumps menu bar
item together. Requests run on a short-lived worker thread so the hotkey stays
responsive while a request is in flight.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from . import config as config_mod
from . import permissions, ui
from .agent import Agent
from .hotkey import HotkeyListener


def _repo_root() -> Path:
    # src/jarvis/main.py -> repo root is two levels up from the package dir.
    return Path(__file__).resolve().parents[2]


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(config_mod.APP_LOG, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def _hide_dock_icon() -> None:
    """LSUIElement behaviour when run as a plain script (no .app bundle)."""
    try:
        from AppKit import NSApplication  # type: ignore

        # NSApplicationActivationPolicyAccessory = 1 (menu bar only, no Dock).
        NSApplication.sharedApplication().setActivationPolicy_(1)
    except Exception:
        pass


def _first_run_permissions() -> None:
    rep = permissions.report()
    log = logging.getLogger("jarvis.main")
    log.info(permissions.summary_text(rep))
    miss = permissions.missing(rep)
    if not miss:
        return
    pretty = {
        "accessibility": "Accessibility (needed for the global hotkey)",
        "screen_recording": "Screen Recording (needed for screenshots)",
    }
    lines = "\n".join(f"  • {pretty.get(m, m)}" for m in miss)
    ui.info_dialog(
        "JARVIS needs a couple of macOS permissions before everything works:\n\n"
        f"{lines}\n\n"
        "I'll open System Settings to the first one. Enable JARVIS there, then "
        "quit and relaunch. Automation is granted the first time JARVIS controls "
        "an app.",
        title="JARVIS — permissions",
    )
    # Open the most important missing pane (accessibility first).
    order = ["accessibility", "screen_recording"]
    for name in order:
        if name in miss:
            permissions.open_pane(name)
            break


def build_app(agent: Agent, hotkey_label: str):
    import rumps

    class JarvisApp(rumps.App):
        def __init__(self):
            super().__init__("JARVIS", title="🤖", quit_button=None)
            self.menu = [
                rumps.MenuItem("Ask JARVIS…", callback=self.on_ask),
                None,
                rumps.MenuItem("Status", callback=self.on_status),
                rumps.MenuItem("Open action log", callback=self.on_open_log),
                None,
                rumps.MenuItem("Quit", callback=self.on_quit),
            ]

        def on_ask(self, _):
            _dispatch(agent)

        def on_status(self, _):
            ui.info_dialog(
                permissions.summary_text() + f"\n\nHotkey: {hotkey_label}",
                title="JARVIS — status",
            )

        def on_open_log(self, _):
            import subprocess

            subprocess.run(["open", str(config_mod.ACTIONS_LOG)], capture_output=True)

        def on_quit(self, _):
            rumps.quit_application()

    return JarvisApp()


def _dispatch(agent: Agent) -> None:
    """Prompt for a request and handle it on a worker thread."""

    def worker():
        text = ui.ask_for_request()
        if text:
            agent.handle_request(text)

    threading.Thread(target=worker, daemon=True).start()


def main() -> None:
    cfg = config_mod.load(repo_root=_repo_root())
    _setup_logging(cfg.log_level)
    log = logging.getLogger("jarvis.main")
    log.info("starting JARVIS (model=%s, hotkey=%s)", cfg.model, cfg.hotkey)

    _hide_dock_icon()
    _first_run_permissions()

    agent = Agent(cfg)

    listener = HotkeyListener(cfg.hotkey, on_activate=lambda: _dispatch(agent))
    try:
        listener.start()
    except Exception:
        log.exception("could not start hotkey listener (Accessibility permission?)")
        ui.info_dialog(
            "JARVIS couldn't start the global hotkey. This almost always means "
            "Accessibility permission is missing — enable JARVIS in System "
            "Settings › Privacy & Security › Accessibility, then relaunch. You can "
            "still use 'Ask JARVIS…' from the menu.",
            title="JARVIS — hotkey",
        )

    app = build_app(agent, cfg.hotkey)
    app.run()


if __name__ == "__main__":
    main()
