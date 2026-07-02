"""Phase B integration tests against Postgres: idempotency, decision provenance,
review authorization, persistent state across restart, and determinism.
"""
from __future__ import annotations

import uuid

import pytest

from bedrock.policy_service import DocumentIn, PolicyService, ProposalIn, Unauthorized


def _ingest(policy, org, *, day, amount, cp, code, atype, pattern, conf,
            direction="outflow", raw=None, flags=()):
    return policy.ingest_transaction(
        org, day=day, amount_minor=amount, counterparty=cp, description=cp,
        direction=direction, fraud_flags=flags,
        document=DocumentIn("bank_feed_line", "plaid", raw or f"{day} {cp} {amount} {direction}"),
        proposal=ProposalIn(code, atype, f"match {cp}", conf, pattern))


# --- requirement 1: idempotency by content hash -----------------------------
def test_idempotent_dedupe_same_line(policy, porg):
    org = porg
    first = _ingest(policy, org, day="2026-05-10", amount=4000, cp="Shell",
                    code="6100", atype="expense", pattern="seen", conf=0.99, raw="LINE-A")
    again = _ingest(policy, org, day="2026-05-10", amount=4000, cp="Shell",
                    code="6100", atype="expense", pattern="seen", conf=0.99, raw="LINE-A")
    assert first["deduped"] is False
    assert again["deduped"] is True
    assert again["txn_id"] == first["txn_id"]
    assert again["decision"] == first["decision"]
    # exactly one transaction persisted
    with policy.ledger.pool.connection() as conn:
        n = conn.execute("SELECT count(*) FROM transactions WHERE org_id=%s", (org,)).fetchone()[0]
    assert n == 1


# --- requirement 2: decision provenance persisted verbatim ------------------
def test_decision_provenance_persisted(policy, porg):
    org = porg
    res = _ingest(policy, org, day="2026-05-11", amount=4000, cp="Shell",
                  code="6100", atype="expense", pattern="seen", conf=0.99, raw="PROV-1")
    with policy.ledger.pool.connection() as conn:
        row = conn.execute(
            """SELECT policy_version, effective_thresholds, reason
               FROM routing_decisions WHERE org_id=%s AND txn_id=%s""",
            (org, res["txn_id"])).fetchone()
    policy_version, thresholds, reason = row
    assert policy_version == "1.0.0"
    assert reason == res["reason"]
    # full effective thresholds captured at decision time
    assert set(thresholds.keys()) == {
        "auto_post_max_amount", "hard_stop_amount", "auto_post_min_confidence",
        "queue_min_confidence", "daily_counterparty_cum_cap", "monthly_auto_post_share_cap"}


# --- requirement 3: review endpoint + server-side lane authorization --------
def test_bookkeeper_cannot_clear_controller_lane(policy, porg):
    org = porg
    res = _ingest(policy, org, day="2026-05-12", amount=5000, cp="TXComptroller",
                  code="2200", atype="tax", pattern="seen", conf=0.99, raw="AUTHZ-1")
    assert res["decision"] == "controller_queue"
    with pytest.raises(Unauthorized):
        policy.submit_review(org, res["txn_id"], action="approve",
                             reviewer_id="bk", reviewer_role="bookkeeper")


def test_bookkeeper_cannot_clear_hard_stop(policy, porg):
    org = porg
    res = _ingest(policy, org, day="2026-05-12", amount=10_000_00, cp="Big",
                  code="5000", atype="expense", pattern="seen", conf=0.99, raw="AUTHZ-2")
    assert res["decision"] == "hard_stop"
    with pytest.raises(Unauthorized):
        policy.submit_review(org, res["txn_id"], action="approve",
                             reviewer_id="bk", reviewer_role="bookkeeper")
    # controller may
    out = policy.submit_review(org, res["txn_id"], action="approve",
                               reviewer_id="ctrl", reviewer_role="controller")
    assert out["status"] == "resolved"


def test_correction_updates_error_rate_and_tightens(policy, porg):
    org = porg
    # 10 decisions on account 6100, all corrected -> error_rate 1.0 -> autonomy off
    for i in range(10):
        r = _ingest(policy, org, day="2026-05-13", amount=100 + i, cp=f"cp{i}",
                    code="6100", atype="expense", pattern="similar", conf=0.5, raw=f"ERR-{i}")
        policy.submit_review(org, r["txn_id"], action="correct", reviewer_id="bk",
                             reviewer_role="bookkeeper", corrected_account_code="5000")
    info = policy.account_error(org, "6100")
    assert info["decided"] == 10
    assert info["error_rate"] == pytest.approx(1.0)
    assert info["effective_thresholds"]["auto_post_min_confidence"] == 1.01  # tightened

    # org-global fallback (spec): a fresh account in an org whose *global* rate is
    # bad inherits the caution — 6999 has no history but the org-global rate is 1.0.
    assert policy.account_error(org, "6999")["effective_thresholds"]["auto_post_min_confidence"] == 1.01

    # min-sample gate: in a clean org (<10 total decisions) nothing tightens.
    fresh = policy.ledger.ensure_org(f"clean-{uuid.uuid4()}")
    assert policy.account_error(fresh, "6100")["effective_thresholds"]["auto_post_min_confidence"] == 0.97


# --- requirement 4: state survives restart ----------------------------------
def test_daily_cumulative_survives_restart(policy, porg, app_url, ai_url):
    org = porg
    # push same-day, same-counterparty outflows just under the cap
    for i in range(5):
        _ingest(policy, org, day="2026-05-20", amount=40000, cp="Repeaty",
                code="6100", atype="expense", pattern="seen", conf=0.99, raw=f"CUM-{i}")
    # simulate a process restart: brand-new service instance, fresh pools
    restarted = PolicyService(app_dsn=app_url, ai_url=ai_url)
    try:
        res = _ingest(restarted, org, day="2026-05-20", amount=40000, cp="Repeaty",
                      code="6100", atype="expense", pattern="seen", conf=0.99, raw="CUM-after")
        # 6 * 40000 = 240000 > 200000 cap -> queued, proving day_cum was read from DB
        assert res["decision"] == "bookkeeper_queue"
        assert "cumulative cap" in res["reason"]
    finally:
        restarted.close()


# --- requirement 5: determinism across independent state --------------------
def test_same_sequence_two_orgs_identical_decisions(policy):
    L = policy.ledger
    seq = [
        ("2026-05-01", 4000, "Shell", "6100", "expense", "seen", 0.99),
        ("2026-05-01", 4000, "Shell", "6100", "expense", "seen", 0.99),
        ("2026-05-02", 10_000_00, "Big", "5000", "expense", "seen", 0.99),
        ("2026-05-02", 4000, "Novelty", "5000", "expense", "novel", 0.72),
        ("2026-05-03", 5000, "Tax", "2200", "tax", "seen", 0.99),
    ]

    def run(org):
        for code, name, atype, nb in [("1000", "Cash", "asset", "debit"),
                                      ("6100", "Fuel", "expense", "debit"),
                                      ("5000", "Parts", "expense", "debit"),
                                      ("2200", "Tax", "liability", "credit")]:
            L.add_account(org, code, name, atype, nb, is_sensitive=(code == "2200"))
        out = []
        for day, amt, cp, code, atype, pat, conf in seq:
            r = _ingest(policy, org, day=day, amount=amt, cp=cp, code=code,
                        atype=atype, pattern=pat, conf=conf, raw=f"{day}-{cp}-{amt}")
            out.append(r["decision"])
        return out

    import uuid
    a = run(L.ensure_org(f"det-a-{uuid.uuid4()}"))
    b = run(L.ensure_org(f"det-b-{uuid.uuid4()}"))
    assert a == b


# --- requirement 6 (sub-check): cumulative cap fires on a same-day sequence --
def test_cumulative_cap_fires_on_seeded_sequence(policy, porg):
    org = porg
    decisions = []
    for i in range(6):
        r = _ingest(policy, org, day="2026-06-01", amount=40000, cp="SameVendor",
                    code="6100", atype="expense", pattern="seen", conf=0.99, raw=f"SEQ-{i}")
        decisions.append((r["decision"], r["reason"]))
    # the transaction that pushes cumulative past $2,000 is queued with the cap reason
    assert any(d == "bookkeeper_queue" and "cumulative cap" in reason
               for d, reason in decisions)
