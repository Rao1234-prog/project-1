"""Bedrock close + reconciliation service (Phase C).

The month-end close is gated server-side: the checklist and the adversarial
error hunt are computed here, and ``approve_close`` refuses (raises
``CloseBlocked``) while any blocking finding exists. The UI enforces nothing —
it can only render what this returns and surface the 409.
"""
from __future__ import annotations

from dataclasses import dataclass

from .service import LedgerService

# Accounts that require an approved bank reconciliation before close.
RECON_REQUIRED = ["1000"]   # operating checking


class CloseBlocked(Exception):
    def __init__(self, findings: list[dict]):
        self.findings = findings
        super().__init__("close blocked by findings")


@dataclass
class CloseService:
    ledger: LedgerService

    @property
    def pool(self):
        return self.ledger.pool

    # ---- reconciliation ------------------------------------------------
    def approve_reconciliation(self, org: str, account_code: str, period: str,
                               approved_by: str, approved_role: str) -> dict:
        with self.pool.connection() as conn:
            conn.execute(
                """INSERT INTO reconciliations (org_id, account_code, period_key,
                                                approved_by, approved_role)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (org_id, account_code, period_key)
                   DO UPDATE SET approved_by=EXCLUDED.approved_by,
                                 approved_role=EXCLUDED.approved_role, approved_at=now()""",
                (org, account_code, period, approved_by, approved_role),
            )
            self.ledger._log(conn, org, f"human:{approved_role}", "reconciliation_approved",
                             account_code, {"period": period, "by": approved_by})
        return {"account_code": account_code, "period": period, "reconciled": True}

    def _reconciled(self, conn, org: str, period: str) -> set[str]:
        rows = conn.execute(
            "SELECT account_code FROM reconciliations WHERE org_id=%s AND period_key=%s",
            (org, period),
        ).fetchall()
        return {r[0] for r in rows}

    # ---- adversarial findings + checklist ------------------------------
    def checklist(self, org: str, period: str) -> dict:
        with self.pool.connection() as conn:
            open_items = conn.execute(
                """SELECT count(*) FROM review_queue q
                   JOIN transactions t ON t.org_id=q.org_id AND t.txn_id=q.txn_id
                   WHERE q.org_id=%s AND q.status='open' AND q.lane <> 'qa_sample'
                     AND to_char(t.txn_date,'YYYY-MM')=%s""",
                (org, period),
            ).fetchone()[0]

            reconciled = self._reconciled(conn, org, period)

            # duplicate source documents inside the period (same doc + amount on
            # two different entries)
            dupes = conn.execute(
                """SELECT l.doc_id, l.amount_minor, count(DISTINCT l.entry_id) AS n
                   FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id
                   WHERE l.org_id=%s AND to_char(e.entry_date,'YYYY-MM')=%s
                   GROUP BY l.doc_id, l.amount_minor HAVING count(DISTINCT l.entry_id) > 1""",
                (org, period),
            ).fetchall()

            # recurring accrual: rent (6300) seen in a prior month but not this one
            rent_months = conn.execute(
                """SELECT DISTINCT to_char(e.entry_date,'YYYY-MM') AS m
                   FROM journal_lines l
                   JOIN journal_entries e ON e.entry_id=l.entry_id
                   JOIN accounts a ON a.account_id=l.account_id
                   WHERE l.org_id=%s AND a.code='6300'""",
                (org,),
            ).fetchall()
            rent_set = {r[0] for r in rent_months}

            has_depreciation = conn.execute(
                """SELECT count(*) FROM journal_entries
                   WHERE org_id=%s AND entry_type='depreciation'
                     AND to_char(entry_date,'YYYY-MM')=%s""", (org, period)).fetchone()[0] > 0
            has_accrual = conn.execute(
                """SELECT count(*) FROM journal_entries
                   WHERE org_id=%s AND entry_type='accrual'
                     AND to_char(entry_date,'YYYY-MM')=%s""", (org, period)).fetchone()[0] > 0

            prow = conn.execute(
                "SELECT status FROM periods WHERE org_id=%s AND period_key=%s", (org, period)
            ).fetchone()
            period_status = prow[0] if prow else "open"

        findings: list[dict] = []
        if open_items:
            findings.append({"severity": "HIGH", "check": "review_queue_not_clear",
                             "detail": f"{open_items} transaction(s) still in review — "
                                       f"nothing unposted may remain at close",
                             "blocking": True})
        for code in RECON_REQUIRED:
            if code not in reconciled:
                findings.append({"severity": "HIGH", "check": "bank_account_not_reconciled",
                                 "detail": f"account {code} lacks an approved bank reconciliation",
                                 "blocking": True})
        for doc_id, amount, n in dupes:
            findings.append({"severity": "HIGH", "check": "duplicate_source_document",
                             "detail": f"document {doc_id} with amount {amount} appears on {n} entries",
                             "blocking": True})
        prior = sorted(m for m in rent_set if m < period)
        if prior and period not in rent_set:
            findings.append({"severity": "MED", "check": "missing_recurring_accrual",
                             "detail": "rent present in prior months, absent this month",
                             "blocking": False})

        checklist = [
            {"ok": has_depreciation, "label": "Depreciation drafted from replayable schedule"},
            {"ok": has_accrual, "label": "Accruals drafted"},
            {"ok": open_items == 0, "label": "Review queue clear"},
            {"ok": all(c in reconciled for c in RECON_REQUIRED),
             "label": "Bank reconciliation approved"},
        ]
        blocking = [f for f in findings if f["blocking"]]
        locked = period_status in ("closed", "locked")
        return {"period": period, "checklist": checklist, "findings": findings,
                "blocking": bool(blocking), "can_close": not blocking and not locked,
                "status": period_status, "locked": locked}

    def approve_close(self, org: str, period: str, approved_by: str, approved_role: str) -> dict:
        state = self.checklist(org, period)
        if state["blocking"]:
            raise CloseBlocked([f for f in state["findings"] if f["blocking"]])
        # lock the period: posting into it is now rejected by the period-lock trigger
        self.ledger.lock_period(org, period, f"{approved_by} ({approved_role})")
        with self.pool.connection() as conn:
            self.ledger._log(conn, org, f"human:{approved_role}", "close_approved", period,
                             {"by": approved_by})
        return {"period": period, "status": "locked", "approved_by": approved_by}
