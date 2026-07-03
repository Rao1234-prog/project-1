#!/usr/bin/env python3
"""Phase D end-to-end demo: a fresh vendor arrives, the categorizer falls back to
the LLM, a human corrects it, the per-account error rate updates, and the same
vendor's next transaction is served from pattern memory.

Uses a deterministic offline LLM stub by default (no network). Set RUN_LIVE_LLM=1
(and ANTHROPIC_API_KEY) to exercise the real Anthropic categorizer instead.

Env: GREENLEDGER_DATABASE_URL, GREENLEDGER_AI_URL, GREENLEDGER_ADMIN_URL.
"""
from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from greenledger.policy_service import PolicyService, DocumentIn
from greenledger.llm import StubLLMClient, AnthropicLLMClient

step = 0
def say(m):
    global step; step += 1
    print(f"[{step}] {m}")


def proposal_of(policy, org, txn_id):
    with policy.ledger.pool.connection() as c:
        return c.execute(
            """SELECT source_layer, pattern_match, account_code, confidence, model_id
               FROM proposals WHERE org_id=%s AND txn_id=%s""", (org, txn_id)).fetchone()


def main():
    live = os.environ.get("RUN_LIVE_LLM") == "1" and os.environ.get("ANTHROPIC_API_KEY")
    llm = AnthropicLLMClient() if live else StubLLMClient()
    say(f"LLM fallback client: {'AnthropicLLMClient (live)' if live else 'StubLLMClient (offline, deterministic)'}")

    policy = PolicyService(llm=llm)
    L = policy.ledger
    org = L.ensure_org(f"PhaseD Demo {uuid.uuid4().hex[:8]}")
    for c, n, t, nb in [("1000", "Operating checking", "asset", "debit"),
                        ("5100", "Subcontractors", "expense", "debit"),
                        ("6100", "Fuel & vehicle", "expense", "debit")]:
        L.add_account(org, c, n, t, nb)
    say(f"fresh org created with chart of accounts")

    vendor = "Bluebird Duct Fabrication LLC"

    def ingest(raw):
        return policy.ingest_transaction(
            org, day="2026-06-12", amount_minor=4200, counterparty=vendor,
            description="duct fabrication for Hobbs job", direction="outflow",
            document=DocumentIn("invoice", "email_ingest", raw))  # no proposal -> categorizer

    r1 = ingest("Bluebird inv #1 duct fab 42.00")
    pr1 = proposal_of(policy, org, r1["txn_id"])
    say(f"NEW vendor '{vendor}' arrives -> categorizer")
    say(f"    pattern memory MISS -> LLM fallback: layer={pr1[0]} pattern={pr1[1]} "
        f"acct={pr1[2]} conf={float(pr1[3]):.2f} model={pr1[4]}")
    say(f"    routing decision: {r1['decision']}  (novel + capped conf -> never auto-posts)")

    policy.submit_review(org, r1["txn_id"], action="correct", reviewer_id="J. Okafor",
                         reviewer_role="bookkeeper", corrected_account_code="5100")
    err = policy.account_error(org, "5100")
    say(f"human corrects -> account 5100. error-rate row for 5100 now: "
        f"{err['corrected']}/{err['decided']} decisions")

    r2 = ingest("Bluebird inv #2 duct fab 42.00 (different invoice)")
    pr2 = proposal_of(policy, org, r2["txn_id"])
    say(f"SAME vendor's next transaction -> categorizer")
    say(f"    pattern memory HIT: layer={pr2[0]} pattern={pr2[1]} acct={pr2[2]} "
        f"conf={float(pr2[3]):.2f}")
    say(f"    routing decision: {r2['decision']}  (grounded 'seen' proposal can now auto-post)")

    audit = policy.accuracy_audit(org)
    say(f"accuracy audit: reviewed={audit['reviewed_count']} "
        f"first-pass={audit['overall_first_pass_agreement']} "
        f"wrong_auto_posts={audit['wrong_auto_posts']}")
    say(f"ledger trial balance={L.trial_balance(org)} chain_verified={L.verify_chain(org)}")
    policy.close()
    print("\nPHASE D DEMO COMPLETE.")


if __name__ == "__main__":
    main()
