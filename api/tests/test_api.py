"""FastAPI smoke test — drives the ledger through the HTTP surface end to end."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_url, monkeypatch):
    monkeypatch.setenv("BEDROCK_DATABASE_URL", app_url)
    from bedrock.main import app
    with TestClient(app) as c:
        yield c


def test_api_end_to_end(client):
    org = client.post("/orgs", json={"name": f"api-{uuid.uuid4()}"}).json()["org_id"]

    for code, name, atype, nb in [("1000", "Cash", "asset", "debit"),
                                  ("3000", "Equity", "equity", "credit"),
                                  ("6100", "Fuel", "expense", "debit")]:
        r = client.post(f"/orgs/{org}/accounts",
                        json={"code": code, "name": name,
                              "account_type": atype, "normal_balance": nb})
        assert r.status_code == 200

    doc = client.post(f"/orgs/{org}/documents",
                      json={"doc_type": "ob", "source_system": "manual",
                            "raw": "opening balance"}).json()["doc_id"]

    r = client.post(f"/orgs/{org}/entries", json={
        "entry_date": "2026-03-31", "entry_type": "opening_balance", "memo": "OB",
        "posted_by_policy": "migration",
        "lines": [
            {"account_code": "1000", "side": "debit", "amount_minor": 8_421_355, "doc_id": doc},
            {"account_code": "3000", "side": "credit", "amount_minor": 8_421_355, "doc_id": doc},
        ]})
    assert r.status_code == 200
    entry = r.json()
    assert entry["chain_seq"] == 1
    assert entry["prev_hash"] == "0" * 64

    # unbalanced entry rejected via API (422)
    bad = client.post(f"/orgs/{org}/entries", json={
        "entry_date": "2026-04-01", "entry_type": "standard", "memo": "bad",
        "posted_by_policy": "test",
        "lines": [
            {"account_code": "6100", "side": "debit", "amount_minor": 5000, "doc_id": doc},
            {"account_code": "1000", "side": "credit", "amount_minor": 4999, "doc_id": doc},
        ]})
    assert bad.status_code == 422

    assert client.get(f"/orgs/{org}/trial-balance").json()["trial_balance"] == 0
    assert client.get(f"/orgs/{org}/chain/verify").json()["verified"] is True
    assert client.get(f"/orgs/{org}/accounts/1000/balance").json()["balance"] == 8_421_355

    prov = client.get(f"/orgs/{org}/entries/{entry['entry_id']}/provenance").json()["provenance"]
    assert len(prov) == 2
    assert {p["account"] for p in prov} == {"Cash", "Equity"}
