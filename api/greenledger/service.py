"""GreenLedger ledger engine — persistent service layer (Phase A).

A faithful reimplementation of the validated ``ledger.py`` prototype over the
Postgres schema in ``db/sql``. The behavioral contract is identical; the
difference is *where* each invariant lives:

    prototype (in-memory)              this service (Postgres)
    ----------------------             ----------------------------------------
    balance check in post()            deferred balance-check constraint trigger
    append-only by convention          UPDATE/DELETE revoked from the app role
    doc_id checked in __post_init__    journal_lines.doc_id NOT NULL + FK
    hash chain in Python               per-org hash chain in a COMMIT trigger
    period dict lookup                 period-lock BEFORE INSERT trigger
    int amounts by dataclass check     BIGINT + CHECK (amount_minor > 0)

No AI anywhere in this module (the AI writes only to the ``proposals`` table,
and only via the ``greenledger_ai`` role).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .db import make_pool

ENTRY_TYPES = {"standard", "accrual", "depreciation", "reclass", "reversal", "opening_balance"}


class LedgerError(Exception):
    """Raised for any ledger-invariant violation (mirrors the prototype)."""


@dataclass(frozen=True)
class LineInput:
    """One journal line, addressed by account *code* (as callers think of it)."""
    account_code: str
    side: str
    amount_minor: int
    doc_id: str
    txn_id: Optional[str] = None

    def validate(self) -> None:
        if self.side not in ("debit", "credit"):
            raise LedgerError("bad side")
        # bool is an int subclass; exclude it. Floats/str are rejected here just
        # as the DB's BIGINT column would reject them — but with a clean error.
        if isinstance(self.amount_minor, bool) or not isinstance(self.amount_minor, int):
            raise LedgerError("amounts are positive integer minor units")
        if self.amount_minor <= 0:
            raise LedgerError("amounts are positive integer minor units")
        if not self.doc_id:
            raise LedgerError("provenance required: doc_id missing")


def _sha256(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class LedgerService:
    """Double-entry ledger over Postgres. All amounts are integer minor units."""

    def __init__(self, pool: ConnectionPool | None = None, dsn: str | None = None):
        self.pool = pool or make_pool(dsn)

    # ---- orgs ----------------------------------------------------------
    def ensure_org(self, name: str) -> str:
        with self.pool.connection() as conn:
            row = conn.execute(
                "INSERT INTO orgs(legal_name) VALUES (%s) ON CONFLICT (legal_name) DO NOTHING RETURNING org_id",
                (name,),
            ).fetchone()
            if row is None:  # already exists
                row = conn.execute("SELECT org_id FROM orgs WHERE legal_name=%s", (name,)).fetchone()
            return str(row[0])

    def list_orgs(self) -> list[dict]:
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                "SELECT org_id, legal_name AS name FROM orgs ORDER BY legal_name").fetchall()
        return [{"org_id": str(r["org_id"]), "name": r["name"]} for r in rows]

    # ---- chart of accounts --------------------------------------------
    def add_account(self, org: str, code: str, name: str, account_type: str,
                    normal_balance: str, is_sensitive: bool = False) -> str:
        try:
            with self.pool.connection() as conn:
                row = conn.execute(
                    """INSERT INTO accounts(org_id, code, name, account_type, normal_balance, is_sensitive)
                       VALUES (%s,%s,%s,%s,%s,%s) RETURNING account_id""",
                    (org, code, name, account_type, normal_balance, is_sensitive),
                ).fetchone()
                return str(row[0])
        except psycopg.errors.UniqueViolation as e:
            raise LedgerError(f"duplicate account code {code}") from e

    def _account_ids(self, conn, org: str, codes: Sequence[str]) -> dict[str, str]:
        rows = conn.execute(
            "SELECT code, account_id FROM accounts WHERE org_id=%s AND code = ANY(%s)",
            (org, list(set(codes))),
        ).fetchall()
        found = {r[0]: str(r[1]) for r in rows}
        for c in codes:
            if c not in found:
                raise LedgerError(f"no account {c}")
        return found

    def by_code(self, org: str, code: str) -> dict:
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(
                "SELECT * FROM accounts WHERE org_id=%s AND code=%s", (org, code)
            ).fetchone()
        if not row:
            raise LedgerError(f"no account {code}")
        return {k: (str(v) if k in ("account_id", "org_id") else v) for k, v in row.items()}

    # ---- documents (immutable, content-addressed, deduped) ------------
    def ingest_document(self, org: str, doc_type: str, source_system: str, raw: str) -> str:
        sha = _sha256(raw)
        with self.pool.connection() as conn:
            row = conn.execute(
                """INSERT INTO source_documents(org_id, doc_type, source_system, raw, content_sha256)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (org_id, content_sha256) DO NOTHING
                   RETURNING doc_id""",
                (org, doc_type, source_system, raw, sha),
            ).fetchone()
            if row is None:  # identical content already ingested -> dedupe
                row = conn.execute(
                    "SELECT doc_id FROM source_documents WHERE org_id=%s AND content_sha256=%s",
                    (org, sha),
                ).fetchone()
                self._log(conn, org, "system", "dedupe_document", str(row[0]), {"sha256": sha})
            else:
                self._log(conn, org, "system", "ingest_document", str(row[0]),
                          {"type": doc_type, "sha256": sha[:12]})
            return str(row[0])

    # ---- periods -------------------------------------------------------
    @staticmethod
    def period_key(d: date) -> str:
        return f"{d.year:04d}-{d.month:02d}"

    def period_status(self, org: str, d: date) -> str:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT status FROM periods WHERE org_id=%s AND period_key=%s",
                (org, self.period_key(d)),
            ).fetchone()
        return row[0] if row else "open"

    def close_period(self, org: str, key: str, closed_by: str) -> None:
        with self.pool.connection() as conn:
            cur = conn.execute(
                "SELECT status FROM periods WHERE org_id=%s AND period_key=%s", (org, key)
            ).fetchone()
            if cur and cur[0] in ("closed", "locked"):
                raise LedgerError(f"period {key} already closed")
            conn.execute(
                """INSERT INTO periods(org_id, period_key, status, closed_by, closed_at)
                   VALUES (%s,%s,'closed',%s, now())
                   ON CONFLICT (org_id, period_key)
                   DO UPDATE SET status='closed', closed_by=EXCLUDED.closed_by, closed_at=now()""",
                (org, key, closed_by),
            )
            self._log(conn, org, "human", "close_period", key, {"closed_by": closed_by})

    def lock_period(self, org: str, key: str, locked_by: str) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                """INSERT INTO periods(org_id, period_key, status, closed_by, closed_at)
                   VALUES (%s,%s,'locked',%s, now())
                   ON CONFLICT (org_id, period_key)
                   DO UPDATE SET status='locked', closed_by=EXCLUDED.closed_by, closed_at=now()""",
                (org, key, locked_by),
            )
            self._log(conn, org, "human", "lock_period", key, {"locked_by": locked_by})

    # ---- posting (the only write path) --------------------------------
    def post(self, org: str, entry_date: date, entry_type: str, memo: str,
             lines: list[LineInput], posted_by_policy: str,
             reverses: Optional[str] = None) -> dict:
        if entry_type not in ENTRY_TYPES:
            raise LedgerError("bad entry_type")
        if not lines:
            raise LedgerError("empty entry")
        for ln in lines:
            ln.validate()
        with self.pool.connection() as conn:
            code_to_id = self._account_ids(conn, org, [l.account_code for l in lines])
            resolved = [
                (code_to_id[l.account_code], l.side, l.amount_minor, l.doc_id, l.txn_id)
                for l in lines
            ]
            return self._insert_entry(conn, org, entry_date, entry_type, memo,
                                      resolved, posted_by_policy, reverses)

    def _insert_entry(self, conn, org: str, entry_date: date, entry_type: str, memo: str,
                      resolved_lines: list[tuple], posted_by_policy: str,
                      reverses: Optional[str]) -> dict:
        """Insert an entry + lines by account_id. Balance/hash finalize at COMMIT."""
        try:
            with conn.transaction():
                eid = conn.execute(
                    """INSERT INTO journal_entries
                         (org_id, entry_date, entry_type, memo, posted_by_policy, reverses)
                       VALUES (%s,%s,%s,%s,%s,%s) RETURNING entry_id""",
                    (org, entry_date, entry_type, memo, posted_by_policy, reverses),
                ).fetchone()[0]
                with conn.cursor() as cur:
                    cur.executemany(
                        """INSERT INTO journal_lines
                             (entry_id, org_id, account_id, side, amount_minor, doc_id, txn_id)
                           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                        [(eid, org, aid, side, amt, doc, txn)
                         for (aid, side, amt, doc, txn) in resolved_lines],
                    )
                debits = sum(a for (_, s, a, _, _) in resolved_lines if s == "debit")
                self._log(conn, org, posted_by_policy, "post_entry", str(eid),
                          {"type": entry_type, "dr": debits, "memo": memo[:60]})
            # transaction committed -> deferred balance + hash-chain triggers ran
        except (psycopg.errors.RaiseException,
                psycopg.errors.CheckViolation,
                psycopg.errors.ForeignKeyViolation,
                psycopg.errors.NotNullViolation,
                psycopg.errors.InsufficientPrivilege) as e:
            raise LedgerError(str(e).strip().splitlines()[0]) from e

        row = conn.execute(
            """SELECT entry_id, entry_type, memo, entry_hash, prev_hash, chain_seq, reverses
               FROM journal_entries WHERE entry_id=%s""",
            (eid,),
        ).fetchone()
        return {
            "entry_id": str(row[0]), "entry_type": row[1], "memo": row[2],
            "entry_hash": row[3], "prev_hash": row[4], "chain_seq": row[5],
            "reverses": str(row[6]) if row[6] else None,
        }

    def insert_entry_on_conn(self, conn, org: str, entry_date: date, entry_type: str,
                             memo: str, lines: list[LineInput], posted_by_policy: str,
                             reverses: Optional[str] = None) -> str:
        """Insert an entry + lines on a caller-supplied connection, returning the
        entry_id. The caller owns the transaction; the deferred balance and
        hash-chain triggers fire at the caller's COMMIT. Used by the policy engine
        so a routing decision and its auto-post land atomically.
        """
        if entry_type not in ENTRY_TYPES:
            raise LedgerError("bad entry_type")
        if not lines:
            raise LedgerError("empty entry")
        for ln in lines:
            ln.validate()
        code_to_id = self._account_ids(conn, org, [l.account_code for l in lines])
        eid = conn.execute(
            """INSERT INTO journal_entries
                 (org_id, entry_date, entry_type, memo, posted_by_policy, reverses)
               VALUES (%s,%s,%s,%s,%s,%s) RETURNING entry_id""",
            (org, entry_date, entry_type, memo, posted_by_policy, reverses),
        ).fetchone()[0]
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO journal_lines
                     (entry_id, org_id, account_id, side, amount_minor, doc_id, txn_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                [(eid, org, code_to_id[l.account_code], l.side, l.amount_minor, l.doc_id, l.txn_id)
                 for l in lines],
            )
        debits = sum(l.amount_minor for l in lines if l.side == "debit")
        self._log(conn, org, posted_by_policy, "post_entry", str(eid),
                  {"type": entry_type, "dr": debits, "memo": memo[:60]})
        return str(eid)

    def reverse(self, org: str, entry_id: str, entry_date: date, memo: str, by: str) -> dict:
        with self.pool.connection() as conn:
            orig = conn.execute(
                "SELECT entry_id FROM journal_entries WHERE entry_id=%s AND org_id=%s",
                (entry_id, org),
            ).fetchone()
            if orig is None:
                raise LedgerError("no such entry")
            lines = conn.execute(
                """SELECT account_id, side, amount_minor, doc_id, txn_id
                   FROM journal_lines WHERE entry_id=%s ORDER BY line_id""",
                (entry_id,),
            ).fetchall()
            flipped = [
                (str(aid), "credit" if side == "debit" else "debit", amt, str(doc), txn)
                for (aid, side, amt, doc, txn) in lines
            ]
            return self._insert_entry(conn, org, entry_date, "reversal", memo,
                                      flipped, by, reverses=entry_id)

    # ---- reads ---------------------------------------------------------
    def balance(self, org: str, code: str, as_of: Optional[date] = None) -> int:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT account_balance(%s,%s,%s)", (org, code, as_of)).fetchone()
        return int(row[0])

    def trial_balance(self, org: str) -> int:
        with self.pool.connection() as conn:
            return int(conn.execute("SELECT trial_balance(%s)", (org,)).fetchone()[0])

    def verify_chain(self, org: str) -> bool:
        with self.pool.connection() as conn:
            return bool(conn.execute("SELECT verify_chain(%s)", (org,)).fetchone()[0])

    def entry_count(self, org: str) -> int:
        with self.pool.connection() as conn:
            return int(conn.execute(
                "SELECT count(*) FROM journal_entries WHERE org_id=%s", (org,)
            ).fetchone()[0])

    def provenance(self, org: str, entry_id: str) -> list[dict]:
        """Line-level provenance: line -> account -> source document."""
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                """SELECT a.name AS account, l.side, l.amount_minor AS amount,
                          d.doc_type AS document, left(d.content_sha256,12) AS doc_sha256
                   FROM journal_lines l
                   JOIN accounts a ON a.account_id = l.account_id
                   JOIN source_documents d ON d.doc_id = l.doc_id
                   WHERE l.entry_id=%s AND l.org_id=%s
                   ORDER BY l.line_id""",
                (entry_id, org),
            ).fetchall()
        return [dict(r) for r in rows]

    def audit_log(self, org: str, limit: int = 100, offset: int = 0) -> list[dict]:
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                """SELECT actor, action, object, detail, created_at
                   FROM audit_log WHERE org_id=%s
                   ORDER BY log_id DESC LIMIT %s OFFSET %s""",
                (org, limit, offset),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- Phase C reads -------------------------------------------------
    def list_balances(self, org: str) -> list[dict]:
        """Every account with its balance (integer minor units)."""
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                """SELECT a.code, a.name, a.account_type, a.normal_balance,
                          COALESCE(sum(CASE WHEN l.side=a.normal_balance
                                            THEN l.amount_minor ELSE -l.amount_minor END),0) AS balance
                   FROM accounts a
                   LEFT JOIN journal_lines l ON l.account_id=a.account_id
                   WHERE a.org_id=%s
                   GROUP BY a.code, a.name, a.account_type, a.normal_balance
                   ORDER BY a.code""",
                (org,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_entries(self, org: str, limit: int = 50, offset: int = 0) -> dict:
        """Paginated journal entries (most recent first) with their lines."""
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            total = cur.execute(
                "SELECT count(*) AS n FROM journal_entries WHERE org_id=%s", (org,)
            ).fetchone()["n"]
            entries = cur.execute(
                """SELECT entry_id, entry_date, entry_type, memo, posted_by_policy,
                          reverses, entry_hash, prev_hash, chain_seq
                   FROM journal_entries WHERE org_id=%s
                   ORDER BY chain_seq DESC NULLS LAST LIMIT %s OFFSET %s""",
                (org, limit, offset),
            ).fetchall()
            ids = [r["entry_id"] for r in entries]
            lines_by_entry: dict = {r["entry_id"]: [] for r in entries}
            if ids:
                lines = cur.execute(
                    """SELECT l.entry_id, a.code, a.name, l.side, l.amount_minor
                       FROM journal_lines l JOIN accounts a ON a.account_id=l.account_id
                       WHERE l.entry_id = ANY(%s) ORDER BY l.line_id""",
                    (ids,),
                ).fetchall()
                for ln in lines:
                    lines_by_entry[ln["entry_id"]].append(
                        {"code": ln["code"], "name": ln["name"], "side": ln["side"],
                         "amount_minor": ln["amount_minor"]})
        out = []
        for e in entries:
            out.append({
                "entry_id": str(e["entry_id"]), "date": e["entry_date"].isoformat(),
                "entry_type": e["entry_type"], "memo": e["memo"],
                "posted_by_policy": e["posted_by_policy"],
                "entry_hash": e["entry_hash"], "chain_seq": e["chain_seq"],
                "lines": lines_by_entry[e["entry_id"]],
            })
        return {"total": total, "limit": limit, "offset": offset, "entries": out}

    def trail(self, org: str, entry_id: str) -> Optional[dict]:
        """Full provenance join for one entry:
        line -> entry -> decision (incl stored thresholds + reason) -> proposal
             -> document -> reviewer -> hash. One response; nothing generated."""
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            e = cur.execute(
                """SELECT entry_id, entry_date, entry_type, memo, posted_by_policy,
                          entry_hash, prev_hash, chain_seq
                   FROM journal_entries WHERE org_id=%s AND entry_id=%s""",
                (org, entry_id),
            ).fetchone()
            if not e:
                return None
            lines = cur.execute(
                """SELECT a.code, a.name, l.side, l.amount_minor, l.doc_id
                   FROM journal_lines l JOIN accounts a ON a.account_id=l.account_id
                   WHERE l.entry_id=%s ORDER BY l.line_id""",
                (entry_id,),
            ).fetchall()
            # primary source document = the first line's document
            doc = None
            if lines:
                doc = cur.execute(
                    "SELECT doc_type, source_system, raw, content_sha256 AS sha256 FROM source_documents WHERE doc_id=%s",
                    (lines[0]["doc_id"],),
                ).fetchone()
            # the routing decision that produced this entry (may be absent for
            # migration / manual close adjustments)
            decision = cur.execute(
                """SELECT decision_id, decision, reason, policy_version, effective_thresholds,
                          qa_sampled, proposal_id, status
                   FROM routing_decisions WHERE org_id=%s AND posted_entry_id=%s""",
                (org, entry_id),
            ).fetchone()
            proposal = None
            reviewer = None
            if decision:
                proposal = cur.execute(
                    """SELECT account_code, account_type, rationale, confidence,
                              pattern_match, model_id
                       FROM proposals WHERE proposal_id=%s""",
                    (decision["proposal_id"],),
                ).fetchone()
                rq = cur.execute(
                    """SELECT action, reviewer_id, reviewer_role
                       FROM review_queue WHERE decision_id=%s AND status='resolved'
                       ORDER BY resolved_at DESC LIMIT 1""",
                    (decision["decision_id"],),
                ).fetchone()
                if rq and rq["reviewer_id"]:
                    reviewer = {"id": rq["reviewer_id"], "role": rq["reviewer_role"],
                                "kind": "human", "action": rq["action"]}
                elif decision["decision"] == "auto_post":
                    reviewer = {"id": "Policy engine v1.0.0",
                                "role": "Deterministic auto-post rule", "kind": "auto",
                                "action": "auto_post"}

        return {
            "entry": {"entry_id": str(e["entry_id"]), "date": e["entry_date"].isoformat(),
                      "entry_type": e["entry_type"], "memo": e["memo"],
                      "posted_by_policy": e["posted_by_policy"],
                      "entry_hash": e["entry_hash"], "prev_hash": e["prev_hash"],
                      "chain_seq": e["chain_seq"]},
            "amount_minor": sum(l["amount_minor"] for l in lines if l["side"] == "debit"),
            "lines": [{"code": l["code"], "name": l["name"], "side": l["side"],
                       "amount_minor": l["amount_minor"]} for l in lines],
            "document": (dict(doc) if doc else None),
            "decision": (dict(decision) | {"decision_id": str(decision["decision_id"]),
                                           "proposal_id": str(decision["proposal_id"])}
                         if decision else None),
            "proposal": (dict(proposal) | {"confidence": float(proposal["confidence"])}
                         if proposal else None),
            "reviewer": reviewer,
        }

    # ---- internal ------------------------------------------------------
    @staticmethod
    def _log(conn, org, actor, action, obj, detail) -> None:
        from psycopg.types.json import Jsonb
        conn.execute(
            "INSERT INTO audit_log(org_id, actor, action, object, detail) VALUES (%s,%s,%s,%s,%s)",
            (org, actor, action, obj, Jsonb(detail)),
        )
