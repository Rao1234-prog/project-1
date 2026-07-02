"""Phase B gate: run the ported Phase 1 simulation and assert INVARIANTS
(not exact routing counts, which depend on the RNG and won't match across a port).
"""
from __future__ import annotations

import uuid

from bedrock.phase1_sim import load_coa, simulate
from bedrock.policy import BASE


def test_phase1_simulation_invariants(policy):
    org = policy.ledger.ensure_org(f"gate-{uuid.uuid4()}")
    load_coa(policy.ledger, org)
    stats = simulate(policy, org, seed=42)

    audit = stats["audit"]
    assert stats["total"] > 250          # sanity: full dataset ran

    # (a) zero wrong categorizations auto-posted
    assert stats["auto_wrong"] == 0

    # (b) every transaction >= $10,000 was hard-stopped
    for ai_code, truth, decision, amount, pattern in audit:
        if amount >= BASE["hard_stop_amount"]:
            assert decision == "hard_stop", f"{amount} not hard-stopped"

    # (c) every novel-pattern transaction was queued (never auto-posted)
    for ai_code, truth, decision, amount, pattern in audit:
        if pattern == "novel":
            assert decision != "auto_post"

    # (d) cumulative cap fired at least once on a same-day counterparty run
    #     (guaranteed deterministically here, independent of the RNG draw)
    capped = _force_cumulative_cap(policy, org)
    assert capped

    # (e) auto-post share stayed under the cap
    assert stats["auto_share"] <= BASE["monthly_auto_post_share_cap"]

    # (f) trial balance nets to zero, (g) chain verifies
    assert stats["trial_balance"] == 0
    assert stats["chain_verified"] is True


def _force_cumulative_cap(policy, org) -> bool:
    from bedrock.policy_service import DocumentIn, ProposalIn
    decisions = []
    for i in range(6):
        r = policy.ingest_transaction(
            org, day="2026-06-15", amount_minor=40000, counterparty="CapRun",
            description="CapRun", direction="outflow",
            document=DocumentIn("bank_feed_line", "plaid", f"CAPRUN-{i}"),
            proposal=ProposalIn("6100", "expense", "cap run", 0.99, "seen"))
        decisions.append((r["decision"], r["reason"]))
    return any(d == "bookkeeper_queue" and "cumulative cap" in reason
               for d, reason in decisions)
