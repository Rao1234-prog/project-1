"""Bedrock policy engine — pure routing logic (Phase B).

A faithful, side-effect-free port of the routing core in the ``policy.py``
prototype. The prototype mutated in-memory dicts; here ``decide()`` takes the
prior state as explicit inputs and returns the decision plus the *new* state
values, so the DB-backed ``PolicyService`` can persist them atomically.

This function is deterministic: identical (txn, proposal, prior-state) inputs
always yield an identical decision. That is what requirement 5 tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple

POLICY_VERSION = "1.0.0"

AUTO_POST = "auto_post"
BK_QUEUE = "bookkeeper_queue"
BK_QUEUE_LOWCONF = "bookkeeper_queue_lowconf"
CTRL_QUEUE = "controller_queue"
HARD_STOP = "hard_stop"

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
    txn_id: str
    account_code: str
    account_type: str
    rationale: str
    confidence: float
    pattern_match: str            # seen | similar | novel
    model_id: str = "bedrock-cat-1"


@dataclass
class Txn:
    txn_id: str
    day: str                      # YYYY-MM-DD
    amount_minor: int
    counterparty: str
    description: str
    direction: str
    doc_id: str
    fraud_flags: tuple = ()


def error_rate(corrected: int, decided: int) -> float:
    """Per-account error rate. Needs a sample of >=10 before it tightens."""
    return (corrected / decided) if decided >= 10 else 0.0


def thresholds(account_error_rate: float) -> dict:
    """Effective thresholds for an account given its current error rate."""
    t = dict(BASE)
    if account_error_rate > 0.05:
        t["auto_post_min_confidence"] = 1.01       # autonomy off for this account
    elif account_error_rate > 0.02:
        t["auto_post_min_confidence"] = 0.99
        t["auto_post_max_amount"] = 250_00
    return t


class Decision(NamedTuple):
    decision: str
    reason: str
    # new persistent state values after applying this decision:
    day_cum: int          # daily cumulative for (day, counterparty)
    month_auto: int       # monthly auto-post count
    month_total: int      # monthly total routed count


def decide(txn: Txn, p: Proposal, t: dict,
           day_cum_before: int, month_auto_before: int, month_total_before: int) -> Decision:
    """Route one transaction. Mirrors PolicyEngine.route() exactly.

    ``t`` is the effective thresholds (already adjusted for the account's error
    rate). The three ``*_before`` args are the prior persisted state.
    """
    # total is incremented for EVERY routed transaction (share denominator).
    month_total = month_total_before + 1
    month_auto = month_auto_before
    day_cum = day_cum_before

    if txn.amount_minor >= t["hard_stop_amount"] or txn.fraud_flags:
        return Decision(HARD_STOP, "amount >= $10,000 or fraud heuristic match",
                        day_cum, month_auto, month_total)

    if p.account_type in SENSITIVE_TYPES:
        return Decision(CTRL_QUEUE, "touches equity/tax accounts",
                        day_cum, month_auto, month_total)

    # cumulative cap: increment first (side effect persists), then test.
    day_cum = day_cum_before + txn.amount_minor
    if day_cum > t["daily_counterparty_cum_cap"]:
        return Decision(BK_QUEUE, "daily cumulative cap for counterparty exceeded",
                        day_cum, month_auto, month_total)

    if p.pattern_match == "novel":
        return Decision(BK_QUEUE, "novel vendor/pattern never auto-posts",
                        day_cum, month_auto, month_total)

    share_ok = ((month_auto + 1) / month_total <= t["monthly_auto_post_share_cap"]
                if month_total else True)

    if (p.pattern_match == "seen"
            and p.confidence >= t["auto_post_min_confidence"]
            and txn.amount_minor < t["auto_post_max_amount"]
            and share_ok):
        month_auto += 1
        reason = (f"seen pattern, conf {p.confidence:.2f} >= {t['auto_post_min_confidence']}, "
                  f"< ${t['auto_post_max_amount']/100:.0f}")
        return Decision(AUTO_POST, reason, day_cum, month_auto, month_total)

    if p.confidence < t["queue_min_confidence"]:
        return Decision(BK_QUEUE_LOWCONF, f"confidence {p.confidence:.2f} below floor",
                        day_cum, month_auto, month_total)

    return Decision(BK_QUEUE, "did not meet auto-post criteria",
                    day_cum, month_auto, month_total)


# --- review-lane authorization ---------------------------------------------
# Which reviewer roles may clear which lanes. A bookkeeper cannot clear a
# controller_queue or hard_stop item; a controller can clear anything.
LANE_AUTHORIZATION = {
    "bookkeeper": {"bookkeeper_queue", "bookkeeper_queue_lowconf", "qa_sample"},
    "controller": {"bookkeeper_queue", "bookkeeper_queue_lowconf", "qa_sample",
                   "controller_queue", "hard_stop"},
    "admin": {"bookkeeper_queue", "bookkeeper_queue_lowconf", "qa_sample",
              "controller_queue", "hard_stop"},
}


def role_may_clear(role: str, lane: str) -> bool:
    return lane in LANE_AUTHORIZATION.get(role, set())
