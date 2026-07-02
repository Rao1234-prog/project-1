# Bedrock

An AI-native bookkeeping system. Built in phases over a validated in-memory
prototype (`ledger.py`, `policy.py`, `run_phases.py`) and a frontend prototype
(`bedrock-app.jsx`). The prototype's invariants and test cases are the
behavioral contract and are preserved as each phase makes them real.

> **Throughline constraints (enforced in every phase):** integer minor units
> everywhere (no floats), every journal line requires a `doc_id`, no mutation
> paths on posted entries, and the AI writes only to the `proposals` table —
> all enforced by **schema grants**, not application code.

## Phase A — Persistent ledger engine ✅

Postgres 16 with the Section 1.2 schema, and a FastAPI service layer that
reimplements the ledger prototype over it.

### What's here

```
docker-compose.yml          # Postgres 16 + API service
db/
  sql/
    01_extensions.sql       # pgcrypto (sha256 for the hash chain)
    02_roles.sql            # bedrock / bedrock_app / bedrock_ai roles
    03_schema.sql           # tables, hash chain, deferred balance + period triggers
    04_grants.sql           # the trust boundary: append-only journals, AI = proposals only
  apply.sh                  # applies the ordered SQL (used by docker + local runner)
  docker-initdb/            # first-init hook for the postgres container
api/
  bedrock/
    db.py                   # connection pool (autocommit so deferred triggers fire at COMMIT)
    service.py              # LedgerService — the port of ledger.py
    models.py               # pydantic request/response models
    main.py                 # FastAPI app
  tests/                    # pytest: Phase 0 ports + new DB-level tests
scripts/
  pg_local.sh              # local postgres-16 runner (up | reset | down | env | psql)
  run_gate.sh             # brings up DB, runs pytest, prints the gate summary
```

### How the invariants are enforced by the schema

| Invariant | Mechanism |
|---|---|
| Entries balance | **Deferred** constraint trigger (`finalize_entry`) checks `sum(debit)=sum(credit)` at COMMIT |
| Append-only journals | `UPDATE`/`DELETE` **revoked** from `bedrock_app` on `journal_entries` / `journal_lines` |
| Provenance mandatory | `journal_lines.doc_id NOT NULL` + FK to `source_documents` |
| Tamper-evident | Per-org SHA-256 hash chain; `verify_chain()` recomputes and compares |
| No posting into a closed period | `check_period_open` BEFORE INSERT trigger |
| Integer minor units | `amount_minor BIGINT CHECK (> 0)` |
| AI writes only proposals | `bedrock_ai` has `INSERT` only on `proposals`, no grant on the ledger |

The hash-chain link is written by a `SECURITY DEFINER` trigger owned by the
superuser, so it can set `entry_hash` even though the app role has no `UPDATE` —
integrity comes from the database, not from trusting the application.

## Phase B — Policy engine + API ✅

The routing core of `policy.py`, ported behind the API with all policy state in
Postgres (nothing in process memory).

### Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /orgs/{org}/transactions` | Ingest a normalized txn + AI proposal → routing decision |
| `POST /orgs/{org}/transactions/{txn_id}/reviews` | approve / correct / reject with reviewer id + role |
| `GET /orgs/{org}/queue?lane=…` | Open review-queue items by lane |
| `GET /orgs/{org}/accounts/{code}/error-rate` | Per-account correction stats + effective thresholds |

### The requirements, and where they live

1. **Idempotency** — `transactions` has `UNIQUE (org_id, content_sha256)`; a
   re-pulled bank line dedupes to one transaction + one decision (logged
   `dedupe_transaction`). `bedrock/policy_service.py::ingest_transaction`.
2. **Decision provenance** — `routing_decisions` stores `policy_version`, the
   full `effective_thresholds` (jsonb) at decision time, and the `reason`
   string, rendered verbatim by the Phase C paper trail.
3. **Review + authorization** — `POST /reviews` records approve/correct/reject
   with reviewer identity + role; lane authorization is enforced server-side
   (`policy.py::role_may_clear` — a bookkeeper cannot clear `controller_queue`
   or `hard_stop`). Corrections update per-`(org, account)` error rates, which
   tighten thresholds only after ≥10 decisions.
4. **State survives restarts** — daily per-counterparty cumulative totals
   (`policy_daily_cum`), monthly auto-post share (`policy_month_counts`), and
   the 5% QA sampling of auto-posts all live in Postgres.
5. **Determinism** — the routing core (`policy.py::decide`) is a pure function
   of `(txn, proposal, prior state)`; tested for repeatability.
6. **AI boundary** — proposals are written through the `bedrock_ai` role
   (`ai_pool`); the ledger post and state writes go through `bedrock_app`.

### Phase B gate

`scripts/run_gate.sh` runs the ported Phase 1 simulation and asserts
**invariants** (not exact counts, which depend on the RNG): zero wrong
categorizations auto-posted, every txn ≥ $10,000 hard-stopped, every
novel-pattern txn queued, the cumulative cap firing on a same-day sequence, the
auto-post share under the cap, trial balance zero, and the chain verified.

## Phase C — Frontend on real data ✅

A Vite + React app (`web/`) that talks to the API instead of in-memory state,
plus the server-side surface the UI renders. **The UI enforces nothing** — every
rule (lane authorization, close-blocking) is enforced by the API.

### Server-side first

| Endpoint | Purpose |
|---|---|
| `GET /orgs/{org}/balances` | every account with its balance (integer cents) |
| `GET /orgs/{org}/entries?limit&offset` | paginated journal entries + lines |
| `GET /orgs/{org}/entries/{id}/trail` | full provenance join in one response: line → entry → decision (stored thresholds + reason) → proposal → document → reviewer → hash |
| `GET /orgs/{org}/queue` | open review-queue items |
| `GET /orgs/{org}/audit` | audit log |
| `POST /orgs/{org}/reconciliations/approve` | approve a bank reconciliation (controller) |
| `GET /orgs/{org}/close/checklist?period=` | checklist + server-computed adversarial findings + blocking flags |
| `POST /orgs/{org}/close/approve` | **409 if any blocking finding** — the client cannot force a close (controller) |

### Role from the server boundary

The acting role is the `X-Bedrock-Role` header (`bookkeeper` / `controller` /
`cpa`) the UI's "acting as" switcher sets. The API enforces lane authorization
(a bookkeeper cannot clear a `controller_queue` or `hard_stop` item) and
requires `controller` to approve a reconciliation or a close.

### UI contract

Five tabs (Overview / Review queue / Ledger / Close / Advisor), the paper-trail
drawer rendered from the trail join, the close-blocking flow, the `CLOSED` stamp,
and the exact design tokens (palette + IBM Plex Serif/Sans/Mono roles) — all in
one module, `web/src/tokens.js`. Amounts travel as integer cents and are
formatted only at render. Every list has an empty state; every failed request
surfaces what went wrong and what to do (a 409 shows the blocking findings, a
403 shows the required role).

### Seed + run the frontend

```bash
# seed the June demo state (idempotent; --reset to rebuild)
BEDROCK_DATABASE_URL=… BEDROCK_AI_URL=… python3 scripts/seed_demo.py
uvicorn bedrock.main:app --app-dir api            # API on :8000
cd web && npm install && npm run dev              # app on :5173
```

### Phase C gate

`scripts/run_gate.sh` runs pytest (incl. the trail-join / close-409 / role-403
contract tests), builds the frontend (`vite build`), and walks the demo via the
API: the review queue is worked down, close is blocked (409), reconciliation is
approved, close is approved (period locks), and a post into the locked period is
rejected.

## Running it

### With Docker (preferred)

```bash
docker compose up -d db      # applies the schema on first init
docker compose up api        # FastAPI on :8000
```

### Local (no Docker registry access)

Uses the system's postgres-16 with the identical SQL:

```bash
scripts/pg_local.sh up       # initdb + start + apply schema
eval "$(scripts/pg_local.sh env)"   # export BEDROCK_*_URL
uvicorn bedrock.main:app --app-dir api
```

## Phase A gate

```bash
scripts/run_gate.sh
```

Runs the full pytest suite and prints the gate summary:

- **all pytest green** — Phase 0 invariants ported + `UPDATE` on
  `journal_entries` denied at the DB level + two simultaneous unbalanced inserts
  both fail
- **trial balance zero** after the seeded run
- **chain verification passes**
- **direct SQL tampering is detected**
