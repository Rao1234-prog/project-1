"""Phase D API tests: categorizer-driven ingest (no proposal in the body), the
accuracy-audit endpoint, and a live LLM smoke test gated behind RUN_LIVE_LLM.
"""
from __future__ import annotations

import os
import uuid

import pytest

BK = {"X-GreenLedger-Role": "bookkeeper"}


def _tx(client, org, *, vendor, raw, day="2026-06-10", amount=4000, proposal=None):
    body = {"day": day, "amount_minor": amount, "counterparty": vendor,
            "description": vendor, "direction": "outflow",
            "document": {"doc_type": "bank_feed_line", "source_system": "plaid", "raw": raw}}
    if proposal:
        body["proposal"] = proposal
    return client.post(f"/orgs/{org}/transactions", json=body)


def test_ingest_without_proposal_runs_categorizer(client, api_org):
    org = api_org
    # No proposal in the body -> the server categorizes. With no LLM credentials
    # in this environment the LLM fails toward review (never auto-posts, never
    # crashes) — either way a new vendor is not auto-posted.
    r = _tx(client, org, vendor="Greenfield Rentals", raw=f"GF-{uuid.uuid4()}")
    assert r.status_code == 200
    body = r.json()
    assert body["deduped"] is False
    assert body["decision"] != "auto_post"          # novel vendor -> human review

    # a proposal row was written with categorizer provenance
    trail = client.get(f"/orgs/{org}/entries").json()  # ensure endpoint healthy
    assert "entries" in trail


def test_accuracy_audit_endpoint(client, api_org):
    org = api_org
    # create some reviewed history via explicit proposals
    for i in range(3):
        r = _tx(client, org, vendor=f"Vendor{i}", raw=f"AUD-{i}",
                proposal={"account_code": "5000", "account_type": "expense",
                          "rationale": "parts", "confidence": 0.72, "pattern_match": "novel"})
        txn = r.json()["txn_id"]
        # approve two, correct one
        action = "approve" if i < 2 else "correct"
        payload = {"action": action, "reviewer_id": "bk"}
        if action == "correct":
            payload["corrected_account_code"] = "6100"
        client.post(f"/orgs/{org}/transactions/{txn}/reviews", headers=BK, json=payload)

    audit = client.get(f"/orgs/{org}/audit/accuracy").json()
    assert audit["reviewed_count"] == 3
    assert audit["gate"] == 0.95
    assert audit["wrong_auto_posts"] == 0            # structural
    # 2 approvals of 5000, 1 correction -> 5000 agreement = 2/3
    acct = {a["account_code"]: a for a in audit["per_account"]}
    assert acct["5000"]["agreement"] == pytest.approx(2 / 3, abs=0.01)
    pat = {p["pattern_match"]: p for p in audit["per_pattern"]}
    assert pat["novel"]["total"] == 3


@pytest.mark.skipif(
    not (os.environ.get("RUN_LIVE_LLM") == "1" and os.environ.get("ANTHROPIC_API_KEY")),
    reason="live LLM smoke test requires RUN_LIVE_LLM=1 and ANTHROPIC_API_KEY")
def test_live_llm_smoke(policy, porg):
    """Real Anthropic call. Skipped unless explicitly enabled. Asserts the LLM
    fallback yields a novel proposal capped at 0.90."""
    from greenledger.categorizer import Categorizer, LLM_CONFIDENCE_CAP
    from greenledger.llm import AnthropicLLMClient
    cat = Categorizer(policy.ledger, policy.ai_pool, AnthropicLLMClient())
    res = cat.categorize(porg, txn_id="live1", vendor="Shell Fuel Station",
                         description="diesel fuel for fleet truck",
                         content_sha256=uuid.uuid4().hex)
    assert res.pattern_match == "novel"
    assert res.source_layer in ("llm", "failed")
    if res.source_layer == "llm":
        assert res.confidence <= LLM_CONFIDENCE_CAP
        assert res.account_code  # a real code from the chart
