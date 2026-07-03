"""Phase 0 seeded run — mirrors the opening-balance + correction sequence in
run_phases.py and asserts the exit criteria: 4 entries, trial balance zero,
chain verified.
"""
from __future__ import annotations

from datetime import date

from greenledger.service import LineInput


def test_seeded_phase0_run(service, seeded_org):
    org = seeded_org

    # opening balances
    d0 = service.ingest_document(org, "opening_balance", "manual",
                                 "OB 2026-03-31 checking 84,213.55 equity")
    service.post(org, date(2026, 3, 31), "opening_balance", "Opening balances",
                 [LineInput("1000", "debit", 8_421_355, d0),
                  LineInput("3000", "credit", 8_421_355, d0)], "migration")

    # a mispost, its reversal, and the correction (net effect: fuel = 6120)
    dA = service.ingest_document(org, "receipt", "email_ingest", "Shell 04/02 61.20 card *4411")
    bad = service.post(org, date(2026, 4, 2), "standard", "Fuel misposted to Meals",
                       [LineInput("6600", "debit", 6120, dA),
                        LineInput("1000", "credit", 6120, dA)], "bookkeeper")
    service.reverse(org, bad["entry_id"], date(2026, 4, 2), "Reverse mispost", "bookkeeper")
    service.post(org, date(2026, 4, 2), "standard", "Fuel — Shell (corrected)",
                 [LineInput("6100", "debit", 6120, dA),
                  LineInput("1000", "credit", 6120, dA)], "bookkeeper")

    # exit criteria
    assert service.entry_count(org) == 4
    assert service.balance(org, "6600") == 0
    assert service.balance(org, "6100") == 6120
    assert service.trial_balance(org) == 0
    assert service.verify_chain(org) is True

    # period lock closes the books
    service.close_period(org, "2026-04", "controller.demo")
    assert service.period_status(org, date(2026, 4, 15)) == "closed"
