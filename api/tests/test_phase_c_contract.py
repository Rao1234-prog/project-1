"""Phase C endpoint contract tests: trail-join completeness, close-409 (the
client cannot force a close), and role-403 (server-side lane authorization).
"""
from __future__ import annotations

BK = {"X-GreenLedger-Role": "bookkeeper"}
CTRL = {"X-GreenLedger-Role": "controller"}


def _tx(client, org, *, day, amount, cp, code, atype, pattern, conf, direction="outflow", raw=None):
    return client.post(f"/orgs/{org}/transactions", json={
        "day": day, "amount_minor": amount, "counterparty": cp, "description": cp,
        "direction": direction,
        "document": {"doc_type": "bank_feed_line", "source_system": "plaid",
                     "raw": raw or f"{day} {cp} {amount}"},
        "proposal": {"account_code": code, "account_type": atype, "rationale": f"m {cp}",
                     "confidence": conf, "pattern_match": pattern}})


# --- trail join completeness ------------------------------------------------
def test_trail_join_is_complete(client, api_org):
    org = api_org
    # a bookkeeper-lane item, then resolved -> posted entry with full provenance
    r = _tx(client, org, day="2026-06-15", amount=61290, cp="Crane Rental Co",
            code="5000", atype="expense", pattern="similar", conf=0.83, raw="TRAIL-1").json()
    assert r["decision"] == "bookkeeper_queue"
    resolved = client.post(f"/orgs/{org}/transactions/{r['txn_id']}/reviews",
                           headers=BK, json={"action": "approve", "reviewer_id": "J. Okafor"})
    assert resolved.status_code == 200
    entry_id = resolved.json()["posted_entry_id"]

    trail = client.get(f"/orgs/{org}/entries/{entry_id}/trail").json()
    # every level of the join is present and non-empty
    assert trail["entry"]["entry_hash"]                      # hash / ledger seal
    assert trail["entry"]["chain_seq"] is not None
    assert trail["document"]["sha256"]                       # source document
    assert trail["document"]["raw"]
    assert trail["decision"]["decision"] == "bookkeeper_queue"
    assert trail["decision"]["reason"]                       # stored reason
    assert len(trail["decision"]["effective_thresholds"]) == 6   # stored thresholds
    assert trail["decision"]["policy_version"] == "1.0.0"
    assert trail["proposal"]["account_code"] == "5000"       # AI proposal
    assert trail["proposal"]["pattern_match"] == "similar"
    assert trail["reviewer"]["id"] == "J. Okafor"            # accountable human
    assert trail["reviewer"]["role"] == "bookkeeper"
    assert len(trail["lines"]) == 2


def test_trail_auto_post_names_policy_engine(client, api_org):
    org = api_org
    # push volume so the share cap allows an auto-post, then a small seen txn
    for i in range(12):
        _tx(client, org, day="2026-06-02", amount=100, cp=f"warm{i}", code="6100",
            atype="expense", pattern="seen", conf=0.99, raw=f"WARM-{i}")
    r = _tx(client, org, day="2026-06-02", amount=4000, cp="Shell", code="6100",
            atype="expense", pattern="seen", conf=0.99, raw="AUTO-TRAIL").json()
    assert r["decision"] == "auto_post"
    trail = client.get(f"/orgs/{org}/entries/{r['posted_entry_id']}/trail").json()
    assert trail["reviewer"]["kind"] == "auto"
    assert trail["decision"]["decision"] == "auto_post"


# --- close cannot be forced past a blocking finding (409) -------------------
def test_close_blocked_returns_409_with_findings(client, api_org):
    org = api_org
    # an open queue item in June -> blocking
    r = _tx(client, org, day="2026-06-10", amount=184000, cp="Rodriguez Duct",
            code="5100", atype="expense", pattern="novel", conf=0.72, raw="CLOSE-Q").json()
    assert r["decision"] == "bookkeeper_queue"

    blocked = client.post(f"/orgs/{org}/close/approve", headers=CTRL,
                          json={"period": "2026-06", "approved_by": "M. Reyes"})
    assert blocked.status_code == 409
    detail = blocked.json()["detail"]
    checks = {f["check"] for f in detail["findings"]}
    assert "review_queue_not_clear" in checks
    assert "bank_account_not_reconciled" in checks

    # work the queue down and reconcile, then close succeeds
    client.post(f"/orgs/{org}/transactions/{r['txn_id']}/reviews", headers=BK,
                json={"action": "correct", "reviewer_id": "J. Okafor",
                      "corrected_account_code": "5100"})
    client.post(f"/orgs/{org}/reconciliations/approve", headers=CTRL,
                json={"account_code": "1000", "period": "2026-06", "approved_by": "M. Reyes"})
    ok = client.post(f"/orgs/{org}/close/approve", headers=CTRL,
                     json={"period": "2026-06", "approved_by": "M. Reyes"})
    assert ok.status_code == 200
    assert ok.json()["status"] == "locked"

    # posting into the locked period is rejected
    doc = client.post(f"/orgs/{org}/documents",
                      json={"doc_type": "x", "source_system": "y", "raw": "LOCKED-POST"}).json()["doc_id"]
    late = client.post(f"/orgs/{org}/entries", json={
        "entry_date": "2026-06-30", "entry_type": "standard", "memo": "late", "posted_by_policy": "test",
        "lines": [{"account_code": "6100", "side": "debit", "amount_minor": 100, "doc_id": doc},
                  {"account_code": "1000", "side": "credit", "amount_minor": 100, "doc_id": doc}]})
    assert late.status_code == 422


# --- role authorization (403) -----------------------------------------------
def test_close_requires_controller(client, api_org):
    org = api_org
    # even with a clean org, a bookkeeper may not approve a close
    resp = client.post(f"/orgs/{org}/close/approve", headers=BK,
                       json={"period": "2026-06", "approved_by": "J. Okafor"})
    assert resp.status_code == 403
    assert resp.json()["detail"]["required_role"] == "controller"


def test_reconciliation_requires_controller(client, api_org):
    org = api_org
    resp = client.post(f"/orgs/{org}/reconciliations/approve", headers=BK,
                       json={"account_code": "1000", "period": "2026-06", "approved_by": "J. Okafor"})
    assert resp.status_code == 403


def test_missing_role_header_rejected(client, api_org):
    org = api_org
    resp = client.post(f"/orgs/{org}/close/approve",
                       json={"period": "2026-06", "approved_by": "x"})
    assert resp.status_code == 400
