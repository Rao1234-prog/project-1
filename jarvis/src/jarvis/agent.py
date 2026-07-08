"""The agent: Anthropic API call + tool loop, with the safety gate wired in.

Design seams for Tier 2 (voice):
  * Input  — `Agent.handle_request(text)` takes a plain string. A Whisper
             transcription is just another string; the loop is unchanged.
  * Output — everything user-facing goes through `ui.respond(text)`. Add TTS
             there and nothing else changes.
"""

from __future__ import annotations

import logging

from . import safety, ui
from .config import Config
from .tools import registry

log = logging.getLogger("jarvis.agent")

# Keep the last ~10 exchanges (user+assistant) so follow-ups have context.
_MAX_HISTORY_MESSAGES = 20
# Guard against a runaway tool loop within a single request.
_MAX_TOOL_ITERATIONS = 8
_MAX_TOKENS = 2048

_OPERATIONAL_NOTE = (
    "\n\nYou are running as a macOS menu bar assistant. You have tools to inspect "
    "and control the Mac. Some tools require the user's confirmation before they "
    "run; if the user declines, respect it and offer an alternative rather than "
    "trying to work around it. Keep simple answers short enough to read in a "
    "notification."
)


class Agent:
    def __init__(self, config: Config, client=None):
        self.config = config
        self._client = client  # injectable for testing
        self._history: list[dict] = []

    # -- public entry point (input source is abstracted to a string) ----------
    def handle_request(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        try:
            answer = self._run(text)
        except Exception as exc:  # never let a request crash the app
            log.exception("request failed")
            answer = f"Something went wrong, sir: {exc}"
        ui.respond(answer)

    # -- internals ------------------------------------------------------------
    def _client_or_error(self):
        if self._client is not None:
            return self._client
        if not self.config.api_key:
            raise RuntimeError(
                "no Anthropic API key. Set ANTHROPIC_API_KEY or add it to "
                "~/.jarvis/config.toml."
            )
        import anthropic  # imported lazily so the app starts without the key

        self._client = anthropic.Anthropic(api_key=self.config.api_key)
        return self._client

    def _system_prompt(self) -> str:
        return self.config.persona_text() + _OPERATIONAL_NOTE

    def _run(self, user_text: str) -> str:
        client = self._client_or_error()
        system = self._system_prompt()
        # Working message list = rolling history + this turn. Intermediate
        # tool_use/tool_result blocks stay local and are NOT persisted to history
        # (history holds only clean user/assistant text), which keeps follow-ups
        # simple and avoids tool-pairing hazards.
        messages = list(self._history) + [{"role": "user", "content": user_text}]

        final_text = ""
        for _ in range(_MAX_TOOL_ITERATIONS):
            response = client.messages.create(
                model=self.config.model,
                max_tokens=_MAX_TOKENS,
                system=system,
                tools=registry.TOOL_DEFINITIONS,
                messages=messages,
            )

            if response.stop_reason == "refusal":
                final_text = "I can't help with that one, sir."
                break

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            text_blocks = [b.text for b in response.content if b.type == "text"]
            if text_blocks:
                final_text = "\n".join(text_blocks).strip()

            if response.stop_reason != "tool_use" or not tool_uses:
                break

            # Preserve the assistant turn (incl. tool_use blocks) for the API.
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for tu in tool_uses:
                result_content = self._run_tool(tu.name, tu.input or {})
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": result_content,
                    }
                )
            messages.append({"role": "user", "content": tool_results})
        else:
            log.warning("hit tool-iteration cap")

        final_text = final_text or "Done, sir."
        self._remember(user_text, final_text)
        return final_text

    def _run_tool(self, name: str, tool_input: dict):
        """Apply the safety gate, then execute (or return a decline)."""
        allowed, level = safety.gate(name, tool_input)
        if not allowed:
            return f"The user declined to approve this {level.value.lower()} action."
        return registry.execute(name, tool_input)

    def _remember(self, user_text: str, assistant_text: str) -> None:
        self._history.append({"role": "user", "content": user_text})
        self._history.append({"role": "assistant", "content": assistant_text})
        if len(self._history) > _MAX_HISTORY_MESSAGES:
            self._history = self._history[-_MAX_HISTORY_MESSAGES:]
