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
