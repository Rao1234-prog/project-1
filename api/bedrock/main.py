"""Bedrock FastAPI — Phase A: the persistent ledger engine over Postgres.

Every write goes through LedgerService, which connects as the append-only
``bedrock_app`` role. The database, not this process, is the source of truth for
balance, immutability, provenance, the hash chain, and period locks.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from .models import (AccountIn, ClosePeriodIn, DocumentIn, EntryIn, OrgIn, ReverseIn)
from .service import LedgerError, LedgerService, LineInput

service: LedgerService | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global service
    service = LedgerService()
    yield
    if service and service.pool:
        service.pool.close()


app = FastAPI(title="Bedrock Ledger", version="0.1.0", lifespan=lifespan)


def svc() -> LedgerService:
    assert service is not None
    return service


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/orgs")
def create_org(body: OrgIn):
    return {"org_id": svc().ensure_org(body.name)}


@app.post("/orgs/{org}/accounts")
def add_account(org: str, body: AccountIn):
    try:
        aid = svc().add_account(org, body.code, body.name, body.account_type, body.normal_balance)
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
