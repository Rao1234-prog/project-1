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
