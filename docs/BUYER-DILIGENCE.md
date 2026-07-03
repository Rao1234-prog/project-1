# Buyer Diligence — GreenLedger

An honest technical briefing for a prospective buyer of the GreenLedger source
and IP. Nothing here is marketing; where something is missing or weak it is said
plainly. Pair this with `docs/ASSET-SCHEDULE.md` (what transfers) and
`DEVIATIONS.md` / `docs/deferred-conformance.md` (design decisions and debt).

---

## 1. What GreenLedger is

A tested prototype of an **AI-native bookkeeping engine** whose organizing
principle is **AI proposes, humans decide, the database enforces.** An AI
categorizer proposes an account for each transaction with a confidence score;
a deterministic policy engine routes each proposal to *auto-post* or to a
human review lane based on confidence, amount, cumulative caps, sensitivity, and
a per-account error rate; and PostgreSQL — not application code — enforces the
accounting invariants (balanced entries, append-only journals, mandatory
provenance, period locking, a tamper-evident hash chain, and a role boundary
that lets the AI write *only* proposals).

It is a **prototype**: complete and internally consistent across its four build
phases, fully tested, and runnable end-to-end — but not a production SaaS. See
§6 for the honest limitations.

---

## 2. Stack

| Layer | Technology |
|---|---|
| Database | **PostgreSQL 16** — schema-enforced invariants (deferred constraint triggers, `SECURITY DEFINER` hash-chain trigger, role GRANTs) |
| Backend | **Python 3.11**, **FastAPI 0.115**, **Uvicorn 0.32**, **psycopg 3.2** + `psycopg_pool` (autocommit so `conn.transaction()` fires deferred triggers), **Pydantic 2.10** |
| AI categorizer | **Anthropic API**, model `claude-haiku-4-5`, `temperature=0`, strict-JSON output, confidence capped at 0.90; pattern-memory layer in front of the LLM |
| Frontend | **Vite 5** + **React 18**, **Recharts**, vendored **IBM Plex** fonts (`@fontsource`, no external fetch) |
| Tests / CI | **pytest 8.3** (57 pass / 1 skip), **GitHub Actions** with a Postgres 16 service container |
| Local infra | **Docker Compose** (Postgres + API), plus a no-Docker local Postgres runner |

---

## 3. Architecture summary

- **Deterministic ledger.** All money is integer minor units (BIGINT cents), no
  floats. Entries are immutable; corrections are reversal-only. The balance
  invariant is a DEFERRABLE constraint trigger that fires at COMMIT, so even two
  concurrent unbalanced inserts both fail at the database.
- **AI boundary as a schema grant.** Three DB roles: an owner/superuser, an
  append-only application role (`greenledger_app`), and an AI role
  (`greenledger_ai`) with `INSERT` on the `proposals` table and **nothing else**.
  A compromised or mis-wired AI layer physically cannot post to the ledger.
- **Proposals are not facts.** AI output lands in `proposals` (with
  `prompt_hash`, `model_id`, `features_snapshot` for replayability), separate
  from `journal_entries`/`journal_lines` (the only ledger facts).
- **Policy engine.** A pure `decide()` function of (txn, proposal, prior state)
  — deterministic and unit-tested — plus a DB-backed service that reads/writes
  cumulative caps, monthly auto-post share, and per-account error rates. Error
  rate is windowed to a trailing 90 days with an org-global fallback.
- **Provenance join.** One endpoint returns the full paper trail for any line:
  line → entry → routing decision (stored thresholds + reason) → proposal →
  source document → reviewer → hash. The UI renders it verbatim.
- **Tamper-evidence.** Per-org SHA-256 hash chain over entries and an
  append-only, hash-chained `audit_log`; `verify_chain()` / `verify_audit_chain()`
  recompute and compare. A conformance test proves direct SQL tampering is
  detected.
- **Close workflow.** Period close is blocked (HTTP 409) until reconciliation is
  approved; posting into a locked period is rejected (HTTP 422). Lane
  authorization is server-side (a bookkeeper cannot clear a controller/hard-stop
  item — HTTP 403).

---

## 4. Run it in ~10 minutes

Prerequisites: Docker Desktop running, plus Node 18+ for the web app.

```bash
# 1. Database (Postgres 16) — schema auto-applies on first start
docker compose up -d db

# 2. API on :8000
docker compose up -d api
curl http://localhost:8000/health          # -> {"status":"ok"}

# 3. Seed the demo company (synthetic data)
docker compose run --rm -v "$PWD":/repo -w /repo \
  -e PYTHONPATH=/repo/api api python scripts/seed_demo.py

# 4. Web app on :5173
npm --prefix web install
npm --prefix web run dev
```

Open **http://localhost:5173**. (A no-Docker path using a local Postgres cluster
is in `scripts/pg_local.sh` and the README.)

---

## 5. Test coverage

- **57 passing, 1 skipped** across 11 modules. The single skip is a *live* LLM
  smoke test, gated behind `RUN_LIVE_LLM=1` + `ANTHROPIC_API_KEY`; the rest of
  the suite mocks the Anthropic client, so **the suite needs no network**.
- Coverage is **invariant- and contract-focused**, which is the point of this
  system:
  - DB-level enforcement (`test_db_invariants.py`) — UPDATE/DELETE on journals
    denied, two concurrent unbalanced inserts both fail, AI role cannot write the
    ledger.
  - Policy logic (`test_policy_unit.py`) — routing branches, thresholds,
    determinism, lane authorization.
  - Integration/contract (`test_phase_b/c/d_*`) — idempotency, decision
    provenance, restart-survival, close-blocking, categorizer behavior.
  - Conformance (`test_conformance.py`) — 90-day error-window recovery and
    audit-log tamper detection.
- **What is *not* covered:** load/performance testing, security/penetration
  testing, browser/E2E UI automation, and multi-org concurrency at scale.

---

## 6. Known limitations (read before valuing this)

- **Auth is a header, not a system.** The acting role is the `X-GreenLedger-Role`
  request header. There is **no authentication, no user identity, no session,
  no SSO, no authorization beyond role-lane checks.** A real deployment must add
  an identity layer in front.
- **Demo data only.** No real ingestion. The seed is synthetic.
- **No bank feeds / no external financial integrations.** No Plaid, no
  QBO/Xero, no processor settlement un-netting. Ingestion is modeled close to
  one-line-per-document (see deferred item D3).
- **The live-LLM path needs the Anthropic SDK, which is not pinned.**
  `api/greenledger/llm.py` imports `anthropic` lazily; it is **not** in
  `api/requirements.txt`. The tests mock it, so the gate passes without it, but
  running the *real* categorizer requires `pip install anthropic` and an API
  key. Easy to fix; disclosed so it is not a surprise.
- **No SOC 2, no security audit, no compliance certification** of any kind.
- **Legal / CPA-of-record / compliance structure is research only.** The
  spec describes an accountability model; **none of it is implemented or
  operational.** This is not accounting or legal advice and must not be
  represented as a compliant bookkeeping service without the appropriate
  professional and regulatory structure.
- **Single-node assumptions.** Advisory-lock hash chaining and the pooling model
  are designed for a single Postgres primary; horizontal scale is unproven.

---

## 7. Honest state of each deferred-conformance item

From `docs/deferred-conformance.md` — spec-conformance gaps that were **found,
scoped, and deliberately deferred** (not silently dropped). All are additive;
none is a known bug in what exists.

| ID | Item | State | Est. |
|---|---|---|---|
| **D1** | `journal_entries.period_id` FK to periods | Not built — period membership is derived from `entry_date` at query time; no stored FK. | M (~1 day) |
| **D2** | Full `reconciliations` shape | Not built — close uses a reconciliation *gate/flag*; the per-account reconciliation table and workflow are absent. | L (~2–3 days) |
| **D3** | `raw_transactions`: one-doc-many-lines + status + `counterparty_id` | Not built — ingestion is ~one line per document; counterparty is free text, not a resolved FK; no normalization status. Settlement un-netting (the hard case) is unaddressed. | L (~2–3 days) |
| **D4** | UUID PKs / account-by-UUID throughout | Partial — UUID PKs exist, but the service resolves accounts by `(org_id, account_code)` for readability rather than by UUID everywhere. | M (~1 day) |
| **D5** | Independent-model adversarial close checks (spec §4.3) | Not built — close blocks on reconciliation, but the independent-model red-team pass and its six checks are not implemented. | L (~3+ days) |
| **D6** | HARD_STOP customer-notice + tax/bank-facing → `cpa_of_record` flows | Not built — the escalation ladder routes lanes and enforces roles, but the customer-notice side effect and the `cpa_of_record` authority tier are absent. | M (~1–2 days) |

These estimates are the seller's engineering judgment, not commitments.

---

## 8. Recommended buyer verification

1. Clone at `release/ip-sale`, run §4, confirm the app renders on real seeded
   data.
2. Run `scripts/run_gate.sh` (or the CI workflow) and confirm **57 passed, 1
   skipped**, trial balance 0, chain verified, tamper detected.
3. Read `DEVIATIONS.md` and `docs/deferred-conformance.md` in full.
4. Commission independent **security**, **license/OSS-compliance**, and (if
   operating as a bookkeeping service) **accounting/regulatory** review — none
   of which the seller has performed.
