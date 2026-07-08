"""Pure-logic unit tests for JARVIS.

These exercise the parts that do NOT need macOS frameworks or the network:
the SAFE/GUARDED safety classifier, the hotkey chord parser, and the agent
tool loop (against a fake Anthropic client). They run on any OS.

Run from the repo's jarvis/ directory:

    PYTHONPATH=src .venv/bin/python tests/test_logic.py

or with pytest if installed:

    PYTHONPATH=src .venv/bin/python -m pytest tests -q
"""

from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path

from jarvis import hotkey, safety, ui
from jarvis.agent import Agent
from jarvis.config import Config
from jarvis.safety import Level
from jarvis.tools import registry


def _cfg() -> Config:
    return Config(
        model="claude-sonnet-4-6",
        hotkey="opt+space",
        persona_file=Path("persona.md"),
        log_level="INFO",
        api_key=None,
    )


def _blk(**kw):
    return types.SimpleNamespace(**kw)


class _Resp:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason


class _FakeMessages:
    """Scripted responses: a list of _Resp returned in order."""

    def __init__(self, scripted):
        self._scripted = scripted
        self.calls = 0

    def create(self, **kw):
        resp = self._scripted[min(self.calls, len(self._scripted) - 1)]
        self.calls += 1
        return resp


class _FakeClient:
    def __init__(self, scripted):
        self.messages = _FakeMessages(scripted)


class TestShellClassification(unittest.TestCase):
    def check(self, command, expected):
        level, _ = safety.classify("run_shell", {"command": command})
        self.assertIs(level, expected, f"{command!r} -> {level}")

    def test_readonly_allowlist_is_safe(self):
        for c in ["ls -la ~", "df -h", "cat notes.txt", "ps aux", "whoami", "uname -a"]:
            self.check(c, Level.SAFE)

    def test_destructive_is_guarded(self):
        for c in ["rm -rf ~/Documents", "sudo reboot", "dd if=/dev/zero of=/dev/disk1"]:
            self.check(c, Level.GUARDED)

    def test_metacharacters_force_guarded(self):
        # Chaining / redirection / subshell must never be classified SAFE,
        # even when the leading command is on the allowlist.
        for c in ["ls; whoami", "ls && rm foo", "cat x > y", "echo $(rm foo)",
                  "cat a | sh", "ls `whoami`"]:
            self.check(c, Level.GUARDED)

    def test_empty_is_guarded(self):
        self.check("", Level.GUARDED)


class TestToolClassification(unittest.TestCase):
    def test_always_guarded(self):
        self.assertIs(safety.classify("run_applescript", {"script": "beep"})[0], Level.GUARDED)
        self.assertIs(safety.classify("quit_app", {"name": "Safari"})[0], Level.GUARDED)

    def test_always_safe(self):
        for name, inp in [
            ("open_app", {"name": "Safari"}),
            ("system_status", {}),
            ("set_volume", {"level": 30}),
            ("set_brightness", {"level": 50}),
            ("search_files", {"query": "x"}),
            ("take_screenshot", {}),
        ]:
            self.assertIs(safety.classify(name, inp)[0], Level.SAFE, name)

    def test_unknown_tool_fails_closed(self):
        self.assertIs(safety.classify("mystery", {})[0], Level.GUARDED)


class TestHotkeyParser(unittest.TestCase):
    def test_parsing(self):
        cases = {
            "opt+space": "<alt>+<space>",
            "cmd+shift+j": "<cmd>+<shift>+j",
            "⌥+space": "<alt>+<space>",  # ⌥
            "ctrl+alt+k": "<ctrl>+<alt>+k",
        }
        for raw, expected in cases.items():
            self.assertEqual(hotkey.to_pynput(raw), expected, raw)


class TestAgentLoop(unittest.TestCase):
    def setUp(self):
        # Redirect the action log to a temp file and capture responses.
        self._tmp = Path(tempfile.mkdtemp()) / "actions.log"
        self._orig_log = safety.ACTIONS_LOG
        safety.ACTIONS_LOG = self._tmp
        self._orig_respond = ui.respond
        self._captured = {}
        ui.respond = lambda text, title="JARVIS": self._captured.setdefault("out", text)
        # Stub tool execution so the loop is hermetic (no real subprocess).
        self._orig_execute = registry.execute
        registry.execute = lambda name, inp: f"stub result for {name}"

    def tearDown(self):
        safety.ACTIONS_LOG = self._orig_log
        ui.respond = self._orig_respond
        registry.execute = self._orig_execute

    def test_safe_tool_round_trip(self):
        scripted = [
            _Resp(
                [
                    _blk(type="text", text="One moment, sir."),
                    _blk(type="tool_use", id="t1", name="system_status", input={}),
                ],
                stop_reason="tool_use",
            ),
            _Resp([_blk(type="text", text="Battery looks healthy, sir.")], stop_reason="end_turn"),
        ]
        agent = Agent(_cfg(), client=_FakeClient(scripted))
        agent.handle_request("how's my battery?")

        self.assertEqual(self._captured["out"], "Battery looks healthy, sir.")
        self.assertEqual(agent._client.messages.calls, 2)
        self.assertEqual(len(agent._history), 2)  # one user + one assistant
        tail = self._tmp.read_text().strip().splitlines()[-1]
        self.assertIn("system_status", tail)
        self.assertIn("SAFE", tail)
        self.assertIn("auto", tail)

    def test_guarded_denied(self):
        # User clicks Cancel on the confirmation dialog.
        orig_confirm = ui.confirm
        ui.confirm = lambda desc, title="JARVIS — confirm": False
        try:
            scripted = [
                _Resp(
                    [_blk(type="tool_use", id="t1", name="run_shell",
                          input={"command": "rm -rf ~/Documents"})],
                    stop_reason="tool_use",
                ),
                _Resp([_blk(type="text", text="Understood, sir.")], stop_reason="end_turn"),
            ]
            agent = Agent(_cfg(), client=_FakeClient(scripted))
            agent.handle_request("delete my documents")
        finally:
            ui.confirm = orig_confirm

        self.assertEqual(self._captured["out"], "Understood, sir.")
        tail = self._tmp.read_text().strip().splitlines()[-1]
        self.assertIn("GUARDED", tail)
        self.assertIn("denied", tail)
        self.assertIn("rm -rf", tail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
