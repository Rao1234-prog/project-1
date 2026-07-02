#!/usr/bin/env python3
"""Walk the June demo end-to-end via HTTP API calls (Phase C gate).

Resets + reseeds the demo org, then: works the review queue down, is blocked at
close, approves the bank reconciliation, approves the close (period locks), and
confirms a post into the locked period is rejected. Also demonstrates the
server-side role boundary (a bookkeeper cannot clear a hard stop or a close).

Requires the API running at BASE and the DB env vars set (for the reseed).
"""
from __future__ import annotations

import os
import subprocess
import sys

import httpx

BASE = os.environ.get("BEDROCK_API_URL", "http://127.0.0.1:8000")
PERIOD = "2026-06"
BK = {"X-Bedrock-Role": "bookkeeper"}
CTRL = {"X-Bedrock-Role": "controller"}
HERE = os.path.dirname(__file__)

step = 0
def say(msg):
    global step; step += 1
    print(f"[{step:2}] {msg}")


def main() -> None:
    print("=== reseeding demo org ===")
    subprocess.run([sys.executable, os.path.join(HERE, "seed_demo.py"), "--reset"],
                   check=True, capture_output=True)

    c = httpx.Client(base_url=BASE, timeout=30)
    org = next(o for o in c.get("/orgs").json()["orgs"] if o["name"].startswith("Cardinal"))["org_id"]
    say(f"org resolved: Cardinal Heating & Air LLC")

    ents = c.get(f"/orgs/{org}/entries").json()
    tb = c.get(f"/orgs/{org}/trial-balance").json()["trial_balance"]
    vc = c.get(f"/orgs/{org}/chain/verify").json()["verified"]
    say(f"seeded: {ents['total']} posted entries · trial balance {tb}¢ · chain verified={vc}")

    q = c.get(f"/orgs/{org}/queue").json()["queue"]
    say(f"review queue opens with {len(q)} items: "
        + ", ".join(f"{i['txn_id']}→{i['decision']}" for i in q))

    # --- role boundary: a bookkeeper cannot clear a hard stop ---------------
    hard = next(i for i in q if i["decision"] == "hard_stop")
    r = c.post(f"/orgs/{org}/transactions/{hard['txn_id']}/reviews", headers=BK,
               json={"action": "approve", "reviewer_id": "J. Okafor"})
    say(f"bookkeeper tries to clear hard stop {hard['txn_id']} → HTTP {r.status_code} "
        f"(required: {r.json()['detail'].get('required_role')})")

    # --- work the queue down -------------------------------------------------
    for item in q:
        lane = item["decision"]
        headers = CTRL if lane in ("controller_queue", "hard_stop") else BK
        who = "controller" if headers is CTRL else "bookkeeper"
        r = c.post(f"/orgs/{org}/transactions/{item['txn_id']}/reviews", headers=headers,
                   json={"action": "approve", "reviewer_id": who})
        say(f"{who} approves {item['txn_id']} ({lane}) → HTTP {r.status_code}, "
            f"posted entry {str(r.json().get('posted_entry_id'))[:8]}")
    say(f"queue now has {len(c.get(f'/orgs/{org}/queue').json()['queue'])} items")

    # --- close blocked (reconciliation still pending) -----------------------
    r = c.post(f"/orgs/{org}/close/approve", headers=CTRL,
               json={"period": PERIOD, "approved_by": "M. Reyes"})
    findings = r.json()["detail"]["findings"]
    say(f"controller attempts close → HTTP {r.status_code} BLOCKED: "
        + "; ".join(f"{f['check']}" for f in findings))

    # --- role boundary: a bookkeeper cannot approve a close -----------------
    r = c.post(f"/orgs/{org}/close/approve", headers=BK,
               json={"period": PERIOD, "approved_by": "J. Okafor"})
    say(f"bookkeeper attempts close → HTTP {r.status_code} "
        f"(required: {r.json()['detail'].get('required_role')})")

    # --- approve reconciliation ---------------------------------------------
    r = c.post(f"/orgs/{org}/reconciliations/approve", headers=CTRL,
               json={"account_code": "1000", "period": PERIOD, "approved_by": "M. Reyes"})
    say(f"controller approves bank reconciliation (1000) → HTTP {r.status_code}")

    cl = c.get(f"/orgs/{org}/close/checklist", params={"period": PERIOD}).json()
    say(f"checklist now: can_close={cl['can_close']}, findings={len(cl['findings'])}")

    # --- approve close -> period locks --------------------------------------
    r = c.post(f"/orgs/{org}/close/approve", headers=CTRL,
               json={"period": PERIOD, "approved_by": "M. Reyes"})
    say(f"controller approves close → HTTP {r.status_code}, status={r.json()['status']}")

    # --- post into the locked period is rejected ----------------------------
    doc = c.post(f"/orgs/{org}/documents",
                 json={"doc_type": "bank_feed_line", "source_system": "plaid",
                       "raw": "07/01 late June adjustment attempt"}).json()["doc_id"]
    r = c.post(f"/orgs/{org}/entries", json={
        "entry_date": "2026-06-30", "entry_type": "standard", "memo": "late post",
        "posted_by_policy": "test",
        "lines": [{"account_code": "6100", "side": "debit", "amount_minor": 100, "doc_id": doc},
                  {"account_code": "1000", "side": "credit", "amount_minor": 100, "doc_id": doc}]})
    say(f"post into LOCKED June period → HTTP {r.status_code} REJECTED: {r.json()['detail'][:60]}")

    tb = c.get(f"/orgs/{org}/trial-balance").json()["trial_balance"]
    vc = c.get(f"/orgs/{org}/chain/verify").json()["verified"]
    say(f"final: trial balance {tb}¢ · chain verified={vc} · "
        f"{c.get(f'/orgs/{org}/entries').json()['total']} entries")
    print("\nDEMO WALK COMPLETE.")


if __name__ == "__main__":
    main()
