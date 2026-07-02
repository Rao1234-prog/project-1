"""
Bedrock ledger engine — Phase 0.
Deterministic double-entry core. No AI anywhere in this module (brief §4.2 stage 4).

Invariants enforced:
  1. Every journal entry balances (sum debits == sum credits) or it cannot post.
  2. Entries are append-only; corrections are reversal entries. No mutation API exists.
  3. Every journal line carries a source-document reference (provenance NOT NULL).
  4. Entries are hash-chained per org; tampering with history is detectable.
  5. Entries cannot post into a closed/locked period.
  6. All amounts are integer minor units (cents). No floats.
"""
from __future__ import annotations
import hashlib, json, itertools
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

_id = itertools.count(1)
def nid(prefix: str) -> str: return f"{prefix}_{next(_id):05d}"

class LedgerError(Exception): pass

@dataclass(frozen=True)
class Account:
    account_id: str; code: str; name: str; account_type: str; normal_balance: str

@dataclass(frozen=True)
class SourceDocument:
    doc_id: str; doc_type: str; source_system: str; raw: str
    sha256: str = ""
    def __post_init__(self):
        object.__setattr__(self, "sha256", hashlib.sha256(self.raw.encode()).hexdigest())

@dataclass(frozen=True)
class JournalLine:
    account_id: str; side: str; amount_minor: int; doc_id: str; txn_id: Optional[str] = None
    def __post_init__(self):
        if self.side not in ("debit", "credit"): raise LedgerError("bad side")
        if not isinstance(self.amount_minor, int) or self.amount_minor <= 0:
            raise LedgerError("amounts are positive integer minor units")
        if not self.doc_id: raise LedgerError("provenance required: doc_id missing")

@dataclass(frozen=True)
class JournalEntry:
    entry_id: str; entry_date: date; entry_type: str; memo: str
    lines: tuple; posted_by_policy: str; reverses: Optional[str]; entry_hash: str

class Ledger:
    ENTRY_TYPES = {"standard","accrual","depreciation","reclass","reversal","opening_balance"}

    def __init__(self, org_name: str):
        self.org_name = org_name
        self.accounts: dict[str, Account] = {}
        self.documents: dict[str, SourceDocument] = {}
        self._doc_hashes: set[str] = set()
        self.entries: list[JournalEntry] = []
        self.periods: dict[str, str] = {}          # "YYYY-MM" -> open|closed|locked
        self.audit_log: list[dict] = []
        self._prev_hash = "0" * 64

    # ---- chart of accounts -------------------------------------------
    def add_account(self, code, name, account_type, normal_balance) -> Account:
        a = Account(nid("acct"), code, name, account_type, normal_balance)
        if any(x.code == code for x in self.accounts.values()):
            raise LedgerError(f"duplicate account code {code}")
        self.accounts[a.account_id] = a
        return a

    def by_code(self, code) -> Account:
        for a in self.accounts.values():
            if a.code == code: return a
        raise LedgerError(f"no account {code}")

    # ---- documents (immutable, content-addressed, deduped) ------------
    def ingest_document(self, doc_type, source_system, raw) -> SourceDocument:
        d = SourceDocument(nid("doc"), doc_type, source_system, raw)
        if d.sha256 in self._doc_hashes:
            existing = next(x for x in self.documents.values() if x.sha256 == d.sha256)
            self._log("system", "dedupe_document", existing.doc_id, {"sha256": d.sha256})
            return existing
        self._doc_hashes.add(d.sha256)
        self.documents[d.doc_id] = d
        self._log("system", "ingest_document", d.doc_id, {"type": doc_type, "sha256": d.sha256[:12]})
        return d

    # ---- periods -------------------------------------------------------
    def period_key(self, d: date) -> str: return f"{d.year:04d}-{d.month:02d}"
    def period_status(self, d: date) -> str: return self.periods.get(self.period_key(d), "open")

    def close_period(self, key: str, closed_by: str):
        if self.periods.get(key) in ("closed", "locked"):
            raise LedgerError(f"period {key} already closed")
        self.periods[key] = "closed"
        self._log("human", "close_period", key, {"closed_by": closed_by})

    # ---- posting (the only write path) ---------------------------------
    def post(self, entry_date: date, entry_type: str, memo: str,
             lines: list[JournalLine], posted_by_policy: str,
             reverses: Optional[str] = None) -> JournalEntry:
        if entry_type not in self.ENTRY_TYPES: raise LedgerError("bad entry_type")
        if self.period_status(entry_date) != "open":
            raise LedgerError(f"period {self.period_key(entry_date)} is not open")
        if not lines: raise LedgerError("empty entry")
        for ln in lines:
            if ln.account_id not in self.accounts: raise LedgerError("unknown account")
            if ln.doc_id not in self.documents: raise LedgerError("unknown source document")
        debits  = sum(l.amount_minor for l in lines if l.side == "debit")
        credits = sum(l.amount_minor for l in lines if l.side == "credit")
        if debits != credits:
            raise LedgerError(f"unbalanced entry: dr {debits} != cr {credits}")
        canonical = json.dumps({
            "date": entry_date.isoformat(), "type": entry_type, "memo": memo,
            "lines": [[l.account_id, l.side, l.amount_minor, l.doc_id] for l in lines],
            "reverses": reverses, "prev": self._prev_hash}, sort_keys=True)
        h = hashlib.sha256(canonical.encode()).hexdigest()
        e = JournalEntry(nid("je"), entry_date, entry_type, memo, tuple(lines),
                         posted_by_policy, reverses, h)
        self.entries.append(e)
        self._prev_hash = h
        self._log(posted_by_policy, "post_entry", e.entry_id,
                  {"type": entry_type, "dr": debits, "memo": memo[:60]})
        return e

    def reverse(self, entry_id: str, entry_date: date, memo: str, by: str) -> JournalEntry:
        orig = next((e for e in self.entries if e.entry_id == entry_id), None)
        if orig is None: raise LedgerError("no such entry")
        flipped = [JournalLine(l.account_id, "credit" if l.side == "debit" else "debit",
                               l.amount_minor, l.doc_id, l.txn_id) for l in orig.lines]
        return self.post(entry_date, "reversal", memo, flipped, by, reverses=entry_id)

    # ---- reads ----------------------------------------------------------
    def balance(self, code: str, as_of: Optional[date] = None) -> int:
        a = self.by_code(code); bal = 0
        for e in self.entries:
            if as_of and e.entry_date > as_of: continue
            for l in e.lines:
                if l.account_id != a.account_id: continue
                sign = 1 if l.side == a.normal_balance else -1
                bal += sign * l.amount_minor
        return bal

    def trial_balance(self) -> int:
        dr = sum(l.amount_minor for e in self.entries for l in e.lines if l.side == "debit")
        cr = sum(l.amount_minor for e in self.entries for l in e.lines if l.side == "credit")
        return dr - cr

    def verify_chain(self) -> bool:
        prev = "0" * 64
        for e in self.entries:
            canonical = json.dumps({
                "date": e.entry_date.isoformat(), "type": e.entry_type, "memo": e.memo,
                "lines": [[l.account_id, l.side, l.amount_minor, l.doc_id] for l in e.lines],
                "reverses": e.reverses, "prev": prev}, sort_keys=True)
            if hashlib.sha256(canonical.encode()).hexdigest() != e.entry_hash: return False
            prev = e.entry_hash
        return True

    def provenance(self, entry_id: str) -> list[dict]:
        e = next(x for x in self.entries if x.entry_id == entry_id)
        return [{"account": self.accounts[l.account_id].name, "side": l.side,
                 "amount": l.amount_minor,
                 "document": self.documents[l.doc_id].doc_type,
                 "doc_sha256": self.documents[l.doc_id].sha256[:12]} for l in e.lines]

    def _log(self, actor, action, obj, detail):
        self.audit_log.append({"actor": actor, "action": action, "object": obj, "detail": detail})
