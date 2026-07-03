"""LLM client seam for the categorizer.

The categorizer depends on the ``LLMClient`` protocol, never on the Anthropic SDK
directly, so tests inject a mock and no network is required. ``AnthropicLLMClient``
is the real implementation; ``StubLLMClient`` is a deterministic offline stand-in
for demos.

Contract for every client: given a system + user prompt, return the raw model
text (expected to be a JSON object). The categorizer owns parsing, validation,
clamping, and the fail-toward-review behavior — a client only produces text or
raises.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# Categorization is a cheap, structured classification task and must honor
# temperature=0 for determinism. Opus 4.8 / Sonnet 5 reject `temperature`
# (HTTP 400); Haiku 4.5 accepts temperature=0 and supports structured outputs,
# so it is the correct tier here.
DEFAULT_MODEL = "claude-haiku-4-5"


@dataclass
class LLMResponse:
    raw: str            # raw response text (a JSON object, per the prompt)
    model_id: str


class LLMClient(Protocol):
    def complete(self, *, system: str, user: str) -> LLMResponse: ...

    @property
    def model_id(self) -> str: ...


# JSON schema the model is constrained to (shape only — account_code validity is
# checked against the org's real chart of accounts by the categorizer).
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "account_code": {"type": "string"},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
    },
    "required": ["account_code", "confidence", "rationale"],
    "additionalProperties": False,
}


class AnthropicLLMClient:
    """Real Anthropic-backed client. temperature=0, strict JSON output."""

    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None,
                 timeout: float = 20.0):
        # Construct lazily: missing credentials must surface as a failed
        # categorization (-> human review), not a crash at service startup.
        self._model = model
        self._api_key = api_key
        self._timeout = timeout
        self._client = None

    @property
    def model_id(self) -> str:
        return self._model

    def _ensure(self):
        if self._client is None:
            import anthropic
            self._client = (anthropic.Anthropic(api_key=self._api_key, timeout=self._timeout)
                            if self._api_key else anthropic.Anthropic(timeout=self._timeout))
        return self._client

    def complete(self, *, system: str, user: str) -> LLMResponse:
        resp = self._ensure().messages.create(
            model=self._model,
            max_tokens=512,
            temperature=0,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        return LLMResponse(raw=text, model_id=self._model)


class StubLLMClient:
    """Deterministic offline stand-in for demos (no network). Picks an account
    by keyword; clearly not for production. Confidence is intentionally high to
    demonstrate that the 0.90 LLM cap — not the model's self-report — is what
    keeps a novel vendor out of auto-post."""

    KEYWORDS = [
        ("duct", "5100"), ("subcontract", "5100"), ("fuel", "6100"), ("gas", "6100"),
        ("crane", "5000"), ("rental", "5000"), ("part", "5000"), ("supply", "5000"),
        ("software", "6200"), ("saas", "6200"), ("phone", "6200"),
        ("payroll", "6000"), ("rent", "6300"), ("insurance", "6400"),
        ("tax", "2200"), ("meal", "6600"), ("lunch", "6600"),
    ]
    model_id = "stub-cat-1"

    def complete(self, *, system: str, user: str) -> LLMResponse:
        import json
        low = user.lower()
        code = next((c for kw, c in self.KEYWORDS if kw in low), "5000")
        return LLMResponse(
            raw=json.dumps({"account_code": code, "confidence": 0.95,
                            "rationale": f"stub keyword match on '{low[:40]}'"}),
            model_id=self.model_id)
