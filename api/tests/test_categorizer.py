"""Phase D categorizer tests. The Anthropic client is always mocked — no network.
Covers: system-decided pattern_match, categorizer ordering, the LLM confidence
cap, fail-toward-review (error / malformed / invalid account), and caching.
"""
from __future__ import annotations

import json
import uuid

import pytest

from bedrock.categorizer import Categorizer, LLM_CONFIDENCE_CAP
from bedrock.llm import LLMResponse
from bedrock.policy_service import DocumentIn, ProposalIn


class MockLLM:
    """Configurable, call-counting LLM stand-in."""
    def __init__(self, payload=None, raise_exc=None, raw=None):
        self.calls = 0
        self.payload = payload
        self.raise_exc = raise_exc
        self.raw = raw
        self.model_id = "mock-cat"

    def complete(self, *, system, user):
        self.calls += 1
        if self.raise_exc:
            raise self.raise_exc
        if self.raw is not None:
            return LLMResponse(raw=self.raw, model_id=self.model_id)
        return LLMResponse(raw=json.dumps(self.payload), model_id=self.model_id)


def _cat(policy, llm):
    return Categorizer(policy.ledger, policy.ai_pool, llm)


def _sha():
    return uuid.uuid4().hex + uuid.uuid4().hex


# --- requirement 1: the SYSTEM decides pattern_match ------------------------
def test_pattern_match_is_system_decided_not_model(policy, porg):
    # model insists it's a confident, seen match; the vendor has no history
    llm = MockLLM(payload={"account_code": "5100", "confidence": 0.99,
                           "rationale": "I know this vendor well"})
    res = _cat(policy, llm).categorize(
        porg, txn_id="t1", vendor="Totally New Vendor Ltd",
        description="duct fabrication", content_sha256=_sha())
    assert res.pattern_match == "novel"           # no history -> novel, model can't override
    assert res.source_layer == "llm"
    assert res.confidence <= LLM_CONFIDENCE_CAP    # and capped regardless of the 0.99 claim


# --- requirement 4: LLM confidence capped below the auto-post gate ----------
def test_llm_confidence_is_capped(policy, porg):
    llm = MockLLM(payload={"account_code": "6100", "confidence": 0.999, "rationale": "x"})
    res = _cat(policy, llm).categorize(
        porg, txn_id="t1", vendor="Brand New Fuel Co", description="fuel",
        content_sha256=_sha())
    assert res.confidence == LLM_CONFIDENCE_CAP    # 0.999 -> 0.90


# --- requirement 3: invalid account is a failed proposal, not a guess -------
def test_invalid_account_fails_toward_review(policy, porg):
    llm = MockLLM(payload={"account_code": "9999", "confidence": 0.8, "rationale": "x"})
    res = _cat(policy, llm).categorize(
        porg, txn_id="t1", vendor="New Vendor", description="x", content_sha256=_sha())
    assert res.source_layer == "failed"
    assert res.confidence == 0.0
    assert "not in the chart" in res.rationale


# --- requirement 5: fail toward review on error / malformed JSON ------------
def test_llm_error_fails_toward_review(policy, porg):
    llm = MockLLM(raise_exc=TimeoutError("api timeout"))
    res = _cat(policy, llm).categorize(
        porg, txn_id="t1", vendor="New Vendor", description="x", content_sha256=_sha())
    assert res.source_layer == "failed"
    assert res.confidence == 0.0
    assert "failed" in res.rationale.lower()


def test_malformed_json_fails_toward_review(policy, porg):
    llm = MockLLM(raw="this is not json{{")
    res = _cat(policy, llm).categorize(
        porg, txn_id="t1", vendor="New Vendor", description="x", content_sha256=_sha())
    assert res.source_layer == "failed"
    assert res.confidence == 0.0


# --- requirement 2: pattern memory first; LLM only on a miss ---------------
def test_pattern_memory_precedes_llm(policy, porg):
    org = porg
    # build one approved decision for a vendor (queue it, then approve)
    r = policy.ingest_transaction(
        org, day="2026-06-01", amount_minor=4000, counterparty="Ferguson Supply",
        description="parts", direction="outflow",
        document=DocumentIn("bank_feed_line", "plaid", "FERG-hist"),
        proposal=ProposalIn("5000", "expense", "parts", 0.72, "novel"))  # novel -> queued
    policy.submit_review(org, r["txn_id"], action="approve", reviewer_id="bk",
                         reviewer_role="bookkeeper")

    llm = MockLLM(payload={"account_code": "6600", "confidence": 0.9, "rationale": "wrong"})
    res = _cat(policy, llm).categorize(
        org, txn_id="t2", vendor="Ferguson Supply", description="more parts",
        content_sha256=_sha())
    assert res.source_layer == "pattern_exact"     # served from memory
    assert res.pattern_match == "seen"
    assert res.account_code == "5000"              # from approved history, not the LLM
    assert llm.calls == 0                          # LLM never consulted on a hit


# --- requirement 6: cache — identical content, no second API call ----------
def test_cache_avoids_second_llm_call(policy, porg):
    org = porg
    sha = _sha()
    llm = MockLLM(payload={"account_code": "6100", "confidence": 0.8, "rationale": "x"})
    cat = _cat(policy, llm)
    first = cat.categorize(org, txn_id="t1", vendor="Cache Vendor", description="x",
                           content_sha256=sha)
    assert llm.calls == 1
    second = cat.categorize(org, txn_id="t2", vendor="Cache Vendor", description="x",
                            content_sha256=sha)
    assert second.cached is True
    assert second.proposal_id == first.proposal_id
    assert llm.calls == 1                           # not called again
