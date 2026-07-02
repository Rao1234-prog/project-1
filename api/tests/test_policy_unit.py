"""Pure routing-logic tests (no database). Ports the decision semantics of
policy.py and covers the determinism requirement.
"""
from __future__ import annotations

import pytest

from bedrock.policy import (AUTO_POST, BASE, BK_QUEUE, BK_QUEUE_LOWCONF, CTRL_QUEUE,
                            HARD_STOP, Proposal, Txn, decide, error_rate, role_may_clear,
                            thresholds)


def _txn(amount, cp="V", day="2026-05-01", direction="outflow", flags=()):
    return Txn("t1", day, amount, cp, "desc", direction, "doc1", flags)


def _prop(atype="expense", conf=0.99, pattern="seen", code="6100", is_sensitive=False):
    return Proposal("t1", code, atype, "why", conf, pattern, is_sensitive=is_sensitive)


# --- error_rate / thresholds ------------------------------------------------
def test_error_rate_needs_sample_of_ten():
    assert error_rate(5, 9) == 0.0          # < 10 decisions -> no tightening yet
    assert error_rate(1, 10) == pytest.approx(0.1)


def test_thresholds_tighten_with_error_rate():
    assert thresholds(0.0) == BASE
    mid = thresholds(0.03)                   # 0.02 < err <= 0.05
    assert mid["auto_post_min_confidence"] == 0.99
    assert mid["auto_post_max_amount"] == 250_00
    high = thresholds(0.06)                   # err > 0.05 -> autonomy off
    assert high["auto_post_min_confidence"] == 1.01


# --- routing branches -------------------------------------------------------
def test_hard_stop_by_amount():
    d = decide(_txn(10_000_00), _prop(), BASE, 0, 0, 9)
    assert d.decision == HARD_STOP


def test_hard_stop_by_fraud_flag():
    d = decide(_txn(100, flags=("velocity",)), _prop(), BASE, 0, 0, 9)
    assert d.decision == HARD_STOP


def test_sensitive_account_goes_to_controller():
    # equity by type
    assert decide(_txn(100), _prop(atype="equity"), BASE, 0, 0, 9).decision == CTRL_QUEUE
    # tax (or any sensitive account) by the explicit is_sensitive flag
    assert decide(_txn(100), _prop(is_sensitive=True), BASE, 0, 0, 9).decision == CTRL_QUEUE


def test_related_party_goes_to_controller():
    d = decide(_txn(100), _prop(), BASE, 0, 0, 9)   # control: not related
    assert d.decision != CTRL_QUEUE
    rp = _txn(100)
    rp.related_party = True
    assert decide(rp, _prop(), BASE, 0, 0, 9).decision == CTRL_QUEUE


def test_cumulative_cap_queues():
    # prior same-day cumulative already near the cap; this one crosses it
    d = decide(_txn(50_00), _prop(), BASE, 1_990_00, 0, 9)
    assert d.decision == BK_QUEUE
    assert "cumulative cap" in d.reason


def test_novel_never_auto_posts():
    d = decide(_txn(100), _prop(pattern="novel"), BASE, 0, 0, 9)
    assert d.decision == BK_QUEUE


def test_auto_post_when_all_conditions_met():
    # month already has enough volume that share cap allows one more auto-post
    d = decide(_txn(400_00), _prop(conf=0.99, pattern="seen"), BASE, 0, 0, 9)
    assert d.decision == AUTO_POST
    assert d.month_auto == 1


def test_share_cap_blocks_auto_post():
    # 9 autos out of 9 so far -> (9+1)/10 = 1.0 > 0.90 -> not auto
    d = decide(_txn(400_00), _prop(conf=0.99, pattern="seen"), BASE, 0, 9, 9)
    assert d.decision != AUTO_POST


def test_low_confidence_lane():
    d = decide(_txn(100), _prop(conf=0.5, pattern="similar"), BASE, 0, 0, 9)
    assert d.decision == BK_QUEUE_LOWCONF


def test_amount_at_or_above_max_not_auto():
    d = decide(_txn(500_00), _prop(conf=0.99, pattern="seen"), BASE, 0, 0, 9)
    assert d.decision != AUTO_POST         # strictly less than max required


# --- determinism (requirement 5) -------------------------------------------
def test_decide_is_deterministic():
    args = (_txn(400_00), _prop(conf=0.99, pattern="seen"), dict(BASE), 100_00, 3, 20)
    first = decide(*args)
    for _ in range(50):
        assert decide(_txn(400_00), _prop(conf=0.99, pattern="seen"),
                      dict(BASE), 100_00, 3, 20) == first


# --- lane authorization -----------------------------------------------------
def test_role_authorization_matrix():
    assert role_may_clear("bookkeeper", "bookkeeper_queue")
    assert not role_may_clear("bookkeeper", "controller_queue")
    assert not role_may_clear("bookkeeper", "hard_stop")
    assert role_may_clear("controller", "hard_stop")
    assert role_may_clear("controller", "bookkeeper_queue")
    assert role_may_clear("admin", "hard_stop")
