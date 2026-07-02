"""Bedrock FastAPI — Phase A: the persistent ledger engine over Postgres.

Every write goes through LedgerService, which connects as the append-only
``bedrock_app`` role. The database, not this process, is the source of truth for
balance, immutability, provenance, the hash chain, and period locks.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .close_service import CloseBlocked, CloseService
from .models import (AccountIn, CloseApproveIn, ClosePeriodIn, DocumentIn, EntryIn, OrgIn,
                     ReconApproveIn, ReviewIn, ReverseIn, TransactionIn)
from .policy import VALID_ROLES
from .policy_service import (DocumentIn as PDoc, PolicyService, ProposalIn, PolicyError,
                             Unauthorized)
from .service import LedgerError, LedgerService, LineInput

service: LedgerService | None = None
policy: PolicyService | None = None
close_svc: CloseService | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global service, policy, close_svc
    policy = PolicyService()
    service = policy.ledger      # share the same ledger/app pool
    close_svc = CloseService(service)
    yield
    if policy:
        policy.close()


app = FastAPI(title="Bedrock Ledger", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def svc() -> LedgerService:
    assert service is not None
    return service


def pol() -> PolicyService:
    assert policy is not None
    return policy


def closer() -> CloseService:
    assert close_svc is not None
    return close_svc


def require_role(x_bedrock_role: Optional[str] = Header(default=None)) -> str:
    """The acting role comes from the server boundary (the X-Bedrock-Role header
    the UI's 'acting as' switcher sets), never from the request body."""
    if not x_bedrock_role or x_bedrock_role not in VALID_ROLES:
        raise HTTPException(status_code=400,
                            detail=f"X-Bedrock-Role header required (one of {sorted(VALID_ROLES)})")
    return x_bedrock_role


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/orgs")
def create_org(body: OrgIn):
    return {"org_id": svc().ensure_org(body.name)}


@app.get("/orgs")
def list_orgs():
    return {"orgs": svc().list_orgs()}


@app.post("/orgs/{org}/accounts")
def add_account(org: str, body: AccountIn):
    try:
        aid = svc().add_account(org, body.code, body.name, body.account_type,
                                body.normal_balance, is_sensitive=body.is_sensitive)
    except LedgerError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"account_id": aid}


@app.get("/orgs/{org}/accounts/{code}")
def get_account(org: str, code: str):
    try:
        return svc().by_code(org, code)
    except LedgerError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/orgs/{org}/documents")
def ingest_document(org: str, body: DocumentIn):
    doc_id = svc().ingest_document(org, body.doc_type, body.source_system, body.raw)
    return {"doc_id": doc_id}


@app.post("/orgs/{org}/entries")
def post_entry(org: str, body: EntryIn):
    lines = [LineInput(l.account_code, l.side, l.amount_minor, l.doc_id, l.txn_id)
             for l in body.lines]
    try:
        return svc().post(org, body.entry_date, body.entry_type, body.memo, lines,
                          body.posted_by_policy, body.reverses)
    except LedgerError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/orgs/{org}/entries/{entry_id}/reverse")
def reverse_entry(org: str, entry_id: str, body: ReverseIn):
    try:
        return svc().reverse(org, entry_id, body.entry_date, body.memo, body.by)
    except LedgerError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.get("/orgs/{org}/entries/{entry_id}/provenance")
def provenance(org: str, entry_id: str):
    return {"entry_id": entry_id, "provenance": svc().provenance(org, entry_id)}


@app.post("/orgs/{org}/periods/{key}/close")
def close_period(org: str, key: str, body: ClosePeriodIn):
    try:
        svc().close_period(org, key, body.closed_by)
    except LedgerError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"period": key, "status": "closed"}


@app.get("/orgs/{org}/trial-balance")
def trial_balance(org: str):
    return {"trial_balance": svc().trial_balance(org)}


@app.get("/orgs/{org}/accounts/{code}/balance")
def account_balance(org: str, code: str):
    return {"code": code, "balance": svc().balance(org, code)}


@app.get("/orgs/{org}/chain/verify")
def verify_chain(org: str):
    return {"verified": svc().verify_chain(org)}


# ---- Phase B: policy engine + review ---------------------------------------
@app.post("/orgs/{org}/transactions")
def ingest_transaction(org: str, body: TransactionIn):
    proposal = None
    if body.proposal is not None:
        proposal = ProposalIn(body.proposal.account_code, body.proposal.account_type,
                              body.proposal.rationale, body.proposal.confidence,
                              body.proposal.pattern_match, body.proposal.model_id)
    try:
        return pol().ingest_transaction(
            org, day=body.day, amount_minor=body.amount_minor,
            counterparty=body.counterparty, description=body.description,
            direction=body.direction,
            document=PDoc(body.document.doc_type, body.document.source_system, body.document.raw),
            proposal=proposal,
            txn_id=body.txn_id, fraud_flags=tuple(body.fraud_flags),
            cash_account_code=body.cash_account_code)
    except LedgerError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.get("/orgs/{org}/audit/accuracy")
def audit_accuracy(org: str):
    return pol().accuracy_audit(org)


@app.post("/orgs/{org}/transactions/{txn_id}/reviews")
def submit_review(org: str, txn_id: str, body: ReviewIn,
                  x_bedrock_role: Optional[str] = Header(default=None)):
    # The authoritative role is the server boundary header, not the body.
    role = require_role(x_bedrock_role)
    try:
        return pol().submit_review(
            org, txn_id, action=body.action, reviewer_id=body.reviewer_id,
            reviewer_role=role,
            corrected_account_code=body.corrected_account_code,
            cash_account_code=body.cash_account_code)
    except Unauthorized as e:
        raise HTTPException(status_code=403,
                            detail={"error": str(e), "required_role": "controller",
                                    "acting_role": role})
    except (PolicyError, LedgerError) as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.get("/orgs/{org}/queue")
def get_queue(org: str, lane: str | None = None):
    return {"queue": pol().queue(org, lane)}


@app.get("/orgs/{org}/accounts/{code}/error-rate")
def account_error(org: str, code: str):
    return pol().account_error(org, code)


# ---- Phase C: reads, trail, reconciliation, close --------------------------
@app.get("/orgs/{org}/balances")
def balances(org: str):
    return {"balances": svc().list_balances(org)}


@app.get("/orgs/{org}/entries")
def entries(org: str, limit: int = 50, offset: int = 0):
    return svc().list_entries(org, limit=limit, offset=offset)


@app.get("/orgs/{org}/entries/{entry_id}/trail")
def entry_trail(org: str, entry_id: str):
    t = svc().trail(org, entry_id)
    if t is None:
        raise HTTPException(status_code=404, detail="entry not found")
    return t


@app.get("/orgs/{org}/audit")
def audit(org: str, limit: int = 100, offset: int = 0):
    return {"audit": svc().audit_log(org, limit=limit, offset=offset)}


@app.post("/orgs/{org}/reconciliations/approve")
def approve_reconciliation(org: str, body: ReconApproveIn,
                           x_bedrock_role: Optional[str] = Header(default=None)):
    role = require_role(x_bedrock_role)
    if role != "controller":
        raise HTTPException(status_code=403,
                            detail={"error": "approving a reconciliation requires controller",
                                    "required_role": "controller", "acting_role": role})
    return closer().approve_reconciliation(org, body.account_code, body.period,
                                           approved_by=body.approved_by, approved_role=role)


@app.get("/orgs/{org}/close/checklist")
def close_checklist(org: str, period: str):
    return closer().checklist(org, period)


@app.post("/orgs/{org}/close/approve")
def close_approve(org: str, body: CloseApproveIn,
                  x_bedrock_role: Optional[str] = Header(default=None)):
    role = require_role(x_bedrock_role)
    if role != "controller":
        raise HTTPException(status_code=403,
                            detail={"error": "approving a close requires controller",
                                    "required_role": "controller", "acting_role": role})
    try:
        return closer().approve_close(org, body.period, approved_by=body.approved_by,
                                      approved_role=role)
    except CloseBlocked as e:
        # The client cannot force a close: a blocking finding -> 409 with the findings.
        raise HTTPException(status_code=409,
                            detail={"error": "close blocked", "findings": e.findings})
