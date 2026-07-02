"""Phase 1 categorization simulation (ported from run_phases.py).

Drives the persistent policy engine + ledger over the same seeded messy pilot
dataset (Cardinal Heating & Air). Queued items are resolved through the review
endpoint (corrected to truth), exactly as a human bookkeeper/controller would.

Returns the stats needed to assert the Phase B gate invariants. It deliberately
asserts nothing itself — callers (the gate test and run_gate.sh) do.
"""
from __future__ import annotations

import random
from datetime import date

from .policy_service import DocumentIn, PolicyService, ProposalIn
from .service import LedgerService, LineInput

COA = [
    ("1000", "Operating Checking", "asset", "debit"),
    ("1200", "Accounts Receivable", "asset", "debit"),
    ("1500", "Equipment", "asset", "debit"),
    ("1510", "Accum. Depreciation", "contra", "credit"),
    ("2000", "Accounts Payable", "liability", "credit"),
    ("2100", "Payroll Liabilities", "liability", "credit"),
    ("2200", "Sales Tax Payable", "liability", "credit"),   # sensitive
    ("3000", "Owner's Equity", "equity", "credit"),
    ("4000", "Service Revenue", "revenue", "credit"),
    ("4100", "Install Revenue", "revenue", "credit"),
    ("5000", "Parts & Materials COGS", "expense", "debit"),
    ("5100", "Subcontractor COGS", "expense", "debit"),
    ("6000", "Payroll Expense", "expense", "debit"),
    ("6100", "Fuel & Vehicle", "expense", "debit"),
    ("6200", "Software & Office", "expense", "debit"),
    ("6300", "Rent", "expense", "debit"),
    ("6400", "Insurance", "expense", "debit"),
    ("6500", "Depreciation Expense", "expense", "debit"),
    ("6600", "Meals", "expense", "debit"),
]

VENDORS = [   # (name, truth_code, pattern, base_conf, lo, hi)  amounts in cents
    ("Shell Fuel", "6100", "seen", 0.99, 2500, 9500),
    ("QuickFuel Fleet", "6100", "seen", 0.98, 8000, 22000),
    ("Ferguson Supply", "5000", "seen", 0.985, 4500, 48000),
    ("Johnstone Supply", "5000", "seen", 0.975, 3000, 42000),
    ("Gusto Payroll", "6000", "seen", 0.995, 180000, 240000),
    ("State Farm", "6400", "seen", 0.99, 61000, 61000),
    ("ServiceTitan", "6200", "seen", 0.99, 39900, 39900),
    ("Verizon", "6200", "seen", 0.97, 18000, 21000),
    ("Chick-fil-A", "6600", "seen", 0.96, 1400, 6200),
    ("NEW: Rodriguez Duct LLC", "5100", "novel", 0.72, 60000, 180000),
    ("NEW: TX Comptroller", "2200", "novel", 0.65, 90000, 140000),
    ("Crane Rental Co", "5000", "similar", 0.83, 25000, 95000),
]
CUSTOMERS = [
    ("Residential service call", "4000", 18000, 65000),
    ("Commercial install draw", "4100", 250000, 1450000),
]

WRONG_POOL = ["5000", "6100", "6200", "6600"]


def load_coa(ledger: LedgerService, org: str) -> None:
    for code, name, atype, nb in COA:
        try:
            ledger.add_account(org, code, name, atype, nb, is_sensitive=(code == "2200"))
        except Exception:
            pass


def simulate(policy: PolicyService, org: str, seed: int = 42) -> dict:
    rng = random.Random(seed)
    L = policy.ledger

    # opening balances (April), then May+June activity
    d0 = L.ingest_document(org, "opening_balance", "manual", "OB 2026-04-30 checking 91,882.10")
    L.post(org, date(2026, 4, 30), "opening_balance", "Opening balances",
           [LineInput("1000", "debit", 9_188_210, d0),
            LineInput("3000", "credit", 9_188_210, d0)], "migration")

    routed = {"auto_post": 0, "bookkeeper_queue": 0, "bookkeeper_queue_lowconf": 0,
              "controller_queue": 0, "hard_stop": 0}
    audit = []      # (ai_code, truth, decision, amount, pattern)
    type_cache: dict[str, str] = {}
    n = 0

    def atype_of(code: str) -> str:
        if code not in type_cache:
            type_cache[code] = L.by_code(org, code)["account_type"]
        return type_cache[code]

    for month, days in [("2026-05", 31), ("2026-06", 30)]:
        for _ in range(120):
            name, truth, pat, conf, lo, hi = rng.choice(VENDORS)
            day = f"{month}-{rng.randint(1, days):02d}"
            amount = rng.randint(lo, hi)
            n += 1
            raw = f"{day} {name} {amount} outflow #{n}"
            p_right = {"seen": 0.965, "similar": 0.85, "novel": 0.70}[pat]
            ai_code = truth if rng.random() < p_right else rng.choice(WRONG_POOL)
            c = min(0.999, max(0.30, rng.gauss(conf if ai_code == truth else conf - 0.25, 0.02)))

            res = policy.ingest_transaction(
                org, day=day, amount_minor=amount, counterparty=name, description=name,
                direction="outflow",
                document=DocumentIn("bank_feed_line", "plaid", raw),
                proposal=ProposalIn(ai_code, atype_of(ai_code), f"Matched pattern for {name}",
                                    round(c, 3), pat))
            routed[res["decision"]] += 1
            audit.append((ai_code, truth, res["decision"], amount, pat))

            # human review resolves queued items, correcting to truth
            _resolve(policy, org, res, ai_code, truth)

        for _ in range(26):
            desc, code, lo, hi = rng.choice(CUSTOMERS)
            day = f"{month}-{rng.randint(1, days):02d}"
            amount = rng.randint(lo, hi)
            n += 1
            raw = f"{day} {desc} {amount} inflow #{n}"
            res = policy.ingest_transaction(
                org, day=day, amount_minor=amount, counterparty=desc, description=desc,
                direction="inflow",
                document=DocumentIn("bank_feed_line", "plaid", raw),
                proposal=ProposalIn(code, "revenue", desc, 0.985, "seen"))
            routed[res["decision"]] += 1
            audit.append((code, code, res["decision"], amount, "seen"))
            _resolve(policy, org, res, code, code)   # revenue proposals are correct

    total = sum(routed.values())
    auto_wrong = sum(1 for a, b, d, _amt, _p in audit if d == "auto_post" and a != b)
    return {
        "routed": routed, "total": total, "audit": audit, "auto_wrong": auto_wrong,
        "auto_share": routed["auto_post"] / total if total else 0.0,
        "trial_balance": L.trial_balance(org), "chain_verified": L.verify_chain(org),
    }


def _resolve(policy: PolicyService, org: str, res: dict, ai_code: str, truth: str) -> None:
    """Resolve a queued/hard-stopped item the way a human would."""
    decision = res["decision"]
    if decision == "auto_post":
        return
    action = "approve" if ai_code == truth else "correct"
    corrected = None if ai_code == truth else truth
    if decision in ("bookkeeper_queue", "bookkeeper_queue_lowconf"):
        policy.submit_review(org, res["txn_id"], action=action, reviewer_id="bk-1",
                             reviewer_role="bookkeeper", corrected_account_code=corrected)
    elif decision in ("controller_queue", "hard_stop"):
        policy.submit_review(org, res["txn_id"], action=action, reviewer_id="ctrl-1",
                             reviewer_role="controller", corrected_account_code=corrected)
