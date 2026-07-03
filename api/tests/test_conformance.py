"""Conformance tests (spec Section 1/2 adjudication):
  * trailing-90-day error window — an account with only *old* errors regains autonomy
  * audit_log hash-chain tamper detection
"""
from __future__ import annotations

from greenledger.policy_service import DocumentIn, ProposalIn


def _ingest(policy, org, *, raw, cp="Repeaty Vendor", code="6100"):
    return policy.ingest_transaction(
        org, day="2026-05-13", amount_minor=4200, counterparty=cp, description=cp,
        direction="outflow", document=DocumentIn("bank_feed_line", "plaid", raw),
        proposal=ProposalIn(code, "expense", "m", 0.5, "similar"))   # similar -> queues


def _min_conf(policy, org, code):
    return policy.account_error(org, code)["effective_thresholds"]["auto_post_min_confidence"]


# --- trailing-90-day window: old errors age out, autonomy returns -----------
def test_error_window_recovery(policy, porg):
    org = porg
    for i in range(10):
        r = _ingest(policy, org, raw=f"OLD-{i}", cp=f"cp{i}")
        policy.submit_review(org, r["txn_id"], action="correct", reviewer_id="bk",
                             reviewer_role="bookkeeper", corrected_account_code="5000")

    # recent errors -> autonomy off for 6100
    assert _min_conf(policy, org, "6100") == 1.01

    # age every decision past the 90-day window (app role may UPDATE review_queue)
    with policy.ledger.pool.connection() as conn:
        conn.execute(
            "UPDATE review_queue SET resolved_at = now() - make_interval(days => 120) "
            "WHERE org_id=%s AND status='resolved'", (org,))

    # the account's history is now entirely outside the window -> autonomy regained
    assert _min_conf(policy, org, "6100") == 0.97


# --- audit_log is hash-chained and tamper-evident ---------------------------
def test_audit_log_tamper_detection(policy, porg, admin_conn):
    org = porg
    # generate a few chained audit rows
    policy.ledger.ingest_document(org, "receipt", "test", "AUDIT-1")
    policy.ledger.ingest_document(org, "receipt", "test", "AUDIT-2")
    policy.ledger.ingest_document(org, "receipt", "test", "AUDIT-3")

    def verify():
        with policy.ledger.pool.connection() as conn:
            return conn.execute("SELECT verify_audit_chain(%s)", (org,)).fetchone()[0]

    assert verify() is True

    # tamper a row (only the superuser can — the app role has no UPDATE on audit_log)
    victim = admin_conn.execute(
        "SELECT log_id, action FROM audit_log WHERE org_id=%s ORDER BY log_id LIMIT 1",
        (org,)).fetchone()
    admin_conn.execute("UPDATE audit_log SET action='TAMPERED' WHERE log_id=%s", (victim[0],))
    assert verify() is False

    # restore -> chain verifies again
    admin_conn.execute("UPDATE audit_log SET action=%s WHERE log_id=%s", (victim[1], victim[0]))
    assert verify() is True
