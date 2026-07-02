"""Phase 0 ledger invariants — ported one-for-one from run_phases.py.

Each test corresponds to a numbered check in the prototype's Phase 0 section.
The behavior is identical; the enforcement now lives in Postgres.
"""
from __future__ import annotations

from datetime import date

import pytest

from bedrock.service import LedgerError, LineInput


def _doc(service, org, raw="doc"):
    return service.ingest_document(org, "receipt", "test", raw)


# 1. unbalanced entry must be rejected
def test_unbalanced_entry_rejected(service, seeded_org):
    org = seeded_org
    d = _doc(service, org, "unbalanced-doc")
    with pytest.raises(LedgerError):
        service.post(org, date(2026, 4, 1), "standard", "bad",
                     [LineInput("6100", "debit", 5000, d),
                      LineInput("1000", "credit", 4999, d)], "test")


# 2. provenance is mandatory (a line without a source document is invalid)
def test_line_without_document_rejected(service, seeded_org):
    with pytest.raises(LedgerError):
        LineInput("6100", "debit", 5000, "").validate()


# 3. floats (non-integer amounts) rejected — integer minor units only
def test_float_amount_rejected(service, seeded_org):
    with pytest.raises(LedgerError):
        LineInput("6100", "debit", 49.99, "doc").validate()  # type: ignore[arg-type]


# 4. identical document content is deduped (content-addressed)
def test_document_dedupe(service, seeded_org):
    org = seeded_org
    a = service.ingest_document(org, "receipt", "email_ingest", "Shell 04/02 61.20 card *4411")
    b = service.ingest_document(org, "receipt", "upload", "Shell 04/02 61.20 card *4411")
    assert a == b


# 5. correction via reversal chain; net balances correct; original untouched
def test_reversal_correction_and_immutability(service, seeded_org):
    org = seeded_org
    d = _doc(service, org, "Shell 04/02 61.20 card *4411")
    bad = service.post(org, date(2026, 4, 2), "standard", "Fuel misposted to Meals",
                       [LineInput("6600", "debit", 6120, d),
                        LineInput("1000", "credit", 6120, d)], "bookkeeper")
    service.reverse(org, bad["entry_id"], date(2026, 4, 2), "Reverse mispost", "bookkeeper")
    service.post(org, date(2026, 4, 2), "standard", "Fuel — Shell (corrected)",
                 [LineInput("6100", "debit", 6120, d),
                  LineInput("1000", "credit", 6120, d)], "bookkeeper")
    assert service.balance(org, "6600") == 0
    assert service.balance(org, "6100") == 6120
    assert service.verify_chain(org)


# 6. hash chain verifies clean, and tampering is detected (direct SQL)
def test_hash_chain_detects_tampering(service, seeded_org, admin_conn):
    org = seeded_org
    d = _doc(service, org, "tamper-doc")
    e = service.post(org, date(2026, 4, 2), "standard", "original memo",
                     [LineInput("6100", "debit", 100, d),
                      LineInput("1000", "credit", 100, d)], "bookkeeper")
    assert service.verify_chain(org)

    # direct-SQL tamper (only possible as superuser; the app role has no UPDATE)
    admin_conn.execute("UPDATE journal_entries SET memo='tampered' WHERE entry_id=%s",
                       (e["entry_id"],))
    assert service.verify_chain(org) is False

    # restore the canonical value used at hash time -> chain verifies again
    admin_conn.execute("UPDATE journal_entries SET memo='original memo' WHERE entry_id=%s",
                       (e["entry_id"],))
    assert service.verify_chain(org) is True


# 7. posting into a closed period is rejected
def test_closed_period_rejects_post(service, seeded_org):
    org = seeded_org
    d = _doc(service, org, "period-doc")
    service.post(org, date(2026, 4, 5), "standard", "in April",
                 [LineInput("6100", "debit", 100, d),
                  LineInput("1000", "credit", 100, d)], "bookkeeper")
    service.close_period(org, "2026-04", "controller.demo")
    with pytest.raises(LedgerError):
        service.post(org, date(2026, 4, 30), "standard", "late",
                     [LineInput("6100", "debit", 100, d),
                      LineInput("1000", "credit", 100, d)], "test")


# closing an already-closed period is rejected (prototype close_period guard)
def test_double_close_rejected(service, seeded_org):
    org = seeded_org
    service.close_period(org, "2026-04", "controller.demo")
    with pytest.raises(LedgerError):
        service.close_period(org, "2026-04", "controller.demo")


# empty entry rejected
def test_empty_entry_rejected(service, seeded_org):
    with pytest.raises(LedgerError):
        service.post(seeded_org, date(2026, 4, 1), "standard", "empty", [], "test")


# bad entry_type rejected
def test_bad_entry_type_rejected(service, seeded_org):
    org = seeded_org
    d = _doc(service, org)
    with pytest.raises(LedgerError):
        service.post(org, date(2026, 4, 1), "not_a_type", "x",
                     [LineInput("6100", "debit", 100, d),
                      LineInput("1000", "credit", 100, d)], "test")
