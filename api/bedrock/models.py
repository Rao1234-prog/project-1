"""Request/response models for the Bedrock API. Amounts are integer minor units."""
from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class OrgIn(BaseModel):
    name: str


class AccountIn(BaseModel):
    code: str
    name: str
    account_type: str
    normal_balance: str

    @field_validator("normal_balance")
    @classmethod
    def _nb(cls, v: str) -> str:
        if v not in ("debit", "credit"):
            raise ValueError("normal_balance must be debit or credit")
        return v


class DocumentIn(BaseModel):
    doc_type: str
    source_system: str
    raw: str


class LineIn(BaseModel):
    account_code: str
    side: str
    amount_minor: int = Field(..., description="positive integer minor units (cents)")
    doc_id: str
    txn_id: Optional[str] = None

    @field_validator("side")
    @classmethod
    def _side(cls, v: str) -> str:
        if v not in ("debit", "credit"):
            raise ValueError("side must be debit or credit")
        return v


class EntryIn(BaseModel):
    entry_date: date
    entry_type: str
    memo: str
    lines: list[LineIn]
    posted_by_policy: str
    reverses: Optional[str] = None


class ReverseIn(BaseModel):
    entry_date: date
    memo: str
    by: str


class ClosePeriodIn(BaseModel):
    closed_by: str


# ---- Phase B ---------------------------------------------------------------
class ProposalBody(BaseModel):
    account_code: str
    account_type: str
    rationale: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    pattern_match: str
    model_id: str = "bedrock-cat-1"

    @field_validator("pattern_match")
    @classmethod
    def _pm(cls, v: str) -> str:
        if v not in ("seen", "similar", "novel"):
            raise ValueError("pattern_match must be seen, similar, or novel")
        return v


class DocumentBody(BaseModel):
    doc_type: str
    source_system: str
    raw: str


class TransactionIn(BaseModel):
    day: str                       # YYYY-MM-DD
    amount_minor: int = Field(..., gt=0)
    counterparty: str
    description: str
    direction: str
    document: DocumentBody
    # Omit to let the categorizer produce the proposal (pattern memory -> LLM).
    proposal: Optional[ProposalBody] = None
    txn_id: Optional[str] = None
    fraud_flags: list[str] = []
    cash_account_code: str = "1000"

    @field_validator("direction")
    @classmethod
    def _dir(cls, v: str) -> str:
        if v not in ("inflow", "outflow"):
            raise ValueError("direction must be inflow or outflow")
        return v


class ReviewIn(BaseModel):
    action: str                    # approve | correct | reject
    reviewer_id: str
    # role now comes from the X-Bedrock-Role header, not the body; kept optional
    # for backward compatibility but ignored by the API.
    reviewer_role: Optional[str] = None
    corrected_account_code: Optional[str] = None
    cash_account_code: str = "1000"


class ReconApproveIn(BaseModel):
    account_code: str
    period: str                    # YYYY-MM
    approved_by: str


class CloseApproveIn(BaseModel):
    period: str                    # YYYY-MM
    approved_by: str
