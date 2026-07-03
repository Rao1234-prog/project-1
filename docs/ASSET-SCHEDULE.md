# Asset Schedule — GreenLedger IP Sale

**Purpose.** Exhaustive inventory of the assets that transfer in the outright,
one-time IP sale of GreenLedger to a single buyer. Referenced by
`docs/IP-ASSIGNMENT-DRAFT.md` as "the Assets."

**Status of this document:** factual inventory prepared by the seller. It is
**not** a legal opinion. Figures such as the test count are reproducible from the
repository at the point of sale.

---

## A. What transfers

### A1. Source repository (full)
- The complete Git repository at branch `release/ip-sale`, **including full
  commit history** from first commit through the sale commit.
- All application source:
  - **Backend** — `api/greenledger/` (FastAPI service, policy engine,
    categorizer, ledger service, close service, LLM client, DB pool).
  - **Frontend** — `web/` (Vite + React app, design tokens, API client).
  - **Database** — `db/sql/` (ordered schema `01`–`08`), `db/apply.sh`,
    `db/docker-initdb/` bootstrap.
  - **Prototypes (the behavioral contract)** — `ledger.py`, `policy.py`,
    `run_phases.py`, `greenledger-app.jsx` at the repo root.
  - **Scripts** — `scripts/` (local Postgres runner, seed, gate runner, demo
    walkthroughs).
- **Infrastructure** — `docker-compose.yml`, `api/Dockerfile`.

### A2. Specification & design record
- `pre-code-deliverables.md` — the binding spec (Sections 1–2) plus the market
  repositioning analysis (Section 0).
- `DEVIATIONS.md` — the 9 deliberate, documented deviations from the spec and
  their rationale.
- `docs/deferred-conformance.md` — the tracked deferred-work register (items
  D1–D6) with spec references and size estimates.
- `README.md` — phase-by-phase build record and the enforcement/architecture
  narrative.

### A3. Test suite & CI
- The full pytest suite — **57 passing tests, 1 skipped** (the skip is the live
  LLM smoke test, which requires an Anthropic API key). Spread across 11 test
  modules: DB-level invariants, policy unit logic, Phase B/C/D integration and
  contract tests, conformance (90-day window recovery + audit-chain tamper
  detection), and the categorizer.
- `.github/workflows/gate.yml` — GitHub Actions CI running the full gate
  (Postgres 16 service container → schema reset → pytest → `vite build`) on
  every push.
- `scripts/run_gate.sh` — the local equivalent gate + demo walk.

### A4. Design assets
- `web/src/tokens.js` — the single-source design-token module (palette, IBM Plex
  Serif/Sans/Mono type roles, spacing).
- Vendored IBM Plex fonts via `@fontsource` (see A5 for their licensing).

### A5. Seed / demo data
- `scripts/seed_demo.py` and the in-repo demo dataset for the fictional company
  **"Cardinal Heating & Air LLC"** — chart of accounts, 18 source documents, 12
  posted entries, and a 6-item review queue. **Synthetic/demo data only** — no
  real business's books.

### A6. Documentation for the buyer
- This `docs/ASSET-SCHEDULE.md`, `docs/BUYER-DILIGENCE.md`, and
  `docs/IP-ASSIGNMENT-DRAFT.md`.
- `docs/screenshots/` — reference screenshots of the running application.

---

## B. What does NOT exist / is NOT part of the sale

State honestly so the buyer forms no incorrect expectation:

- **No domain name.** No `greenledger.*` (or other) domain is owned or conveyed.
- **No trademark.** "GreenLedger" is a rename chosen to avoid the AWS Bedrock
  collision; **no trademark search, registration, or clearance has been done.**
  The buyer is responsible for clearing and protecting any name it adopts.
- **No customers.** Zero users, zero pilots, zero signed contracts, zero
  pipeline.
- **No revenue.** The product has never been sold or monetized.
- **No patents or patent applications.** None filed, none pending.
- **No third-party integrations** beyond the **Anthropic API usage pattern** in
  the categorizer (`api/greenledger/llm.py`). Specifically: **no bank-feed /
  Plaid connector, no QBO/Xero import/export, no Stripe/Square/Shopify/Amazon
  ingestion, no payroll integration.** Ingestion in the demo is synthetic.
- **No production infrastructure.** No hosted environment, no cloud accounts, no
  managed database, no secrets. Dev credentials in the repo are placeholder
  defaults (`app_secret` / `ai_secret`) and must be replaced.
- **No SOC 2, no audit, no certifications.** See `docs/BUYER-DILIGENCE.md`.
- **No authentication/identity system.** Acting role is carried in a request
  header (`X-GreenLedger-Role`); there is no login, no user store, no SSO.
- **No legal or accounting entity, license, or CPA-of-record structure.** The
  CPA-of-record and compliance concepts in the spec are **research and design
  only** and are not implemented or operational.
- **No employees, contractors, or assignable third-party work product** other
  than the seller's own work in this repository.

---

## C. Third-party components (inbound licenses the buyer inherits)

The software depends on open-source components under their own licenses, which
the buyer continues to use under those licenses (they are **not** owned or
sub-licensed by the seller). Non-exhaustive: PostgreSQL (PostgreSQL License),
FastAPI / Starlette / Pydantic / Uvicorn (MIT/BSD), psycopg (LGPL), React /
Vite (MIT), Recharts (MIT), IBM Plex fonts (SIL OFL 1.1), and the Anthropic
Python SDK (used at runtime for the optional live-LLM path; MIT). The buyer
should perform its own license review — see `docs/BUYER-DILIGENCE.md`.

---

## D. History note (read with the sweep report)

Because **full commit history** transfers, everything ever committed conveys
with it. Two items the buyer and seller should note explicitly:

1. The pre-rename history contains the old **"Bedrock"** name throughout (the
   rename is a forward change; history is not rewritten in this package).
2. Commit messages contain build-provenance trailers (`Co-Authored-By: Claude…`
   and `Claude-Session:` links to the seller's build sessions), and commit
   authorship shows the seller's GitHub identity (`Rao1234-prog`, GitHub-masked
   no-reply email).

No live secrets, API keys, private keys, or personal email addresses were found
in the working tree or history (see the sweep report). If the buyer requires a
name-clean or provenance-clean history, a history rewrite is a separate,
destructive operation that must be agreed before delivery — it is **not**
performed in this package, which preserves history intact.

The commit history was **retained intact by mutual decision** as part of the
asset (it documents the phase-gated build and test evolution). Development
provenance — the work was built AI-assisted under human-gated phases — is
disclosed in `docs/BUYER-DILIGENCE.md` §9. The `Claude-Session` links in commit
trailers resolve only for the authenticated seller account; an unauthenticated
visitor receives HTTP 403 and sees no conversation content.

---

## E. Transfer terms — seller-retained rights

Notwithstanding the outright assignment of the Assets, the **Seller retains the
right to reference, describe, and display non-confidential aspects of the work
(including screenshots and architecture) for portfolio and professional purposes
after transfer.** This retained right is limited to non-confidential material and
does not include the right to resell, relicense, or operate the Assets. It should
be reflected as an explicit carve-out in the assignment agreement
(`docs/IP-ASSIGNMENT-DRAFT.md`).
