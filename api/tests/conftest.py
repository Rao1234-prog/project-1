"""Shared pytest fixtures for the Bedrock ledger tests.

Requires a running Postgres with the schema applied and two env vars:
    BEDROCK_DATABASE_URL  -> the append-only bedrock_app role (service layer)
    BEDROCK_ADMIN_URL     -> superuser, used only to simulate direct-SQL tampering
`scripts/run_gate.sh` sets both up automatically.
"""
from __future__ import annotations

import os
import uuid

import psycopg
import pytest

from bedrock.service import LedgerService

# Cardinal Heating & Air chart of accounts (from run_phases.py).
COA = [
    ("1000", "Operating Checking", "asset", "debit"),
    ("1200", "Accounts Receivable", "asset", "debit"),
    ("1500", "Equipment", "asset", "debit"),
    ("1510", "Accum. Depreciation", "contra", "credit"),
    ("2000", "Accounts Payable", "liability", "credit"),
    ("2100", "Payroll Liabilities", "liability", "credit"),
    ("2200", "Sales Tax Payable", "liability", "credit"),   # sensitive (see SENSITIVE_CODES)
    ("3000", "Owner's Equity", "equity", "credit"),
    ("4000", "Service Revenue", "revenue", "credit"),
    ("4100", "Install Revenue", "revenue", "credit"),
    ("5000", "Parts & Materials COGS", "expense", "debit"),
    ("5100", "Subcontractor COGS", "expense", "debit"),
    ("6000", "Payroll Expense", "expense", "debit"),
    ("6100", "Fuel & Vehicle", "expense", "debit"),
    ("6200", "Software & Office", "expense", "debit"),
    ("6300", "Rent", "expense", "debit"),
    ("6400", "Insurance", "expense", "debit"),
    ("6500", "Depreciation Expense", "expense", "debit"),
    ("6600", "Meals", "expense", "debit"),
]

# Accounts that carry policy sensitivity (tax accounts; equity is sensitive by type).
SENSITIVE_CODES = {"2200"}


def load_coa(ledger, org):
    for code, name, atype, nb in COA:
        ledger.add_account(org, code, name, atype, nb, is_sensitive=(code in SENSITIVE_CODES))


@pytest.fixture(scope="session")
def app_url() -> str:
    url = os.environ.get("BEDROCK_DATABASE_URL")
    if not url:
        pytest.skip("BEDROCK_DATABASE_URL not set")
    return url


@pytest.fixture(scope="session")
def admin_url() -> str:
    url = os.environ.get("BEDROCK_ADMIN_URL")
    if not url:
        pytest.skip("BEDROCK_ADMIN_URL not set")
    return url


@pytest.fixture(scope="session")
def service(app_url) -> LedgerService:
    s = LedgerService(dsn=app_url)
    yield s
    s.pool.close()


@pytest.fixture
def org(service) -> str:
    """A fresh, isolated org per test."""
    return service.ensure_org(f"test-{uuid.uuid4()}")


@pytest.fixture
def seeded_org(service, org) -> str:
    """Fresh org with the full chart of accounts loaded."""
    load_coa(service, org)
    return org


@pytest.fixture
def admin_conn(admin_url):
    with psycopg.connect(admin_url, autocommit=True) as conn:
        yield conn


@pytest.fixture(scope="session")
def ai_url() -> str:
    url = os.environ.get("BEDROCK_AI_URL")
    if not url:
        pytest.skip("BEDROCK_AI_URL not set")
    return url


@pytest.fixture(scope="session")
def policy(app_url, ai_url):
    from bedrock.policy_service import PolicyService
    ps = PolicyService(app_dsn=app_url, ai_url=ai_url)
    yield ps
    ps.close()


@pytest.fixture
def porg(policy) -> str:
    """Fresh org with the full chart of accounts, wired to the policy service."""
    org = policy.ledger.ensure_org(f"ptest-{uuid.uuid4()}")
    load_coa(policy.ledger, org)
    return org


@pytest.fixture
def client(app_url, ai_url, monkeypatch):
    """FastAPI TestClient wired to the running database."""
    monkeypatch.setenv("BEDROCK_DATABASE_URL", app_url)
    monkeypatch.setenv("BEDROCK_AI_URL", ai_url)
    from fastapi.testclient import TestClient
    from bedrock.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def api_org(client):
    """Fresh org with the full chart of accounts, created through the API."""
    org = client.post("/orgs", json={"name": f"api-{uuid.uuid4()}"}).json()["org_id"]
    for code, name, atype, nb in COA:
        client.post(f"/orgs/{org}/accounts",
                    json={"code": code, "name": name, "account_type": atype,
                          "normal_balance": nb, "is_sensitive": code in SENSITIVE_CODES})
    return org
