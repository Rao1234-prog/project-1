"""The agent: OpenAI-compatible chat-completions call + tool loop, with the
safety gate wired in.

JARVIS is provider-agnostic: it talks to any OpenAI-compatible endpoint (Groq,
Gemini's compat API, OpenAI itself, …) configured in ~/.jarvis/config.toml.
Only the wire format lives here; tool implementations, the registry, and the
safety gate are unchanged.

Design seams for Tier 2 (voice):
  * Input  — `Agent.handle_request(text)` takes a plain string. A Whisper
             transcription is just another string; the loop is unchanged.
  * Output — everything user-facing goes through `ui.respond(text)`. Add TTS
             there and nothing else changes.
"""

from __future__ import annotations

import json
import logging
import time

from . import safety, ui
from .config import Config
from .tools import registry

log = logging.getLogger("jarvis.agent")

# Keep the last ~10 exchanges (user+assistant) so follow-ups have context.
_MAX_HISTORY_MESSAGES = 20
# Guard against a runaway tool loop within a single request.
_MAX_TOOL_ITERATIONS = 8
_MAX_TOKENS = 2048
# Free tiers throttle; retry a few 429s with exponential backoff.
_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0  # seconds; delay = base * 2**attempt (unless Retry-After given)
# Some open models (notably Llama on Groq) intermittently emit malformed
# tool-call syntax the provider rejects with a 400 (code "tool_use_failed").
# A plain re-request almost always yields a well-formed call, so retry a couple
# of times before surfacing a clean, in-character error.
_MAX_TOOL_FORMAT_RETRIES = 2
_TOOL_FORMAT_RETRY_DELAY = 0.5  # seconds; a brief pause, not throttling

_OPERATIONAL_NOTE = (
    "\n\nYou are running as a macOS menu bar assistant. You have tools to inspect "
    "and control the Mac. Some tools require the user's confirmation before they "
    "run; if the user declines, respect it and offer an alternative rather than "
    "trying to work around it. Keep simple answers short enough to read in a "
    "notification."
)


def _is_rate_limit(exc: Exception) -> bool:
    """True if an exception is (or looks like) an HTTP 429."""
    return getattr(exc, "status_code", None) == 429


def _is_tool_use_failed(exc: Exception) -> bool:
    """True for a 400 where the model emitted tool-call syntax the provider
    could not parse. Groq reports this as error code 'tool_use_failed'."""
    if getattr(exc, "status_code", None) != 400:
        return False
    if getattr(exc, "code", None) == "tool_use_failed":
        return True
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("code") == "tool_use_failed":
            return True
    return "tool_use_failed" in str(exc)


def _retry_after(exc: Exception) -> float | None:
    """Seconds to wait per a Retry-After response header, if present."""
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if not headers:
        return None
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


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
        except Exception as exc:
            if _is_rate_limit(exc):
                log.warning("request rate-limited after %d retries", _MAX_RETRIES)
                answer = (
                    "We're being rate-limited by the provider, sir — give it a "
                    "moment and ask again."
                )
            elif _is_tool_use_failed(exc):
                log.warning("provider kept rejecting malformed tool calls")
                answer = (
                    "The model garbled a tool request a few times just now, sir — "
                    "a known hiccup with this provider. Do ask again."
                )
            else:  # never let a request crash the app
                log.exception("request failed")
                answer = f"Something went wrong, sir: {exc}"
        ui.respond(answer)

    # -- internals ------------------------------------------------------------
    def _client_or_error(self):
        if self._client is not None:
            return self._client
        if not self.config.api_key:
            raise RuntimeError(
                "no API key. Add it to ~/.jarvis/config.toml under [provider], "
                "or set JARVIS_API_KEY."
            )
        from openai import OpenAI  # imported lazily so the app starts without a key

        self._client = OpenAI(
            base_url=self.config.base_url,
            api_key=self.config.api_key,
        )
        return self._client

    def _system_prompt(self) -> str:
        return self.config.persona_text() + _OPERATIONAL_NOTE

    def _chat_create(self, model: str, messages: list[dict], tools=None):
        """One chat-completions call, resilient to transient provider errors.

        Two independent retry budgets:
          * HTTP 429 rate limits  -> exponential backoff (honours Retry-After).
          * 'tool_use_failed' 400s -> the model emitted malformed tool-call
            syntax the provider rejected; a plain re-request usually fixes it.
        Anything else propagates immediately.
        """
        client = self._client_or_error()
        kwargs: dict = {"model": model, "max_tokens": _MAX_TOKENS, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        rate_retries = 0
        format_retries = 0
        while True:
            try:
                return client.chat.completions.create(**kwargs)
            except Exception as exc:
                if _is_rate_limit(exc) and rate_retries < _MAX_RETRIES:
                    delay = _retry_after(exc)
                    if delay is None:
                        delay = _BACKOFF_BASE * (2 ** rate_retries)
                    rate_retries += 1
                    log.warning(
                        "rate limited (attempt %d/%d); backing off %.1fs",
                        rate_retries, _MAX_RETRIES, delay,
                    )
                    time.sleep(delay)
                    continue
                if _is_tool_use_failed(exc) and format_retries < _MAX_TOOL_FORMAT_RETRIES:
                    format_retries += 1
                    log.warning(
                        "provider rejected a malformed tool call (attempt %d/%d); retrying",
                        format_retries, _MAX_TOOL_FORMAT_RETRIES,
                    )
                    time.sleep(_TOOL_FORMAT_RETRY_DELAY)
                    continue
                raise

    def _run(self, user_text: str) -> str:
        system = self._system_prompt()
        # Working message list = system + rolling history + this turn. Intermediate
        # tool_call / tool-result messages stay local and are NOT persisted to
        # history (history holds only clean user/assistant text), which keeps
        # follow-ups simple and avoids tool-pairing hazards.
        messages: list[dict] = (
            [{"role": "system", "content": system}]
            + list(self._history)
            + [{"role": "user", "content": user_text}]
        )

        final_text = ""
        for _ in range(_MAX_TOOL_ITERATIONS):
            response = self._chat_create(
                self.config.model, messages, tools=registry.openai_tools()
            )
            msg = response.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None)

            if msg.content:
                final_text = msg.content.strip()

            if not tool_calls:
                break

            # Preserve the assistant turn (incl. tool_calls) for the API.
            messages.append(self._assistant_message(msg))
            for tc in tool_calls:
                result_text = self._handle_tool_call(tc)
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": result_text}
                )
        else:
            log.warning("hit tool-iteration cap")

        final_text = final_text or "Done, sir."
        self._remember(user_text, final_text)
        return final_text

    @staticmethod
    def _assistant_message(msg) -> dict:
        return {
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ],
        }

    def _handle_tool_call(self, tc) -> str:
        """Parse args, apply the safety gate, execute, and return result text."""
        name = tc.function.name
        raw_args = tc.function.arguments
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args or "{}")
            except json.JSONDecodeError:
                return f"Error: could not parse arguments for {name}."
        else:
            args = raw_args or {}

        allowed, level = safety.gate(name, args)
        if not allowed:
            return f"The user declined to approve this {level.value.lower()} action."
        raw = registry.execute(name, args)
        return self._tool_result_to_text(name, raw)

    def _tool_result_to_text(self, name: str, raw) -> str:
        """Flatten a tool result to a string for a `role: tool` message.

        Screenshots come back as an image dict; route them to a vision model if
        one is configured, otherwise report cleanly that visual analysis isn't
        available on a text-only provider.
        """
        if isinstance(raw, dict) and raw.get("type") == "image":
            path = raw.get("path", "the screen")
            if not self.config.vision_model:
                return (
                    f"Screenshot captured and saved to {path}, but visual analysis "
                    "isn't available on the current provider — its model is "
                    "text-only. Tell the user you captured the screen but can't "
                    "describe it until a vision-capable model is configured."
                )
            try:
                desc = self._describe_image(raw["data_url"])
            except Exception as exc:
                log.warning("vision call failed: %s", exc)
                return f"Screenshot captured ({path}), but visual analysis failed: {exc}"
            return f"Screenshot captured ({path}). What the screen shows:\n{desc}"

        if isinstance(raw, list):  # legacy content-block form; flatten to text
            texts = [b.get("text", "") for b in raw if isinstance(b, dict) and b.get("type") == "text"]
            return "\n".join(t for t in texts if t) or "(no text content)"

        return str(raw)

    def _describe_image(self, data_url: str) -> str:
        response = self._chat_create(
            self.config.vision_model,
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "This is a screenshot of the user's Mac screen. "
                                "Describe what is visible — apps, windows, key text, "
                                "and overall context. Be concise but specific."
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
        )
        return (response.choices[0].message.content or "").strip() or "(the vision model returned no description)"

    def _remember(self, user_text: str, assistant_text: str) -> None:
        self._history.append({"role": "user", "content": user_text})
        self._history.append({"role": "assistant", "content": assistant_text})
        if len(self._history) > _MAX_HISTORY_MESSAGES:
            self._history = self._history[-_MAX_HISTORY_MESSAGES:]
