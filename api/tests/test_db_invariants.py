"""Database-level enforcement tests (new for Phase A).

These prove the invariants hold at the *schema* level, independent of the
service layer — a mis-wired or compromised app still cannot mutate a posted
entry or slip an unbalanced entry past the deferred constraint.
"""
from __future__ import annotations

from datetime import date

import psycopg
import pytest

from bedrock.service import LineInput


def _posted_entry(service, org):
    d = service.ingest_document(org, "receipt", "test", "db-inv-doc")
    e = service.post(org, date(2026, 5, 1), "standard", "posted",
                     [LineInput("6100", "debit", 100, d),
                      LineInput("1000", "credit", 100, d)], "bookkeeper")
    return e["entry_id"], d


# --- required: UPDATE on journal_entries fails at the DB level --------------
def test_update_on_journal_entries_denied(service, seeded_org, app_url):
    org = seeded_org
    entry_id, _ = _posted_entry(service, org)
    with psycopg.connect(app_url, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE journal_entries SET memo='hacked' WHERE entry_id=%s",
                         (entry_id,))


def test_delete_on_journal_lines_denied(service, seeded_org, app_url):
    org = seeded_org
    entry_id, _ = _posted_entry(service, org)
    with psycopg.connect(app_url, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM journal_lines WHERE entry_id=%s", (entry_id,))


# --- required: two simultaneous unbalanced inserts both fail ----------------
def test_two_simultaneous_unbalanced_inserts_both_fail(service, seeded_org, app_url):
    org = seeded_org
    d = service.ingest_document(org, "receipt", "test", "concurrency-doc")
    cash = service.by_code(org, "1000")["account_id"]
    fuel = service.by_code(org, "6100")["account_id"]

    # Two independent connections, each in its own (non-autocommit) transaction.
    c1 = psycopg.connect(app_url)
    c2 = psycopg.connect(app_url)
    try:
        for c in (c1, c2):
            eid = c.execute(
                """INSERT INTO journal_entries
                     (org_id, entry_date, entry_type, memo, posted_by_policy)
                   VALUES (%s,%s,'standard','concurrent bad','test') RETURNING entry_id""",
                (org, date(2026, 5, 15)),
            ).fetchone()[0]
            # deliberately unbalanced: debit 5000 vs credit 4999
            c.execute(
                """INSERT INTO journal_lines
                     (entry_id, org_id, account_id, side, amount_minor, doc_id)
                   VALUES (%s,%s,%s,'debit',5000,%s)""", (eid, org, fuel, d))
            c.execute(
                """INSERT INTO journal_lines
                     (entry_id, org_id, account_id, side, amount_minor, doc_id)
                   VALUES (%s,%s,%s,'credit',4999,%s)""", (eid, org, cash, d))

        # Both are now pending, unbalanced. Committing either must fail at the
        # deferred balance-check constraint.
        failures = 0
        for c in (c1, c2):
            try:
                c.commit()
            except psycopg.errors.CheckViolation:
                failures += 1
                c.rollback()
        assert failures == 2
    finally:
        c1.close()
        c2.close()

    # And nothing leaked into the ledger.
    assert service.trial_balance(org) == 0
    assert service.entry_count(org) == 0


# --- throughline constraint: the AI role may write ONLY proposals ----------
def test_ai_role_cannot_write_ledger(service, seeded_org):
    import os
    ai_url = os.environ.get("BEDROCK_AI_URL")
    if not ai_url:
        pytest.skip("BEDROCK_AI_URL not set")
    org = seeded_org
    with psycopg.connect(ai_url, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                """INSERT INTO journal_entries
                     (org_id, entry_date, entry_type, memo, posted_by_policy)
                   VALUES (%s,'2026-05-01','standard','x','ai')""", (org,))
        # but it CAN write a proposal
        conn.execute(
            """INSERT INTO proposals
                 (org_id, txn_id, account_code, account_type, rationale, confidence, pattern_match)
               VALUES (%s,'t1','6100','expense','fuel',0.99,'seen')""", (org,))
