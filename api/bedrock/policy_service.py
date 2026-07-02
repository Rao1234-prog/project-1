"""Bedrock policy engine — persistent service layer (Phase B).

Wraps the pure routing logic in ``policy.py`` with Postgres-backed state so that
everything survives restarts: per-counterparty daily cumulative totals, the
monthly auto-post share, per-account error rates, and the QA sampling of
auto-posts. Nothing that affects a decision lives in process memory.

Key properties:
  * Idempotent — ``ingest_transaction`` dedupes by source-document content hash.
  * Provenant — every decision stores policy version + effective thresholds +
    reason, verbatim, for the paper trail.
  * Deterministic — same (txn, proposal, prior state) always routes the same way.
  * Boundary-preserving — proposals are written through the ``bedrock_ai`` role;
    the ledger post and state writes go through ``bedrock_app``.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Optional

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .db import make_pool, ai_dsn
from .policy import (AUTO_POST, POLICY_VERSION, Decision, Proposal, Txn, decide,
                     error_rate, role_may_clear, thresholds)
from .service import LedgerError, LedgerService, LineInput, _sha256


class PolicyError(Exception):
    """Raised for policy-layer violations (e.g. unauthorized reviewer)."""


class Unauthorized(PolicyError):
    pass


@dataclass
class ProposalIn:
    account_code: str
    account_type: str
    rationale: str
    confidence: float
    pattern_match: str
    model_id: str = "bedrock-cat-1"


@dataclass
class DocumentIn:
    doc_type: str
    source_system: str
    raw: str


def _qa_sampled(content_sha256: str) -> bool:
    """Deterministic 5% QA sample of auto-posts, keyed by content hash so it is
    reproducible and independent of ordering (no RNG -> stays deterministic)."""
    return (int(content_sha256[:8], 16) % 100) < 5


class PolicyService:
    def __init__(self, ledger: LedgerService | None = None,
                 ai_pool: ConnectionPool | None = None,
                 app_dsn: str | None = None, ai_url: str | None = None):
        self.ledger = ledger or LedgerService(dsn=app_dsn)
        self.ai_pool = ai_pool or make_pool(ai_url or ai_dsn())

    def close(self) -> None:
        self.ai_pool.close()
        self.ledger.pool.close()

    # ---- proposal insert via the AI role -------------------------------
    def _insert_proposal(self, org: str, txn_id: str, p: ProposalIn) -> str:
        with self.ai_pool.connection() as conn:
            row = conn.execute(
                """INSERT INTO proposals
                     (org_id, txn_id, account_code, account_type, rationale,
                      confidence, pattern_match, model_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING proposal_id""",
                (org, txn_id, p.account_code, p.account_type, p.rationale,
                 p.confidence, p.pattern_match, p.model_id),
            ).fetchone()
            return str(row[0])

    # ---- ingest + route ------------------------------------------------
    def ingest_transaction(self, org: str, *, day: str, amount_minor: int,
                           counterparty: str, description: str, direction: str,
                           document: DocumentIn, proposal: ProposalIn,
                           txn_id: Optional[str] = None, fraud_flags: tuple = (),
                           cash_account_code: str = "1000") -> dict:
        if isinstance(amount_minor, bool) or not isinstance(amount_minor, int) or amount_minor <= 0:
            raise LedgerError("amounts are positive integer minor units")
        if direction not in ("inflow", "outflow"):
            raise LedgerError("direction must be inflow or outflow")

        doc_id = self.ledger.ingest_document(org, document.doc_type,
                                             document.source_system, document.raw)
        content_sha = _sha256(document.raw)

        with self.ledger.pool.connection() as conn:
            with conn.transaction():
                # Serialize routing per org so state transitions are consistent.
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (org,))

                # --- idempotency: dedupe by content hash --------------------
                existing = conn.execute(
                    """SELECT t.txn_id, d.decision, d.reason, d.effective_thresholds,
                              d.policy_version, d.qa_sampled, d.decision_id, d.status,
                              d.posted_entry_id
                       FROM transactions t
                       JOIN routing_decisions d ON d.org_id=t.org_id AND d.txn_id=t.txn_id
                       WHERE t.org_id=%s AND t.content_sha256=%s""",
                    (org, content_sha),
                ).fetchone()
                if existing:
                    self.ledger._log(conn, org, "system", "dedupe_transaction",
                                     existing[0], {"sha256": content_sha[:12]})
                    return {
                        "deduped": True, "txn_id": existing[0], "decision": existing[1],
                        "reason": existing[2], "effective_thresholds": existing[3],
                        "policy_version": existing[4], "qa_sampled": existing[5],
                        "decision_id": str(existing[6]), "status": existing[7],
                        "posted_entry_id": str(existing[8]) if existing[8] else None,
                    }

                tid = txn_id or f"txn_{uuid.uuid4().hex[:12]}"

                conn.execute(
                    """INSERT INTO transactions
                         (txn_id, org_id, doc_id, content_sha256, txn_date, amount_minor,
                          counterparty, description, direction, fraud_flags)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (tid, org, doc_id, content_sha, day, amount_minor, counterparty,
                     description, direction, list(fraud_flags)),
                )

                proposal_id = self._insert_proposal(org, tid, proposal)

                # --- load prior persistent state ----------------------------
                corr = conn.execute(
                    "SELECT corrected, decided FROM account_error_rates WHERE org_id=%s AND account_code=%s",
                    (org, proposal.account_code),
                ).fetchone()
                corrected, decided = (corr[0], corr[1]) if corr else (0, 0)
                t = thresholds(error_rate(corrected, decided))

                month = day[:7]
                dc = conn.execute(
                    "SELECT cum_minor FROM policy_daily_cum WHERE org_id=%s AND day=%s AND counterparty=%s",
                    (org, day, counterparty),
                ).fetchone()
                day_cum_before = dc[0] if dc else 0
                mc = conn.execute(
                    "SELECT auto_count, total_count FROM policy_month_counts WHERE org_id=%s AND month=%s",
                    (org, month),
                ).fetchone()
                month_auto_before, month_total_before = (mc[0], mc[1]) if mc else (0, 0)

                # --- decide -------------------------------------------------
                txn = Txn(tid, day, amount_minor, counterparty, description, direction,
                          doc_id, tuple(fraud_flags))
                prop = Proposal(tid, proposal.account_code, proposal.account_type,
                                proposal.rationale, proposal.confidence, proposal.pattern_match,
                                proposal.model_id)
                d: Decision = decide(txn, prop, t, day_cum_before,
                                     month_auto_before, month_total_before)

                # --- persist updated state ----------------------------------
                conn.execute(
                    """INSERT INTO policy_month_counts (org_id, month, auto_count, total_count)
                       VALUES (%s,%s,%s,%s)
                       ON CONFLICT (org_id, month)
                       DO UPDATE SET auto_count=EXCLUDED.auto_count, total_count=EXCLUDED.total_count""",
                    (org, month, d.month_auto, d.month_total),
                )
                conn.execute(
                    """INSERT INTO policy_daily_cum (org_id, day, counterparty, cum_minor)
                       VALUES (%s,%s,%s,%s)
                       ON CONFLICT (org_id, day, counterparty)
                       DO UPDATE SET cum_minor=EXCLUDED.cum_minor""",
                    (org, day, counterparty, d.day_cum),
                )

                qa = _qa_sampled(content_sha)

                # --- post (auto) or queue (everything else) -----------------
                posted_entry_id = None
                if d.decision == AUTO_POST:
                    posted_entry_id = self._post_txn(conn, org, tid, day, description,
                                                     direction, amount_minor,
                                                     proposal.account_code, doc_id,
                                                     cash_account_code, "policy:auto_post")
                    status = "auto_posted"
                else:
                    status = "queued"

                decision_id = conn.execute(
                    """INSERT INTO routing_decisions
                         (org_id, txn_id, proposal_id, decision, reason, policy_version,
                          effective_thresholds, qa_sampled, status, posted_entry_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING decision_id""",
                    (org, tid, proposal_id, d.decision, d.reason, POLICY_VERSION,
                     Jsonb(t), qa, status, posted_entry_id),
                ).fetchone()[0]

                if d.decision == AUTO_POST:
                    if qa:
                        conn.execute(
                            """INSERT INTO review_queue (org_id, txn_id, decision_id, lane, status)
                               VALUES (%s,%s,%s,'qa_sample','open')""",
                            (org, tid, decision_id),
                        )
                else:
                    conn.execute(
                        """INSERT INTO review_queue (org_id, txn_id, decision_id, lane, status)
                           VALUES (%s,%s,%s,%s,'open')""",
                        (org, tid, decision_id, d.decision),
                    )

                return {
                    "deduped": False, "txn_id": tid, "decision": d.decision,
                    "reason": d.reason, "effective_thresholds": t,
                    "policy_version": POLICY_VERSION, "qa_sampled": qa,
                    "decision_id": str(decision_id), "status": status,
                    "posted_entry_id": posted_entry_id,
                }

    def _post_txn(self, conn, org, txn_id, day, description, direction, amount_minor,
                  account_code, doc_id, cash_code, posted_by) -> str:
        """Post a balanced two-line entry for a transaction."""
        if direction == "outflow":       # money out: Dr expense/account, Cr cash
            lines = [LineInput(account_code, "debit", amount_minor, doc_id, txn_id),
                     LineInput(cash_code, "credit", amount_minor, doc_id, txn_id)]
        else:                            # money in: Dr cash, Cr revenue/account
            lines = [LineInput(cash_code, "debit", amount_minor, doc_id, txn_id),
                     LineInput(account_code, "credit", amount_minor, doc_id, txn_id)]
        y, m, dd = map(int, day.split("-"))
        return self.ledger.insert_entry_on_conn(conn, org, date(y, m, dd), "standard",
                                                description, lines, posted_by)

    # ---- review --------------------------------------------------------
    def submit_review(self, org: str, txn_id: str, *, action: str, reviewer_id: str,
                      reviewer_role: str, corrected_account_code: Optional[str] = None,
                      cash_account_code: str = "1000") -> dict:
        if action not in ("approve", "correct", "reject"):
            raise PolicyError("action must be approve, correct, or reject")
        if action == "correct" and not corrected_account_code:
            raise PolicyError("correct requires corrected_account_code")

        with self.ledger.pool.connection() as conn:
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (org,))

                item = conn.execute(
                    """SELECT q.queue_id, q.lane, q.status, d.decision_id, d.decision,
                              p.account_code, t.txn_date, t.direction, t.amount_minor, t.doc_id
                       FROM review_queue q
                       JOIN routing_decisions d ON d.decision_id=q.decision_id
                       JOIN proposals p ON p.proposal_id=d.proposal_id
                       JOIN transactions t ON t.org_id=q.org_id AND t.txn_id=q.txn_id
                       WHERE q.org_id=%s AND q.txn_id=%s AND q.status='open'
                       ORDER BY q.created_at DESC LIMIT 1""",
                    (org, txn_id),
                ).fetchone()
                if item is None:
                    raise PolicyError(f"no open review for transaction {txn_id}")
                (queue_id, lane, _st, decision_id, _decision,
                 proposed_code, txn_date, direction, amount_minor, doc_id) = item

                # --- server-side lane authorization -------------------------
                if not role_may_clear(reviewer_role, lane):
                    raise Unauthorized(
                        f"role '{reviewer_role}' may not clear a '{lane}' item")

                # --- error-rate feedback (per org, per proposed account) ----
                # approve => not corrected; correct/reject => the proposal was wrong.
                was_corrected = action != "approve"
                conn.execute(
                    """INSERT INTO account_error_rates (org_id, account_code, corrected, decided)
                       VALUES (%s,%s,%s,1)
                       ON CONFLICT (org_id, account_code)
                       DO UPDATE SET corrected = account_error_rates.corrected + %s,
                                     decided   = account_error_rates.decided + 1""",
                    (org, proposed_code, 1 if was_corrected else 0, 1 if was_corrected else 0),
                )

                # --- post to the ledger per the reviewer's action -----------
                posted_entry_id = None
                if action in ("approve", "correct"):
                    final_code = proposed_code if action == "approve" else corrected_account_code
                    posted_entry_id = self._post_txn(
                        conn, org, txn_id, txn_date.isoformat(),
                        f"review:{action}", direction, amount_minor, final_code, str(doc_id),
                        cash_account_code, f"human:{reviewer_role}")
                    new_status = "resolved"
                else:  # reject -> nothing posted
                    new_status = "rejected"

                conn.execute(
                    """UPDATE routing_decisions SET status=%s, posted_entry_id=%s
                       WHERE decision_id=%s""",
                    (new_status, posted_entry_id, decision_id),
                )
                conn.execute(
                    """UPDATE review_queue
                       SET status='resolved', action=%s, reviewer_id=%s, reviewer_role=%s,
                           corrected_account_code=%s, resolved_at=now()
                       WHERE queue_id=%s""",
                    (action, reviewer_id, reviewer_role, corrected_account_code, queue_id),
                )
                self.ledger._log(conn, org, f"human:{reviewer_role}", f"review_{action}",
                                 txn_id, {"lane": lane, "reviewer": reviewer_id})

                return {"txn_id": txn_id, "action": action, "status": new_status,
                        "lane": lane, "posted_entry_id": posted_entry_id}

    # ---- reads ---------------------------------------------------------
    def queue(self, org: str, lane: Optional[str] = None) -> list[dict]:
        from psycopg.rows import dict_row
        sql = """SELECT q.txn_id, q.lane, q.status, d.decision, d.reason,
                        d.effective_thresholds, d.policy_version
                 FROM review_queue q
                 JOIN routing_decisions d ON d.decision_id=q.decision_id
                 WHERE q.org_id=%s AND q.status='open'"""
        params: tuple = (org,)
        if lane:
            sql += " AND q.lane=%s"
            params = (org, lane)
        sql += " ORDER BY q.created_at"
        with self.ledger.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            return [dict(r) for r in cur.execute(sql, params).fetchall()]

    def account_error(self, org: str, account_code: str) -> dict:
        with self.ledger.pool.connection() as conn:
            row = conn.execute(
                "SELECT corrected, decided FROM account_error_rates WHERE org_id=%s AND account_code=%s",
                (org, account_code),
            ).fetchone()
        corrected, decided = (row[0], row[1]) if row else (0, 0)
        return {"account_code": account_code, "corrected": corrected, "decided": decided,
                "error_rate": error_rate(corrected, decided),
                "effective_thresholds": thresholds(error_rate(corrected, decided))}
