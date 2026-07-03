"""
GreenLedger policy engine — Phase 1 trust boundary (brief §5, Deliverable 2).
Deterministic router. The AI proposes; only this module may authorize an auto-post.
"""
from dataclasses import dataclass, field
from collections import defaultdict

POLICY_VERSION = "1.0.0"

AUTO_POST, BK_QUEUE, BK_QUEUE_LOWCONF, CTRL_QUEUE, HARD_STOP = (
    "auto_post", "bookkeeper_queue", "bookkeeper_queue_lowconf",
    "controller_queue", "hard_stop")

BASE = dict(
    auto_post_max_amount=500_00,
    hard_stop_amount=10_000_00,
    auto_post_min_confidence=0.97,
    queue_min_confidence=0.80,
    daily_counterparty_cum_cap=2_000_00,
    monthly_auto_post_share_cap=0.90,
)

SENSITIVE_TYPES = {"equity", "tax"}   # never bookkeeper-level auto decisions

@dataclass
class Proposal:
    txn_id: str; account_code: str; account_type: str
    rationale: str; confidence: float; pattern_match: str  # seen|similar|novel
    model_id: str = "greenledger-cat-1"

@dataclass
class Txn:
    txn_id: str; day: str; amount_minor: int; counterparty: str
    description: str; direction: str; doc_id: str; fraud_flags: tuple = ()

class PolicyEngine:
    def __init__(self):
        self.day_cum = defaultdict(int)            # (day, counterparty) -> cents
        self.month_counts = defaultdict(lambda: [0, 0])  # month -> [auto, total]
        self.corrections = defaultdict(lambda: [0, 0])   # account -> [corrected, decided]

    def record_review(self, account_code: str, corrected: bool):
        c = self.corrections[account_code]
        c[1] += 1
        if corrected: c[0] += 1

    def error_rate(self, account_code: str) -> float:
        c = self.corrections[account_code]
        return (c[0] / c[1]) if c[1] >= 10 else 0.0   # need sample before tightening

    def thresholds(self, account_code: str) -> dict:
        t = dict(BASE)
        err = self.error_rate(account_code)
        if err > 0.05:
            t["auto_post_min_confidence"] = 1.01      # autonomy off for this account
        elif err > 0.02:
            t["auto_post_min_confidence"] = 0.99
            t["auto_post_max_amount"] = 250_00
        return t

    def route(self, txn: Txn, p: Proposal) -> tuple[str, str]:
        t = self.thresholds(p.account_code)
        month = txn.day[:7]
        self.month_counts[month][1] += 1

        if txn.amount_minor >= t["hard_stop_amount"] or txn.fraud_flags:
            return HARD_STOP, "amount >= $10,000 or fraud heuristic match"
        if p.account_type in SENSITIVE_TYPES:
            return CTRL_QUEUE, "touches equity/tax accounts"

        self.day_cum[(txn.day, txn.counterparty)] += txn.amount_minor
        if self.day_cum[(txn.day, txn.counterparty)] > t["daily_counterparty_cum_cap"]:
            return BK_QUEUE, "daily cumulative cap for counterparty exceeded"

        if p.pattern_match == "novel":
            return BK_QUEUE, "novel vendor/pattern never auto-posts"

        auto, total = self.month_counts[month]
        share_ok = (auto + 1) / total <= t["monthly_auto_post_share_cap"] if total else True

        if (p.pattern_match == "seen"
                and p.confidence >= t["auto_post_min_confidence"]
                and txn.amount_minor < t["auto_post_max_amount"]
                and share_ok):
            self.month_counts[month][0] += 1
            return AUTO_POST, f"seen pattern, conf {p.confidence:.2f} >= {t['auto_post_min_confidence']}, < ${t['auto_post_max_amount']/100:.0f}"

        if p.confidence < t["queue_min_confidence"]:
            return BK_QUEUE_LOWCONF, f"confidence {p.confidence:.2f} below floor"
        return BK_QUEUE, "did not meet auto-post criteria"


# ---- Phase 2: adversarial pre-close check ------------------------------
def adversarial_check(ledger, month: str) -> list[dict]:
    """
    Independent second pass. Deterministic heuristics stand in for the
    independent model; interface and blocking semantics are the real design.
    """
    findings = []
    ents = [e for e in ledger.entries if e.entry_date.isoformat()[:7] == month]

    # duplicate source docs inside the month
    seen = {}
    for e in ents:
        for l in e.lines:
            key = (l.doc_id, l.amount_minor)
            if key in seen and seen[key] != e.entry_id:
                findings.append(dict(severity="HIGH", check="duplicate_source_document",
                                     detail=f"{e.entry_id} and {seen[key]} share doc+amount",
                                     blocking=True))
            seen[key] = e.entry_id

    # unreconciled balance-sheet accounts (caller passes recon status via ledger attr)
    for code in getattr(ledger, "recon_required", []):
        if code not in getattr(ledger, "reconciled", set()):
            findings.append(dict(severity="HIGH", check="bank_account_not_reconciled",
                                 detail=f"account {code} lacks approved reconciliation",
                                 blocking=True))

    # missing recurring accrual: rent seen in prior months but absent now
    rent = ledger.by_code("6300")
    months_with_rent = {e.entry_date.isoformat()[:7] for e in ledger.entries
                        for l in e.lines if l.account_id == rent.account_id}
    prior = sorted(m for m in months_with_rent if m < month)
    if prior and month not in months_with_rent:
        findings.append(dict(severity="MED", check="missing_recurring_accrual",
                             detail="rent present in prior months, absent this month",
                             blocking=False))
    return findings
