"""GreenLedger categorizer (Phase D).

Produces an AI proposal for a transaction. Two layers, in order:

  1. Pattern memory — exact then fuzzy vendor match against *approved review
     history*. Grounded in real human decisions, so these proposals may exceed
     the 0.97 auto-post gate.
  2. LLM fallback — only on a pattern-memory miss. Returns account/confidence/
     rationale as strict JSON; capped at 0.90 so a genuinely new vendor's first
     pass always goes through a human.

Invariants:
  * The *system* decides pattern_match ("seen"/"similar"/"novel") from history —
    the model cannot claim familiarity it doesn't have.
  * The LLM never computes amounts and never sees an arithmetic task.
  * Fail toward review: any timeout/error/malformed-JSON/invalid-account yields a
    confidence-0.0 proposal that routes to the queue. Never fail open.
  * Replayable: model_id, prompt template version, prompt hash, and raw response
    are persisted per proposal; identical content reuses the cached proposal.
  * Proposals are written through the greenledger_ai role only.
"""
from __future__ import annotations

import difflib
import hashlib
import json
from dataclasses import dataclass
from typing import Optional

from psycopg_pool import ConnectionPool

from .llm import LLMClient
from .service import LedgerService

PROMPT_TEMPLATE_VERSION = "cat-1"

# A vendor becomes "seen" after this many approved decisions (exact match).
SEEN_MIN_APPROVALS = 1
FUZZY_THRESHOLD = 0.82           # difflib ratio for a "similar" match
LLM_CONFIDENCE_CAP = 0.90        # LLM self-report is uncalibrated — never clears 0.97
EXACT_SEEN_CONFIDENCE = 0.98     # grounded in approved history; may exceed the gate
SIMILAR_CONFIDENCE = 0.85


@dataclass
class CategorizerResult:
    proposal_id: str
    account_code: str
    account_type: str
    confidence: float
    rationale: str
    pattern_match: str           # seen | similar | novel  (SYSTEM-decided)
    source_layer: str            # pattern_exact | pattern_fuzzy | llm | failed
    model_id: str
    cached: bool


def _norm(v: str) -> str:
    return " ".join(v.lower().split())


class Categorizer:
    def __init__(self, ledger: LedgerService, ai_pool: ConnectionPool, llm: LLMClient):
        self.ledger = ledger          # app-role reads (accounts, history)
        self.ai_pool = ai_pool        # greenledger_ai — the only writer of proposals
        self.llm = llm

    # ---- chart of accounts --------------------------------------------
    def _coa(self, org: str) -> dict[str, dict]:
        return {a["code"]: a for a in self.ledger.list_balances(org)}

    # ---- pattern memory (from approved review history) -----------------
    def _vendor_memory(self, org: str) -> dict[str, dict[str, int]]:
        """vendor -> {final_account_code: count} across resolved approve/correct
        reviews. 'approve' credits the proposed account; 'correct' credits the
        human's corrected account."""
        sql = """
            SELECT t.counterparty,
                   CASE WHEN q.action='correct' THEN q.corrected_account_code
                        ELSE p.account_code END AS final_account
            FROM review_queue q
            JOIN routing_decisions d ON d.decision_id = q.decision_id
            JOIN proposals p         ON p.proposal_id = d.proposal_id
            JOIN transactions t      ON t.org_id = q.org_id AND t.txn_id = q.txn_id
            WHERE q.org_id=%s AND q.status='resolved' AND q.action IN ('approve','correct')
        """
        mem: dict[str, dict[str, int]] = {}
        with self.ledger.pool.connection() as conn:
            for cp, acct in conn.execute(sql, (org,)).fetchall():
                if not acct:
                    continue
                mem.setdefault(_norm(cp), {}).setdefault(acct, 0)
                mem[_norm(cp)][acct] += 1
        return mem

    def _pattern_lookup(self, org: str, vendor: str):
        """Return (pattern_match, account_code, source_layer, confidence) or None
        on a miss. The system alone decides this — the model has no say."""
        mem = self._vendor_memory(org)
        key = _norm(vendor)

        # exact vendor match with enough approved decisions -> seen
        if key in mem:
            counts = mem[key]
            total = sum(counts.values())
            best = max(counts, key=counts.get)
            if total >= SEEN_MIN_APPROVALS:
                return ("seen", best, "pattern_exact", EXACT_SEEN_CONFIDENCE)

        # fuzzy match against known vendors -> similar
        candidates = difflib.get_close_matches(key, list(mem.keys()), n=1, cutoff=FUZZY_THRESHOLD)
        if candidates:
            counts = mem[candidates[0]]
            best = max(counts, key=counts.get)
            return ("similar", best, "pattern_fuzzy", SIMILAR_CONFIDENCE)

        return None

    # ---- LLM prompt ----------------------------------------------------
    @staticmethod
    def _build_prompt(coa: dict[str, dict], vendor: str, description: str) -> tuple[str, str]:
        chart = "\n".join(f"  {c} — {a['name']} ({a['account_type']})"
                          for c, a in sorted(coa.items()))
        system = (
            "You are a bookkeeping categorizer. Choose the single best account "
            "for a transaction from the provided chart of accounts. "
            "Return strict JSON: {\"account_code\": <one code from the chart>, "
            "\"confidence\": <0..1>, \"rationale\": <one sentence>}. "
            "Rules: pick account_code ONLY from the chart. Do not invent codes. "
            "Never compute, infer, or output any monetary amount — you categorize "
            "only. If unsure, still pick the closest account and lower confidence."
        )
        user = (f"Chart of accounts:\n{chart}\n\n"
                f"Transaction vendor/counterparty: {vendor}\n"
                f"Description: {description}\n\n"
                "Which account should this post to?")
        return system, user

    # ---- main entry ----------------------------------------------------
    def categorize(self, org: str, *, txn_id: str, vendor: str, description: str,
                   content_sha256: str) -> CategorizerResult:
        coa = self._coa(org)
        valid_codes = set(coa)

        # --- cache: identical content -> reuse proposal, no second API call ---
        cached = self._cached_proposal(org, content_sha256)
        if cached:
            return cached

        pattern = self._pattern_lookup(org, vendor)
        if pattern:
            pattern_match, account_code, source_layer, confidence = pattern
            rationale = (f"pattern memory ({source_layer.split('_')[1]}) matched vendor "
                         f"'{vendor}' from approved history -> {account_code}")
            model_id = "pattern-memory"
            raw = json.dumps({"account_code": account_code, "confidence": confidence,
                              "source": source_layer})
            features = {"vendor": vendor, "description": description,
                        "layer": source_layer, "matched_account": account_code}
        else:
            # --- LLM fallback (novel vendor). System fixes pattern_match=novel. ---
            pattern_match = "novel"
            system, user = self._build_prompt(coa, vendor, description)
            features = {"vendor": vendor, "description": description, "coa": sorted(valid_codes)}
            account_code, confidence, rationale, source_layer, model_id, raw = \
                self._run_llm(system, user, valid_codes)

        # prompt_hash is NOT NULL for every proposal (spec replayability): hash of
        # the inputs the categorizer acted on, LLM or not.
        prompt_hash = hashlib.sha256(json.dumps(features, sort_keys=True).encode()).hexdigest()
        account_type = coa[account_code]["account_type"] if account_code in coa else "expense"

        proposal_id = self._insert_proposal(
            org, txn_id, account_code=account_code, account_type=account_type,
            rationale=rationale, confidence=confidence, pattern_match=pattern_match,
            model_id=model_id, source_layer=source_layer,
            prompt_hash=prompt_hash, raw_response=raw, content_sha256=content_sha256,
            features_snapshot=features)

        return CategorizerResult(proposal_id, account_code, account_type, confidence,
                                 rationale, pattern_match, source_layer, model_id, cached=False)

    def _run_llm(self, system: str, user: str, valid_codes: set[str]):
        """Call the LLM and enforce the output contract. Fail toward review."""
        try:
            resp = self.llm.complete(system=system, user=user)
        except Exception as e:  # timeout, connection, API error — never fail open
            return ("UNCATEGORIZED", 0.0,
                    f"LLM call failed ({type(e).__name__}); routed to human review",
                    "failed", getattr(self.llm, "model_id", "unknown"), "")

        raw = resp.raw
        try:
            data = json.loads(raw)
            code = str(data["account_code"])
            conf = float(data["confidence"])
            rationale = str(data.get("rationale", ""))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return ("UNCATEGORIZED", 0.0,
                    "LLM returned malformed JSON; routed to human review",
                    "failed", resp.model_id, raw)

        if code not in valid_codes:            # invalid account is a failure, not a guess
            return ("UNCATEGORIZED", 0.0,
                    f"LLM proposed account '{code}' not in the chart of accounts; "
                    f"routed to human review", "failed", resp.model_id, raw)

        conf = max(0.0, min(1.0, conf))        # clamp
        conf = min(conf, LLM_CONFIDENCE_CAP)   # cap: uncalibrated self-report
        return (code, conf, rationale or "LLM categorization", "llm", resp.model_id, raw)

    # ---- proposal persistence (via greenledger_ai) ------------------------
    def _cached_proposal(self, org: str, content_sha256: str) -> Optional[CategorizerResult]:
        with self.ledger.pool.connection() as conn:
            row = conn.execute(
                """SELECT proposal_id, account_code, account_type, confidence, rationale,
                          pattern_match, source_layer, model_id
                   FROM proposals
                   WHERE org_id=%s AND content_sha256=%s
                   ORDER BY created_at DESC LIMIT 1""",
                (org, content_sha256),
            ).fetchone()
        if not row:
            return None
        return CategorizerResult(str(row[0]), row[1], row[2], float(row[3]), row[4],
                                 row[5], row[6] or "cached", row[7], cached=True)

    def _insert_proposal(self, org, txn_id, *, account_code, account_type, rationale,
                         confidence, pattern_match, model_id, source_layer,
                         prompt_hash, raw_response, content_sha256, features_snapshot) -> str:
        from psycopg.types.json import Jsonb
        with self.ai_pool.connection() as conn:
            row = conn.execute(
                """INSERT INTO proposals
                     (org_id, txn_id, account_code, account_type, rationale, confidence,
                      pattern_match, model_id, source_layer, prompt_template_version,
                      prompt_hash, raw_response, content_sha256, features_snapshot)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING proposal_id""",
                (org, txn_id, account_code, account_type, rationale, confidence,
                 pattern_match, model_id, source_layer, PROMPT_TEMPLATE_VERSION,
                 prompt_hash, raw_response, content_sha256, Jsonb(features_snapshot)),
            ).fetchone()
            return str(row[0])
