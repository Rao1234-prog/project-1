#!/usr/bin/env python3
"""Seed the June 2026 demo state from bedrock-app.jsx via the service layer.

Reproduces Cardinal Heating & Air's demo so the app opens with real content:
the chart of accounts, the source documents, the posted June entries (each with
its full provenance — proposal, policy decision, reviewer — so the paper trail
renders), and the six open review-queue items with their exact routing reasons.

Idempotent: re-running is a no-op unless --reset is passed (which clears the
org's transactional data first). Amounts are integer cents throughout.

Env: BEDROCK_DATABASE_URL, BEDROCK_AI_URL, and (for --reset) BEDROCK_ADMIN_URL.
"""
from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import psycopg
from psycopg.types.json import Jsonb

from bedrock.policy import BASE, POLICY_VERSION
from bedrock.policy_service import PolicyService, ProposalIn
from bedrock.service import LineInput, _sha256

ORG_NAME = "Cardinal Heating & Air LLC"

COA = [
    ("1000", "Operating checking", "asset", "debit"),
    ("1200", "Accounts receivable", "asset", "debit"),
    ("1500", "Equipment", "asset", "debit"),
    ("1510", "Accum. depreciation", "contra", "credit"),
    ("2000", "Accounts payable", "liability", "credit"),
    ("2200", "Sales tax payable", "liability", "credit"),   # sensitive (tax)
    ("3000", "Owner's equity", "equity", "credit"),
    ("4000", "Service revenue", "revenue", "credit"),
    ("4100", "Install revenue", "revenue", "credit"),
    ("5000", "Parts & materials", "expense", "debit"),
    ("5100", "Subcontractors", "expense", "debit"),
    ("6000", "Payroll", "expense", "debit"),
    ("6100", "Fuel & vehicle", "expense", "debit"),
    ("6200", "Software & office", "expense", "debit"),
    ("6300", "Rent", "expense", "debit"),
    ("6400", "Insurance", "expense", "debit"),
    ("6500", "Depreciation", "expense", "debit"),
    ("6600", "Meals", "expense", "debit"),
]

# name -> (doc_type, source_system, raw)
DOCS = {
    "ob":   ("Opening balance", "Migration", "OB 2026-05-31 · migrated from QuickBooks Online · verified against 2025 filed return, 0 discrepancy"),
    "gus":  ("Payroll report", "Gusto", "Gusto payroll run 06/13 · 12 employees · gross 21,842.10 · taxes withheld itemized"),
    "ferg": ("Bank feed line", "Plaid", "06/17 FERGUSON SUPPLY #2214 · card *4411 · 391.24"),
    "ferg2":("Bank feed line", "Plaid", "06/17 FERGUSON SUPPLY #2231 · card *4411 · 486.20 · 3rd charge today, cum 1,912.60"),
    "qf":   ("Fuel card feed", "QuickFuel", "06/18 QUICKFUEL FLEET · truck 2 · 214.77 · 41.2 gal"),
    "vz":   ("Bank feed line", "Plaid", "06/19 VERIZON WIRELESS · autopay · 189.44"),
    "rod":  ("Invoice (OCR)", "Email ingest", "Rodriguez Duct LLC · inv #91 · duct fab, Hobbs job · 1,840.00 · net 30 · NEW VENDOR"),
    "tx":   ("Bank feed line", "Plaid", "06/20 TEXAS COMPTROLLER · webfile · 1,212.40"),
    "dep":  ("Processor settlement", "Stripe", "06/23 payout 14,500.00 · commercial install draw #3, Hobbs Elementary · fees itemized"),
    "crn":  ("Receipt (photo)", "Mobile upload", "Crane Rental Co · 4hr boom lift · 612.90 · job: Hobbs"),
    "cfa":  ("Bank feed line", "Plaid", "06/21 CHICKFILA #0442 DRIVE THR · 38.40 · descriptor garbled in feed"),
    "sv":   ("Bank feed line", "Plaid", "06/02 SERVICETITAN INC · subscription · 399.00"),
    "rent": ("ACH record", "Plaid", "06/01 OAKDALE PROPERTIES LLC · shop rent · 3,150.00"),
    "rev1": ("Processor settlement", "Stripe", "06/05 payout 6,240.00 · 11 residential service calls, fee detail attached"),
    "rev2": ("Processor settlement", "Stripe", "06/12 payout 8,912.00 · 14 residential service calls, fee detail attached"),
    "ins":  ("ACH record", "Plaid", "06/03 STATE FARM COMM POLICY · 610.00"),
    "deps": ("Schedule", "Bedrock engine", "Fleet depreciation · 3 trucks · SL 60 mo · June 1,450.00 · schedule replayable"),
    "accr": ("Invoice (OCR)", "Email ingest", "Johnstone Supply · inv #5561 · received 06/28, unpaid at close · 2,208.50"),
}

# Posted entries. Each: date, type, memo, lines[(code,side,amt,doc)], decision,
# reason, proposal(code,type,conf,pat,why) or None, reviewer(id,role) or None.
POSTED = [
    dict(date="2026-05-31", type="opening_balance", memo="Opening balances (QBO migration, tied to filed return)",
         lines=[("1000","debit",18268855,"ob"),("1500","debit",8740000,"ob"),
                ("1510","credit",2610000,"ob"),("3000","credit",24398855,"ob")],
         decision=None, reason=None, proposal=None, reviewer=("A. Whitfield, CPA","cpa"),
         posted_by="migration"),
    dict(date="2026-06-01", type="standard", memo="Shop rent — Oakdale Properties",
         lines=[("6300","debit",315000,"rent"),("1000","credit",315000,"rent")],
         decision="auto_post", reason="Identical recurring ACH, 14 prior months",
         proposal=("6300","expense",0.995,"seen","Identical recurring ACH, 14 prior months"),
         reviewer=None, posted_by="policy:auto_post"),
    dict(date="2026-06-02", type="standard", memo="ServiceTitan subscription",
         lines=[("6200","debit",39900,"sv"),("1000","credit",39900,"sv")],
         decision="auto_post", reason="Known SaaS vendor, fixed amount",
         proposal=("6200","expense",0.991,"seen","Known SaaS vendor, fixed amount"),
         reviewer=None, posted_by="policy:auto_post"),
    dict(date="2026-06-03", type="standard", memo="State Farm commercial policy",
         lines=[("6400","debit",61000,"ins"),("1000","credit",61000,"ins")],
         decision="auto_post", reason="Recurring insurer draft",
         proposal=("6400","expense",0.99,"seen","Recurring insurer draft"),
         reviewer=None, posted_by="policy:auto_post"),
    dict(date="2026-06-05", type="standard", memo="Stripe payout — 11 residential service calls",
         lines=[("1000","debit",624000,"rev1"),("4000","credit",624000,"rev1")],
         decision="bookkeeper_queue", reason="Settlement un-netted; fees verified",
         proposal=("4000","revenue",0.985,"seen","Settlement un-netted; fees verified"),
         reviewer=("J. Okafor","bookkeeper"), posted_by="human:J. Okafor"),
    dict(date="2026-06-12", type="standard", memo="Stripe payout — 14 residential service calls",
         lines=[("1000","debit",891200,"rev2"),("4000","credit",891200,"rev2")],
         decision="bookkeeper_queue", reason="Settlement un-netted; fees verified",
         proposal=("4000","revenue",0.985,"seen","Settlement un-netted; fees verified"),
         reviewer=("J. Okafor","bookkeeper"), posted_by="human:J. Okafor"),
    dict(date="2026-06-13", type="standard", memo="Gusto payroll run — 12 employees",
         lines=[("6000","debit",2184210,"gus"),("1000","credit",2184210,"gus")],
         decision="bookkeeper_queue", reason="Tied to Gusto register line-by-line",
         proposal=("6000","expense",0.995,"seen","Tied to Gusto register line-by-line"),
         reviewer=("J. Okafor","bookkeeper"), posted_by="human:J. Okafor"),
    dict(date="2026-06-17", type="standard", memo="Ferguson Supply #2214 — parts",
         lines=[("5000","debit",39124,"ferg"),("1000","credit",39124,"ferg")],
         decision="auto_post", reason="Known supplier, under $500 gate",
         proposal=("5000","expense",0.988,"seen","Known supplier, under $500 gate"),
         reviewer=None, posted_by="policy:auto_post"),
    dict(date="2026-06-18", type="standard", memo="QuickFuel fleet — truck 2",
         lines=[("6100","debit",21477,"qf"),("1000","credit",21477,"qf")],
         decision="auto_post", reason="Fuel card feed, matched to truck",
         proposal=("6100","expense",0.982,"seen","Fuel card feed, matched to truck"),
         reviewer=None, posted_by="policy:auto_post"),
    dict(date="2026-06-19", type="standard", memo="Verizon Wireless — field tablets",
         lines=[("6200","debit",18944,"vz"),("1000","credit",18944,"vz")],
         decision="auto_post", reason="Recurring autopay",
         proposal=("6200","expense",0.973,"seen","Recurring autopay"),
         reviewer=None, posted_by="policy:auto_post"),
    dict(date="2026-06-30", type="depreciation", memo="Fleet depreciation — June (drafted by engine)",
         lines=[("6500","debit",145000,"deps"),("1510","credit",145000,"deps")],
         decision="controller_queue", reason="Deterministic schedule, replayable",
         proposal=("6500","expense",0.999,"seen","Deterministic schedule, replayable"),
         reviewer=("M. Reyes","controller"), posted_by="ai_draft:controller_approved"),
    dict(date="2026-06-30", type="accrual", memo="Accrue Johnstone inv #5561 (received, unpaid)",
         lines=[("5000","debit",220850,"accr"),("2000","credit",220850,"accr")],
         decision="controller_queue", reason="Invoice dated in-period, payment out-of-period",
         proposal=("5000","expense",0.94,"seen","Invoice dated in-period, payment out-of-period"),
         reviewer=("M. Reyes","controller"), posted_by="ai_draft:controller_approved"),
]

# Open review-queue items (routed by the policy engine).
QUEUE = [
    dict(txn="q1", date="2026-06-20", vendor="Rodriguez Duct LLC", amt=184000, dir="outflow", doc="rod",
         decision="bookkeeper_queue", lane="bookkeeper_queue",
         reason="Novel vendor never auto-posts, at any amount or confidence",
         proposal=("5100","expense",0.72,"novel","Invoice text suggests fabricated ductwork for Hobbs job → subcontractor COGS")),
    dict(txn="q2", date="2026-06-20", vendor="Texas Comptroller", amt=121240, dir="outflow", doc="tx",
         decision="controller_queue", lane="controller_queue",
         reason="Touches a tax account — never a bookkeeper-level decision",
         proposal=("2200","liability",0.65,"novel","Webfile descriptor pattern → likely sales tax remittance, reduces liability")),
    dict(txn="q3", date="2026-06-23", vendor="Stripe payout — install draw #3", amt=1450000, dir="inflow", doc="dep",
         decision="hard_stop", lane="hard_stop",
         reason="Amount >= $10,000 — human review required, customer notified",
         proposal=("4100","revenue",0.985,"seen","Matches Hobbs Elementary contract draw schedule")),
    dict(txn="q4", date="2026-06-24", vendor="Crane Rental Co", amt=61290, dir="outflow", doc="crn",
         decision="bookkeeper_queue", lane="bookkeeper_queue",
         reason="Over $500 and only a similar (not seen) pattern",
         proposal=("5000","expense",0.83,"similar","Resembles prior equipment-rental charges tied to install jobs")),
    dict(txn="q5", date="2026-06-21", vendor="CHICKFILA #0442", amt=3840, dir="outflow", doc="cfa",
         decision="bookkeeper_queue_lowconf", lane="bookkeeper_queue_lowconf",
         reason="Confidence 0.61 below the 0.80 floor",
         proposal=("6600","expense",0.61,"seen","Descriptor garbled in feed; weak match to crew-lunch pattern")),
    dict(txn="q6", date="2026-06-17", vendor="Ferguson Supply #2231", amt=48620, dir="outflow", doc="ferg2",
         decision="bookkeeper_queue", lane="bookkeeper_queue",
         reason="Daily same-counterparty cumulative cap ($2,000) reached — anti-structuring",
         proposal=("5000","expense",0.99,"seen","Known supplier — but third charge today; cumulative $1,912.60")),
]


def already_seeded(policy, org) -> bool:
    return policy.ledger.entry_count(org) > 0


def reset_org(org_name: str) -> None:
    admin = os.environ["BEDROCK_ADMIN_URL"]
    with psycopg.connect(admin, autocommit=True) as conn:
        row = conn.execute("SELECT org_id FROM orgs WHERE legal_name=%s", (org_name,)).fetchone()
        if not row:
            return
        org = row[0]
        for tbl in ["review_queue", "routing_decisions", "transactions", "proposals",
                    "reconciliations", "policy_daily_cum", "policy_month_counts",
                    "account_error_rates", "journal_lines", "journal_entries",
                    "periods", "audit_log"]:
            conn.execute(f"DELETE FROM {tbl} WHERE org_id=%s", (org,))
    print(f"  reset org data for {org_name}")


def _scaffold(policy, org, *, txn_id, doc_id, doc_raw, txn_date, amount, counterparty,
              direction, proposal, decision, reason, status, posted_entry_id, reviewer):
    """Create transaction + proposal + routing_decision (+ resolved review), so a
    posted entry or a queued item has the full provenance the trail joins over."""
    L = policy.ledger
    code, atype, conf, pat, why = proposal
    with L.pool.connection() as conn:
        conn.execute(
            """INSERT INTO transactions (txn_id, org_id, doc_id, content_sha256, txn_date,
                    amount_minor, counterparty, description, direction)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (org_id, txn_id) DO NOTHING""",
            (txn_id, org, doc_id, _sha256(doc_raw), txn_date, amount, counterparty,
             counterparty, direction))
    proposal_id = policy._insert_proposal(org, txn_id, ProposalIn(code, atype, why, conf, pat))
    with L.pool.connection() as conn:
        decision_id = conn.execute(
            """INSERT INTO routing_decisions (org_id, txn_id, proposal_id, decision, reason,
                    policy_version, effective_thresholds, qa_sampled, status, posted_entry_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,false,%s,%s) RETURNING decision_id""",
            (org, txn_id, proposal_id, decision, reason, POLICY_VERSION, Jsonb(dict(BASE)),
             status, posted_entry_id)).fetchone()[0]
        lane = decision if decision != "auto_post" else "qa_sample"
        if reviewer is not None:
            rid, rrole = reviewer
            conn.execute(
                """INSERT INTO review_queue (org_id, txn_id, decision_id, lane, status,
                        action, reviewer_id, reviewer_role, resolved_at)
                   VALUES (%s,%s,%s,%s,'resolved','approve',%s,%s, now())""",
                (org, txn_id, decision_id, lane, rid, rrole))
        elif status == "queued":
            conn.execute(
                """INSERT INTO review_queue (org_id, txn_id, decision_id, lane, status)
                   VALUES (%s,%s,%s,%s,'open')""", (org, txn_id, decision_id, lane))
    return decision_id


def seed(policy, org, docs) -> None:
    # posted entries
    for i, e in enumerate(POSTED):
        lines = [LineInput(c, s, a, docs[d]) for (c, s, a, d) in e["lines"]]
        entry = policy.ledger.post(org, date.fromisoformat(e["date"]), e["type"], e["memo"],
                                   lines, e["posted_by"])
        if e["decision"] is None:
            continue   # opening balance: migration, no proposal/decision (trail shows migration)
        dr_total = sum(a for (_c, s, a, _d) in e["lines"] if s == "debit")
        primary_doc = e["lines"][0][3]
        _scaffold(policy, org, txn_id=f"seed_{i:02d}", doc_id=docs[primary_doc],
                  doc_raw=DOCS[primary_doc][2], txn_date=date.fromisoformat(e["date"]),
                  amount=dr_total, counterparty=e["memo"],
                  direction=("inflow" if any(c == "1000" and s == "debit" for c, s, a, d in e["lines"]) else "outflow"),
                  proposal=e["proposal"],
                  decision=e["decision"], reason=e["reason"],
                  status=("auto_posted" if e["decision"] == "auto_post" else "resolved"),
                  posted_entry_id=entry["entry_id"], reviewer=e["reviewer"])
    print(f"  posted {len(POSTED)} entries")

    # open queue items
    for q in QUEUE:
        _scaffold(policy, org, txn_id=q["txn"], doc_id=docs[q["doc"]], doc_raw=DOCS[q["doc"]][2],
                  txn_date=date.fromisoformat(q["date"]),
                  amount=q["amt"], counterparty=q["vendor"], direction=q["dir"],
                  proposal=q["proposal"], decision=q["decision"], reason=q["reason"],
                  status="queued", posted_entry_id=None, reviewer=None)
    print(f"  queued {len(QUEUE)} items")


def main() -> None:
    if "--reset" in sys.argv:
        reset_org(ORG_NAME)

    policy = PolicyService()
    org = policy.ledger.ensure_org(ORG_NAME)
    for code, name, atype, nb in COA:
        try:
            policy.ledger.add_account(org, code, name, atype, nb, is_sensitive=(code == "2200"))
        except Exception:
            pass

    if already_seeded(policy, org):
        print(f"org already seeded ({ORG_NAME}); pass --reset to rebuild. org_id={org}")
        policy.close()
        print(org)
        return

    docs = {name: policy.ledger.ingest_document(org, dt, src, raw)
            for name, (dt, src, raw) in DOCS.items()}
    print(f"  ingested {len(docs)} documents")
    seed(policy, org, docs)

    print(f"seeded org_id={org}  trial_balance={policy.ledger.trial_balance(org)}  "
          f"chain={policy.ledger.verify_chain(org)}")
    policy.close()
    print(org)


if __name__ == "__main__":
    main()
