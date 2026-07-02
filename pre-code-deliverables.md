# Pre-Code Deliverables — AI-Native Full-Service Finance Function

**Prepared per Section 10 of the Operating Brief, updated against the July 2026 competitive landscape.**

This document contains the four deliverables the brief requires before any code is written, plus a preliminary section revising the brief's positioning in light of what Pilot, Bench, Digits, and Puzzle have shipped since the brief was drafted.

---

## 0. Competitive Repositioning — What Changed and What It Means

### 0.1 The 2026 state of play

| Player | What they are now | Ledger | Human accountability | Target customer | Price signal |
|---|---|---|---|---|---|
| **Pilot** | Launched fully autonomous "AI Accountant" (Feb 2026): onboarding-to-close with zero human intervention, plus tiered human service on top. Also selling "Meridian," an AI close OS to accounting firms. | Runs on **QuickBooks Online** (provisions and manages the client's QBO file) | Optional — the $99 AI-only tier has no human bookkeeper; higher tiers add bookkeepers/controllers/CFOs | Venture-backed startups first, SMBs second | $99 (AI-only) → $299+ (Core, scales with expenses) → $1,750–$5,250/mo (CFO) |
| **Digits** | AI-native "Agentic General Ledger": specialized in-house models (trained on $825B+ of SMB transactions, explicitly not raw LLMs), Agentic Close (Jun 2026), automated accrual Schedules (May 2026), MCP server (Apr 2026). Sells both direct and through 700+ accounting-firm partners. | **Owns its ledger** | Backed by "expert human CPAs" language, but accountability model is diffuse; firm channel puts the partner firm's license on the line | General SMBs + accounting firms | Software pricing + wholesale firm pricing |
| **Puzzle** | AI-native GL for startups; ~98% auto-categorization, AI Accuracy Reviews, AI Close Agents; nothing posts without accountant approval; explicitly partners with firms rather than displacing them | **Owns its ledger** | None — it is software; the customer's accountant is accountable | US startups + the firms serving them | Free under $20K volume; $30–$150/mo tiers |
| **Bench (Employer.com)** | Collapsed Dec 27, 2024, locking 12,000+ businesses out of their books; acquired in days by an HR-tech company with no bookkeeping experience; relaunched but D- BBB rating, late filings, bookkeeper turnover, and still **no clean data export** from its proprietary platform | Proprietary, closed | Degraded | Main Street SMBs | ~$189–$299/mo |

### 0.2 Implications for the brief — seven revisions

1. **Phase 1 as written is now table stakes, not a product.** AI categorization with confidence gating on a deterministic ledger is what Digits and Puzzle *sell today*, and Pilot claims to have automated the entire close. The brief's Phase 1 "done" criterion (95% categorization agreement) is roughly where the market already is. Keep the phase discipline, but understand Phase 1 is proving *your* pipeline, not proving a market-differentiating capability.

2. **The durable wedge is the accountability + service stack, not the AI.** The brief predicted this ("your durable edge is the trust architecture and the CPA-of-record model, not raw AI capability, which will commoditize") — it has now happened, faster than the brief assumed. The unoccupied position: **owned deterministic ledger + full service through CFO tier + a named, licensed CPA-of-record on every filing + contractual data portability**, sold to Main Street. Pilot has service but rents its ledger (QBO) and offers an accountability-free AI tier; Digits and Puzzle own ledgers but are primarily software; Bench proved what happens when service + proprietary ledger has *no* portability or accountability.

3. **Target the customer the AI-native players ignore.** Pilot and Puzzle are architected around the venture-backed startup (Stripe/Mercury/Brex/Ramp/Gusto stacks, burn/runway metrics). Digits targets general SMBs but sells software-first. The brief's ICP — the $500K–$20M *non-startup* operating business with messy multi-channel data, some cash, inventory, field crews — is underserved by all three. Sharpen the ICP away from "small business" toward **Main Street operating companies**, and make the connector roadmap match (QBO migration, Square/Toast/Shopify/Amazon, ADP/Paychex, not just the startup fintech stack).

4. **Weaponize the Bench collapse.** Post-Bench, "who is accountable, and can I leave with my books?" are live buying questions with documented horror stories. Add two contractual product features: (a) **Data Portability Guarantee** — full GL export to QBO/Xero format at any time, self-serve, in the contract; (b) **Named Reviewer of Record** — the customer knows the licensed human who signs their filings. These cost little and directly convert Bench refugees and Bench-aware skeptics.

5. **Adopt the second channel or defend against it.** Digits and Puzzle both discovered the accounting-firm channel (Puzzle's whole GTM; 700+ firms applied to Digits' partner program). Decide explicitly in Phase 2 whether to (a) stay direct-only, accepting that firms armed with Digits/Puzzle become competitors, or (b) add a white-label/partner tier. Recommendation: stay direct through Phase 2 (the CPA-of-record model is *your* firm — franchising it early multiplies regulatory exposure), revisit at Phase 3.

6. **Compress Phase 0 without compromising it.** The brief budgets 3 months to build a ledger engine. Competitors spent years on theirs. Keep the deterministic-ledger principle, but do not build from a blank page: build on Postgres with an existing double-entry pattern/library as the starting skeleton, and spend the saved time on the ingestion/normalization layer, which is where Main Street messiness actually lives. Phase 0 exit criterion stands unchanged (replay a real business's year against its filed tax return with zero arithmetic discrepancy).

7. **Reprice with eyes open.** Pilot's $99 AI-only tier resets the price floor for "software-ish bookkeeping." Do not chase it — that tier has no human accountability and is cash-basis only. Hold the brief's $500 floor but justify it explicitly: every close controller-reviewed, named CPA on filings, portability guarantee. The comparison to beat is not Pilot's $99 tier; it is the $2,500–$15,000/mo stitched-together human alternative and Pilot's Core+CFO stack ($2,049+/mo combined).

---

## 1. Deliverable 1 — Ledger Engine Data Model

Design goals, in order: (1) arithmetic correctness enforced by the database, not application code; (2) immutability with reversal-only corrections; (3) every number traceable to a source document; (4) AI proposals stored *separately* from posted facts; (5) replayability — the ledger state at any past moment can be reconstructed exactly.

### 1.1 Entity overview

```
organizations ─┬─ accounts (chart of accounts)
               ├─ source_documents (immutable, hashed)
               ├─ raw_transactions (normalized ingestion output)
               ├─ categorization_proposals (AI layer output — NOT ledger facts)
               ├─ journal_entries ─── journal_lines (the only ledger facts)
               ├─ review_queue / approvals
               ├─ accounting_periods (close + locking)
               ├─ reconciliations
               └─ audit_log (append-only, hash-chained)
```

### 1.2 Schema (PostgreSQL)

```sql
-- ============================================================
-- Core reference
-- ============================================================
CREATE TABLE organizations (
    org_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    legal_name      TEXT NOT NULL,
    entity_type     TEXT NOT NULL,        -- llc, s_corp, c_corp, sole_prop, partnership
    fiscal_year_end DATE NOT NULL,
    accounting_basis TEXT NOT NULL CHECK (accounting_basis IN ('cash','accrual','both')),
    home_state      CHAR(2) NOT NULL,     -- drives compliance rules
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE accounts (
    account_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES organizations,
    code            TEXT NOT NULL,        -- e.g. '5010'
    name            TEXT NOT NULL,
    account_type    TEXT NOT NULL CHECK (account_type IN
                      ('asset','liability','equity','revenue','expense','contra')),
    normal_balance  TEXT NOT NULL CHECK (normal_balance IN ('debit','credit')),
    parent_id       UUID REFERENCES accounts(account_id),
    is_active       BOOLEAN NOT NULL DEFAULT true,
    UNIQUE (org_id, code)
);

-- ============================================================
-- Source documents: first-class, immutable, content-addressed
-- Nothing may be posted without one.
-- ============================================================
CREATE TABLE source_documents (
    doc_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES organizations,
    doc_type        TEXT NOT NULL,        -- bank_feed_line, receipt, invoice, payroll_report,
                                          -- processor_settlement, statement, contract, manual_note
    source_system   TEXT NOT NULL,        -- plaid, stripe, gusto, email_ingest, upload, api
    external_ref    TEXT,                 -- id in the source system
    content_sha256  CHAR(64) NOT NULL,    -- hash of the raw bytes
    storage_uri     TEXT NOT NULL,        -- WORM / object-lock storage
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, content_sha256)       -- dedupe + tamper evidence
);
-- No UPDATE or DELETE grants on this table. Ever. Enforced at the role level.

-- ============================================================
-- Normalized transactions (Stage 2 output). Still not ledger facts.
-- ============================================================
CREATE TABLE raw_transactions (
    txn_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES organizations,
    doc_id          UUID NOT NULL REFERENCES source_documents,
    txn_date        DATE NOT NULL,
    amount_minor    BIGINT NOT NULL,      -- integer cents. NEVER float. NEVER numeric arithmetic in app code.
    currency        CHAR(3) NOT NULL DEFAULT 'USD',
    direction       TEXT NOT NULL CHECK (direction IN ('inflow','outflow')),
    counterparty_raw TEXT,                -- as it appears in the feed
    counterparty_id UUID,                 -- resolved vendor/customer (nullable until resolved)
    description_raw TEXT,
    status          TEXT NOT NULL DEFAULT 'unprocessed'
                    CHECK (status IN ('unprocessed','proposed','queued_review','posted','excluded')),
    external_line_hash CHAR(64) NOT NULL, -- hash of the source line: dedupe across re-pulled feeds
    UNIQUE (org_id, doc_id, external_line_hash)
);

-- ============================================================
-- AI layer output: proposals, never facts.
-- Full model provenance for the audit trail.
-- ============================================================
CREATE TABLE categorization_proposals (
    proposal_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    txn_id          UUID NOT NULL REFERENCES raw_transactions,
    proposed_account UUID NOT NULL REFERENCES accounts,
    proposed_class  TEXT,                 -- department / location / job
    rationale       TEXT NOT NULL,        -- plain-language, customer-showable
    confidence      NUMERIC(4,3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    pattern_match   TEXT NOT NULL CHECK (pattern_match IN ('seen','similar','novel')),
    model_id        TEXT NOT NULL,        -- exact model + version
    prompt_hash     CHAR(64) NOT NULL,    -- replayability of the reasoning step
    features_snapshot JSONB NOT NULL,     -- inputs the model saw
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    superseded_by   UUID REFERENCES categorization_proposals(proposal_id)
);

CREATE TABLE review_decisions (
    decision_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    proposal_id     UUID NOT NULL REFERENCES categorization_proposals,
    reviewer_type   TEXT NOT NULL CHECK (reviewer_type IN ('auto_policy','bookkeeper','controller','cpa_of_record')),
    reviewer_id     UUID,                 -- null only for auto_policy
    outcome         TEXT NOT NULL CHECK (outcome IN ('approved','corrected','rejected','escalated')),
    corrected_account UUID REFERENCES accounts,
    note            TEXT,
    decided_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- review_decisions is the training-feedback table AND the accountability table.

-- ============================================================
-- The ledger itself. Append-only. Balanced by constraint.
-- ============================================================
CREATE TABLE accounting_periods (
    period_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES organizations,
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open','soft_close','closed','locked')),
    closed_by       UUID,                 -- controller who approved the close
    closed_at       TIMESTAMPTZ,
    UNIQUE (org_id, period_start)
);

CREATE TABLE journal_entries (
    entry_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES organizations,
    period_id       UUID NOT NULL REFERENCES accounting_periods,
    entry_date      DATE NOT NULL,
    entry_type      TEXT NOT NULL CHECK (entry_type IN
                      ('standard','accrual','depreciation','reclass','reversal','opening_balance')),
    memo            TEXT,
    reverses        UUID REFERENCES journal_entries(entry_id),  -- reversal chain
    posted_by_policy TEXT NOT NULL,       -- which rule authorized posting (see Deliverable 2)
    posted_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    entry_hash      CHAR(64) NOT NULL     -- sha256(prev_entry_hash || canonical(this entry))
);
-- Hash chain per org: any historical tampering breaks every subsequent hash.

CREATE TABLE journal_lines (
    line_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entry_id        UUID NOT NULL REFERENCES journal_entries,
    account_id      UUID NOT NULL REFERENCES accounts,
    side            TEXT NOT NULL CHECK (side IN ('debit','credit')),
    amount_minor    BIGINT NOT NULL CHECK (amount_minor > 0),
    txn_id          UUID REFERENCES raw_transactions,   -- provenance link
    doc_id          UUID NOT NULL REFERENCES source_documents  -- REQUIRED provenance
);

-- Balance enforcement: deferred constraint trigger — an entry cannot commit unbalanced.
CREATE OR REPLACE FUNCTION assert_entry_balanced() RETURNS trigger AS $$
BEGIN
    IF (SELECT COALESCE(SUM(CASE WHEN side='debit'  THEN amount_minor END),0)
             - COALESCE(SUM(CASE WHEN side='credit' THEN amount_minor END),0)
        FROM journal_lines WHERE entry_id = NEW.entry_id) <> 0
    THEN RAISE EXCEPTION 'Journal entry % does not balance', NEW.entry_id;
    END IF;
    RETURN NULL;
END $$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER trg_balanced
    AFTER INSERT ON journal_lines
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION assert_entry_balanced();

-- Immutability: no UPDATE/DELETE grants on journal_entries / journal_lines.
-- Corrections = a reversal entry + a new entry, both logged. Period lock trigger
-- additionally rejects INSERTs dated into a 'closed' or 'locked' period.

-- ============================================================
-- Reconciliation and audit
-- ============================================================
CREATE TABLE reconciliations (
    recon_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES organizations,
    period_id       UUID NOT NULL REFERENCES accounting_periods,
    account_id      UUID NOT NULL REFERENCES accounts,
    statement_doc   UUID NOT NULL REFERENCES source_documents,
    statement_balance_minor BIGINT NOT NULL,
    ledger_balance_minor     BIGINT NOT NULL,
    difference_minor BIGINT GENERATED ALWAYS AS
        (statement_balance_minor - ledger_balance_minor) STORED,
    status          TEXT NOT NULL CHECK (status IN ('draft','ai_checked','controller_approved')),
    approved_by     UUID,
    approved_at     TIMESTAMPTZ
);

CREATE TABLE audit_log (
    log_id          BIGSERIAL PRIMARY KEY,
    org_id          UUID NOT NULL,
    actor_type      TEXT NOT NULL CHECK (actor_type IN ('ai_model','policy_engine','human','system')),
    actor_id        TEXT NOT NULL,
    action          TEXT NOT NULL,
    object_type     TEXT NOT NULL,
    object_id       UUID NOT NULL,
    detail          JSONB NOT NULL,
    at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    row_hash        CHAR(64) NOT NULL     -- chained, same scheme as entries
);
-- Retention: >= 7 years, mirrored to WORM object storage nightly.
```

### 1.3 Design decisions worth stating explicitly

**Integer minor units everywhere.** All amounts are `BIGINT` cents. No floats anywhere in the pipeline; currency conversion (if ever) happens in a dedicated module with explicit rounding rules and a logged rounding account.

**Proposals are not facts.** The AI writes only to `categorization_proposals`. The only path into `journal_entries` is the policy engine (Deliverable 2) or a human decision recorded in `review_decisions`. This makes "no output rests solely on an LLM's unverified arithmetic" a *schema property*, not a code-review hope.

**Provenance is a NOT NULL constraint.** `journal_lines.doc_id` is required. The customer-facing "why is this number what it is" feature is a join, not a feature build: line → entry → proposal → rationale → document.

**Hash-chained append-only tables** give tamper evidence cheaply and make the SOC 2 story concrete from day one.

**Replayability.** `prompt_hash`, `model_id`, and `features_snapshot` on every proposal mean any historical AI decision can be re-run and compared — this is what makes the adversarial self-check (Section 4.3 of the brief) and the accuracy audits (Section 1 deliverable b) mechanically possible.

**What Digits/Puzzle taught us to add:** Puzzle maintains cash and accrual views simultaneously; the `accounting_basis = 'both'` flag plus entry-type discipline supports the same without duplicate books. Digits' continuous close (Agentic Close) implies the `soft_close` period status: the AI can draft-close continuously during the month while the controller's `closed` action remains a discrete, accountable event.

---

## 2. Deliverable 2 — Confidence-Threshold Logic (Pseudocode)

This implements Section 5 of the brief in code, adds the dynamic tightening and adversarial second pass from Section 4.3, and closes two gaps the brief left open: cumulative-amount gaming and model-drift response.

```python
# ============================================================
# Policy engine: the ONLY writer of auto-posted journal entries.
# Deterministic. Versioned. Every decision logged with the
# policy version that made it.
# ============================================================

POLICY_VERSION = "1.0.0"

# --- Static thresholds (from brief Section 5, extended) -------------
BASE = {
    "auto_post_max_amount":        500_00,      # cents
    "hard_stop_amount":         10_000_00,
    "auto_post_min_confidence":     0.97,       # seen patterns only
    "queue_min_confidence":         0.80,       # below this: never auto, always queue
    "novel_pattern_auto_post":      False,      # novel vendors NEVER auto-post, any amount
    "daily_counterparty_cum_cap":  2_000_00,    # anti-structuring: many small txns, same day, same vendor
    "monthly_auto_post_share_cap":  0.90,       # at least 10% of postings sampled to humans, always
}

# --- Dynamic tightening (brief 4.3: thresholds tighten if error rises) ----
def effective_thresholds(org, gl_account):
    """
    Error rate = corrections / decisions from review_decisions,
    trailing 90 days, per (org, account) with global fallback.
    """
    err = trailing_error_rate(org, gl_account, days=90)
    t = BASE.copy()
    if err > 0.05:                       # >5% correction rate: no autonomy on this account
        t["auto_post_min_confidence"] = 1.01     # unreachable => everything queues
    elif err > 0.02:
        t["auto_post_min_confidence"] = 0.99
        t["auto_post_max_amount"]     = 250_00
    return t

# --- Main decision function ----------------------------------------
def route(txn, proposal, org):
    t = effective_thresholds(org, proposal.account)

    # 1. Hard stops — order matters; these cannot be overridden by confidence.
    if txn.amount >= t["hard_stop_amount"] or fraud_heuristics(txn, org):
        return Decision.HARD_STOP           # human review + customer notification

    if is_related_party(txn, org) or touches_equity_or_tax_accounts(proposal):
        return Decision.CONTROLLER_QUEUE    # never a bookkeeper-level auto decision

    # 2. Anti-structuring: cumulative same-counterparty check.
    #    Ten $450 charges from one vendor in a day must not slide under a $500 gate.
    if day_cum(txn.counterparty, txn.date, org) + txn.amount > t["daily_counterparty_cum_cap"]:
        return Decision.BOOKKEEPER_QUEUE

    # 3. Novel pattern: queue regardless of confidence or size.
    if proposal.pattern_match == "novel" and not t["novel_pattern_auto_post"]:
        return Decision.BOOKKEEPER_QUEUE

    # 4. Confidence + amount gate for seen patterns.
    if (proposal.pattern_match == "seen"
            and proposal.confidence >= t["auto_post_min_confidence"]
            and txn.amount < t["auto_post_max_amount"]
            and monthly_auto_share(org) < t["monthly_auto_post_share_cap"]):
        return Decision.AUTO_POST           # logged; sampled into human QA at 5%

    # 5. Everything else queues; below queue_min_confidence flag as low-confidence.
    return (Decision.BOOKKEEPER_QUEUE_LOWCONF
            if proposal.confidence < t["queue_min_confidence"]
            else Decision.BOOKKEEPER_QUEUE)

# --- Close-time adversarial pass (brief 4.3 "red team") -------------
def pre_close_adversarial_check(org, period):
    """
    Independent model, independent prompt lineage, whose ONLY task is
    to find errors in the drafted close. Runs before the controller sees it.
    """
    draft = assemble_draft_close(org, period)
    findings = adversarial_model.review(
        draft,
        checks=[
            "categorization_inconsistency_vs_history",
            "missing_recurring_accrual",
            "duplicate_source_document",
            "unusual_variance_vs_trailing_12m",
            "cutoff_errors_near_period_boundary",
            "balance_sheet_accounts_not_reconciled",
        ],
    )
    for f in findings:
        open_review_item(f, assignee="controller", blocking=f.severity >= Severity.HIGH)
    # A close CANNOT move to controller sign-off with open blocking items.
    return findings

# --- Escalation ladder (who may approve what) ------------------------
APPROVAL_AUTHORITY = {
    Decision.AUTO_POST:            "policy_engine",
    Decision.BOOKKEEPER_QUEUE:     "bookkeeper",
    Decision.BOOKKEEPER_QUEUE_LOWCONF: "bookkeeper",
    Decision.CONTROLLER_QUEUE:     "controller",
    Decision.HARD_STOP:            "controller",     # + customer notice
    "period_close":                "controller",     # every close, no exceptions
    "tax_filing":                  "cpa_of_record",  # every filing, no exceptions, ever
    "bank_facing_statement":       "cpa_of_record",
}
```

Three additions beyond the brief's table, each with a reason:

1. **Cumulative caps (anti-structuring).** A per-transaction $500 gate is trivially gamed by volume — by a fraudster, or innocently by a vendor that bills in small increments. The daily same-counterparty cumulative cap closes it.
2. **Auto-post share cap.** Even at high confidence, never let human review coverage fall below a floor (10% sampled). This is the ongoing measurement channel for deliverable (b) accuracy audits — without forced sampling, a drifting model looks perfect because nobody checks it.
3. **Unreachable-threshold degradation.** When an account's correction rate exceeds 5%, autonomy on that account switches off entirely rather than degrading gracefully. Graceful degradation on a failing model is how "confidently wrong" reaches customers.

---

## 3. Deliverable 3 — First 10 Pilot Customers

Selection principles: (a) match the revised ICP — Main Street operating companies, not venture-backed startups (Pilot/Puzzle own that segment and it doesn't stress-test messiness); (b) each customer must stress a *different* pipeline stage; (c) all must have at least one filed tax return (Phase 0 exit test requires it); (d) exclude the brief's out-of-scope verticals (fund accounting, healthcare revenue cycle, pure-cash chaos); (e) at least three should be current QuickBooks users, because QBO migration is the dominant real-world onboarding path.

| # | Archetype (rev.) | What it stress-tests | Why chosen |
|---|---|---|---|
| 1 | **HVAC / plumbing contractor, ~$2.5M, 12 employees** | Job costing, progress billing, equipment depreciation, 1099 subcontractors | The canonical Main Street business the startup-native tools ignore; tests class/job dimension of categorization |
| 2 | **Multi-channel e-commerce brand, ~$4M (Shopify + Amazon + wholesale)** | Processor settlement reconciliation (fees, reserves, refunds netted inside deposits), inventory/COGS, sales tax nexus in many states | Settlement un-netting is the single hardest ingestion problem; nexus monitoring exercises the compliance layer early |
| 3 | **Single-location full-service restaurant, ~$1.8M, POS-based** | High transaction volume, tips payroll, cash component *with* POS records, vendor invoice OCR | Cash-adjacent but reconstructable — tests the boundary the brief drew ("qualify pilot customers accordingly") without crossing it |
| 4 | **Marketing/creative agency, ~$1.2M, 8 people** | Clean services business: retainers, deferred revenue, contractor mix | The control group — if the system can't run this near-fully-automated, nothing else matters; also the cost-to-serve floor datapoint |
| 5 | **Wholesale distributor, ~$8M, 20 employees** | Inventory, purchase orders, freight, volume rebates, AR aging | Largest transaction values in the pilot; exercises hard-stop and reconciliation logic at scale |
| 6 | **IT managed-services provider (MSP), ~$3M** | Recurring contracts, annual prepayments, deferred revenue schedules, software COGS | Accrual-heavy; directly tests the automated Schedules capability Digits just shipped — parity check |
| 7 | **Fitness studio with memberships, ~$900K** | Subscription billing (Stripe/Mindbody), refunds/chargebacks, instructor 1099s | Smallest customer — tests whether the $500/mo tier is actually profitable at the low end |
| 8 | **Boutique food manufacturer, ~$5M** | BOM-lite inventory, co-packer arrangements, slotting fees, distributor deductions | Deductions and chargebacks are adversarially messy data — great OCR/normalization stress |
| 9 | **Owner-operator trucking fleet, ~$2M, 6 trucks** | Fuel cards, factoring receivables, per-truck P&L, heavy depreciation, IFTA-adjacent compliance | Factoring is a trap for naive categorization (advances aren't revenue); tests rationale quality |
| 10 | **Franchisee (2 units of a national QSR brand), ~$3.5M** | Franchise royalties, required chart-of-accounts mapping to franchisor format, intercompany between units | Multi-entity-lite; tests reporting-layer flexibility and a repeatable niche (franchisees cluster — strong referral economics) |

Deliberately excluded, and why: venture-backed SaaS startup (Puzzle/Pilot's home turf, teaches nothing new), law firm (IOLTA trust accounting is a specialized liability regime — later vertical), medical practice (insurance revenue cycle — explicitly out of scope), real-estate syndication (fund accounting — out of scope), all-cash businesses (unreconstructable — the brief's data-quality ceiling).

Success instrumentation across the 10: per-customer categorization agreement rate, controller minutes per close, count of hard stops and their dispositions, and one full independent re-do of the books by an outside licensed accountant for customers #2, #5, and #9 (the three hardest) — that re-do is deliverable (b) from Section 1 of the brief.

---

## 4. Deliverable 4 — State-by-State Legal Research Framing (CPA-Signature / Review Layer)

This is a research *plan with the load-bearing facts established*, per the brief — counsel executes it before scaling; nothing here is legal advice.

### 4.1 The two regulatory perimeters

**Perimeter 1 — Federal tax practice (uniform, easiest).** Preparing and filing tax returns for compensation is governed federally: PTIN registration for any paid preparer, and IRS Circular 230 for practice/representation before the IRS, which is open to CPAs, Enrolled Agents, and attorneys in all 50 states. The brief's "licensed CPA/EA signs every filing" model maps cleanly here, and an **EA is a federally licensed alternative** wherever CPA-firm structuring is slow — useful sequencing flexibility.

**Perimeter 2 — State accountancy law (fragmented, the real work).** State boards regulate (a) who may *hold out* as a CPA or CPA firm, (b) which services trigger firm licensure/registration, and (c) firm ownership. The critical distinction is **attest vs. non-attest**:

- *Bookkeeping, write-up work, tax preparation, and advisory are generally non-attest* and in most states may be performed by unlicensed persons/companies — provided no CPA holding-out and no attest-reserved language ("audited," "reviewed," and in many states "compiled") is used.
- *Attest services* (audits, reviews, and in most states compilations under SSARS) are reserved to licensed CPA firms, which must be majority-owned by licensed CPAs. This is where the brief's phrase "financial statement attestation... bank-facing document" needs precision: a bank asking for "reviewed" or "compiled" financials triggers the licensed-firm requirement; a "preparation of financial statements" engagement under SSARS AR-C 70 generally does not, but state treatment varies — this is a core matrix column below.

### 4.2 The structure the model requires: Alternative Practice Structure (APS)

Because the operating company will have outside (non-CPA) investors, it cannot itself be the licensed CPA firm in most states. The industry-standard solution — used in effectively every private-equity accounting deal since 2021 — is the **Alternative Practice Structure**: two entities.

1. **CPA Firm PLLC** — majority-owned (in most states ≥51%) by licensed CPAs, separately governed, registered with each state board where required. It performs anything attest-reserved and is the "reviewer/firm of record."
2. **ServicesCo (the startup)** — owns the technology, employs most staff, holds customer contracts for non-attest services, takes investment freely. Linked to the CPA firm by a long-term administrative services agreement at fair-market-value pricing.

Known state variation on the ownership rule (verify per state; these are documented examples): North Carolina caps non-CPA ownership at 49% with control in law and in fact by CPA license holders; Washington requires a simple CPA majority; California requires more CPA owners than non-CPA owners and requires non-CPA owners to be actively engaged in the firm; Texas permits non-CPA ownership under specific statutory conditions. The "actively engaged" requirement common across states is why passive investor ownership of the CPA entity is a non-starter — hence the APS.

### 4.3 The 50-state research matrix (columns counsel must fill)

For each state: (1) definition of "practice of public accountancy" and the unlicensed-activity (UPA) statute; (2) holding-out rules — what the ServicesCo may and may not call itself and its outputs; (3) whether *compilation* and/or *preparation* engagements trigger firm licensure and peer review; (4) firm registration/permit requirements for the CPA PLLC, including out-of-state firm mobility (UAA Section 23 adoption status — most states allow individual CPA mobility, but *firm* mobility for attest work often still requires registration); (5) non-CPA ownership cap and "actively engaged" definition; (6) fee-splitting and commission rules between ServicesCo and CPA firm; (7) confidential-client-information sharing rules between the two entities; (8) sales-tax and payroll-tax filing agent rules (generally no CPA needed, but registration as a filing agent sometimes is).

**Prioritization:** run the full analysis first for the pilot states only — recommend TX, FL, NC, GA, TN as the initial footprint (large SMB bases, documented ownership rules, no state income tax complexity in three of them reduces filing-scope in year one), plus the state of incorporation. Defer CA and NY (highest-friction boards) until Phase 2. This converts a 50-state problem into a 6-state Phase 1 problem.

### 4.4 Standing constraints for product and marketing (encode now)

Never use "audit," "review," "attest," or "certified" in customer-facing output from ServicesCo; financial statements delivered without a licensed-firm engagement carry a standardized "no assurance is provided" legend; the CPA-of-record's name appears only on outputs the CPA firm entity actually issued; and the confidence-threshold engine (Deliverable 2) routes anything filing-adjacent to `cpa_of_record` regardless of confidence — the legal architecture and the escalation ladder must be the same diagram.

---

## 5. Go / No-Go Posture

Proceed to Phase 0 build, with the seven revisions in Section 0.2 incorporated into the brief. The competitive review does not weaken the thesis — Bench's collapse strengthened the trust-transfer framing, and Pilot/Digits/Puzzle commoditizing the AI layer confirms the brief's own prediction that the durable edge is accountability architecture, not model capability. What the review *does* change is urgency: the window to establish the "accountable AI finance firm for Main Street" position is open now and will not stay open through a leisurely 24-month roadmap. Compress Phase 0 per revision #6.
