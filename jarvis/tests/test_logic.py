"""Pure-logic unit tests for JARVIS.

These exercise the parts that do NOT need macOS frameworks or the network:
the SAFE/GUARDED safety classifier, the hotkey chord parser, and the agent
tool loop (against a fake OpenAI-compatible client). They run on any OS.

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

from jarvis import agent as agent_mod
from jarvis import hotkey, safety, ui
from jarvis.agent import Agent
from jarvis.config import Config
from jarvis.safety import Level
from jarvis.tools import registry


def _cfg(vision_model: str | None = None) -> Config:
    return Config(
        provider="groq",
        base_url="https://api.groq.com/openai/v1",
        model="qwen/qwen3-32b",
        vision_model=vision_model,
        hotkey="opt+space",
        persona_file=Path("persona.md"),
        log_level="INFO",
        api_key="test-key",
    )


# -- fake OpenAI-compatible client ------------------------------------------
def _msg(content=None, tool_calls=None):
    return types.SimpleNamespace(content=content, tool_calls=tool_calls)


def _tool_call(id, name, arguments):
    return types.SimpleNamespace(
        id=id, function=types.SimpleNamespace(name=name, arguments=arguments)
    )


def _resp(message):
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


class _RateLimit(Exception):
    """Stand-in for openai.RateLimitError (an HTTP 429)."""

    status_code = 429

    def __init__(self, retry_after=None):
        super().__init__("429 Too Many Requests")
        if retry_after is not None:
            self.response = types.SimpleNamespace(headers={"retry-after": str(retry_after)})


class _ToolUseFailed(Exception):
    """Stand-in for openai.BadRequestError with Groq code 'tool_use_failed'.

    Raised (as an HTTP 400) when the model emits tool-call syntax the provider
    can't parse — a known intermittent Llama-on-Groq formatting glitch.
    """

    status_code = 400
    code = "tool_use_failed"

    def __init__(self):
        super().__init__(
            "400 tool_use_failed: '<function=run_shell{\"command\":\"rm x\"}</function>'"
        )


class _FakeCompletions:
    """Scripted responses returned in order; an Exception entry is raised."""

    def __init__(self, scripted):
        self._scripted = scripted
        self.calls = 0

    def create(self, **kw):
        item = self._scripted[min(self.calls, len(self._scripted) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return item


class _FakeClient:
    def __init__(self, scripted):
        self.chat = types.SimpleNamespace(completions=_FakeCompletions(scripted))


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


class TestOpenAIToolSchema(unittest.TestCase):
    def test_conversion_shape(self):
        tools = registry.openai_tools()
        self.assertEqual(len(tools), len(registry.TOOL_DEFINITIONS))
        for t in tools:
            self.assertEqual(t["type"], "function")
            fn = t["function"]
            self.assertIn("name", fn)
            self.assertIn("description", fn)
            self.assertIn("parameters", fn)  # JSON-schema object
            self.assertEqual(fn["parameters"]["type"], "object")


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
            _resp(_msg(content="One moment, sir.",
                       tool_calls=[_tool_call("t1", "system_status", "{}")])),
            _resp(_msg(content="Battery looks healthy, sir.")),
        ]
        agent = Agent(_cfg(), client=_FakeClient(scripted))
        agent.handle_request("how's my battery?")

        self.assertEqual(self._captured["out"], "Battery looks healthy, sir.")
        self.assertEqual(agent._client.chat.completions.calls, 2)
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
                _resp(_msg(tool_calls=[_tool_call(
                    "t1", "run_shell", '{"command": "rm -rf ~/Documents"}')])),
                _resp(_msg(content="Understood, sir.")),
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

    def test_rate_limit_backoff_then_success(self):
        # First two calls 429, third succeeds. Backoff must not crash and the
        # answer must come through. Neutralise the sleep so the test is fast.
        orig_sleep = agent_mod.time.sleep
        agent_mod.time.sleep = lambda s: None
        try:
            scripted = [
                _RateLimit(retry_after=1),
                _RateLimit(),
                _resp(_msg(content="All caught up, sir.")),
            ]
            agent = Agent(_cfg(), client=_FakeClient(scripted))
            agent.handle_request("status?")
        finally:
            agent_mod.time.sleep = orig_sleep

        self.assertEqual(self._captured["out"], "All caught up, sir.")
        self.assertEqual(agent._client.chat.completions.calls, 3)

    def test_rate_limit_exhausted_message(self):
        # Every call 429; after retries JARVIS surfaces a clean message, no crash.
        orig_sleep = agent_mod.time.sleep
        agent_mod.time.sleep = lambda s: None
        try:
            agent = Agent(_cfg(), client=_FakeClient([_RateLimit()]))
            agent.handle_request("status?")
        finally:
            agent_mod.time.sleep = orig_sleep

        self.assertIn("rate-limited", self._captured["out"].lower())
        # 1 initial + _MAX_RETRIES retries.
        self.assertEqual(agent._client.chat.completions.calls, agent_mod._MAX_RETRIES + 1)

    def test_tool_use_failed_retries_then_succeeds(self):
        # The model emits malformed tool syntax once; a re-request succeeds.
        # The safety gate is never involved — this fails inside the API call.
        orig_sleep = agent_mod.time.sleep
        agent_mod.time.sleep = lambda s: None
        try:
            scripted = [
                _ToolUseFailed(),
                _resp(_msg(content="All sorted, sir.")),
            ]
            agent = Agent(_cfg(), client=_FakeClient(scripted))
            agent.handle_request("delete a file")
        finally:
            agent_mod.time.sleep = orig_sleep

        self.assertEqual(self._captured["out"], "All sorted, sir.")
        self.assertEqual(agent._client.chat.completions.calls, 2)

    def test_tool_use_failed_exhausted_message(self):
        # Every attempt is malformed; JARVIS surfaces a clean in-character
        # message (never a raw exception, never a silent crash).
        orig_sleep = agent_mod.time.sleep
        agent_mod.time.sleep = lambda s: None
        try:
            agent = Agent(_cfg(), client=_FakeClient([_ToolUseFailed()]))
            agent.handle_request("delete a file")
        finally:
            agent_mod.time.sleep = orig_sleep

        out = self._captured["out"].lower()
        self.assertIn("hiccup", out)
        self.assertNotIn("traceback", out)
        self.assertNotIn("tool_use_failed", out)
        # 1 initial attempt + the tool-format retry budget.
        self.assertEqual(
            agent._client.chat.completions.calls,
            agent_mod._MAX_TOOL_FORMAT_RETRIES + 1,
        )

    def test_screenshot_no_vision_model_is_graceful(self):
        # Text-only provider: capture succeeds, analysis is declined cleanly.
        agent = Agent(_cfg(vision_model=None), client=_FakeClient([_resp(_msg(content="x"))]))
        img = {"type": "image", "path": "/tmp/jarvis_screenshot.png",
               "data_url": "data:image/png;base64,AAAA"}
        out = agent._tool_result_to_text("take_screenshot", img)
        self.assertIn("/tmp/jarvis_screenshot.png", out)
        self.assertIn("text-only", out)
        # Crucially, no vision call was made.
        self.assertEqual(agent._client.chat.completions.calls, 0)

    def test_screenshot_with_vision_model_describes(self):
        # A configured vision model gets a sub-call and its text is returned.
        agent = Agent(_cfg(vision_model="vision-x"),
                      client=_FakeClient([_resp(_msg(content="Safari is open on the desktop."))]))
        img = {"type": "image", "path": "/tmp/jarvis_screenshot.png",
               "data_url": "data:image/png;base64,AAAA"}
        out = agent._tool_result_to_text("take_screenshot", img)
        self.assertIn("Safari is open on the desktop.", out)
        self.assertEqual(agent._client.chat.completions.calls, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
