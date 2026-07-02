# Deferred conformance items

These are divergences from `pre-code-deliverables.md` Sections 1–2 that the A–D
cross-check surfaced and that were **deliberately deferred** (not built now).
Each is larger than a cheap column add — they change schema shape or add a new
subsystem — so they are tracked here as debt with a spec reference and a size
estimate.

> **Note:** the intent was to file one GitHub issue per item. The GitHub
> connector for this session is **not authenticated**, so issues could not be
> created here. This file is the tracked stand-in; port each section to an issue
> once the connector is authorized (claude.ai connector settings, or `/mcp` in
> an interactive session).

---

## D1 — `journal_entries.period_id` FK to `accounting_periods`
**Spec:** §1, `journal_entries` (line ~172) references `accounting_periods`.
**Now:** period membership is derived from `entry_date` at query time; there is
no stored FK.
**Why deferred:** requires backfilling `period_id` on existing entries and a
period-assignment rule at post time; touches the post path and the close logic.
**Size:** M (~1 day: migration + backfill + post-path change + tests).

## D2 — Full `reconciliations` shape
**Spec:** §1, `reconciliations` (line ~217) — `period_id`, per-account
reconciled balances, statement linkage.
**Now:** close-blocking uses a reconciliation *flag/gate*; the full
per-account reconciliation table and workflow are not modeled.
**Size:** L (~2–3 days: schema, ingestion of statement balances, UI surface,
close integration).

## D3 — `raw_transactions`: one-document-many-lines + status + `counterparty_id`
**Spec:** §1, `raw_transactions` (line ~106) — normalized ingestion output with
`counterparty_id` (nullable until resolved), a status lifecycle, and a
one-document-to-many-lines relationship.
**Now:** ingestion is modeled closer to one line per document; counterparty is a
free-text string, not a resolved FK; no explicit normalization status.
**Size:** L (~2–3 days: settlement/deposit un-netting is the hard case called
out in the spec's ICP table).

## D4 — UUID primary keys / account-by-UUID throughout
**Spec:** §1 models accounts and most entities by UUID PK.
**Now:** the service layer resolves accounts by `(org_id, account_code)` for
readability; the UUID PK exists but is not the join key everywhere.
**Size:** M (~1 day: mechanical but broad — touches most service queries and
tests).

## D5 — Remaining adversarial / red-team checks (brief §4.3)
**Spec:** §2, `pre_close_adversarial_check` (line ~334) — an **independent
model, independent prompt lineage** running six checks before controller
sign-off:
`categorization_inconsistency_vs_history`, `missing_recurring_accrual`,
`duplicate_source_document`, `unusual_variance_vs_trailing_12m`,
`cutoff_errors_near_period_boundary`, `balance_sheet_accounts_not_reconciled`.
**Now:** the close gate blocks on reconciliation; the independent-model
adversarial pass and the remaining checks are not implemented.
**Size:** L (~3+ days: second model wiring, prompt lineage isolation, six
check implementations, blocking-item integration with close).

## D6 — HARD_STOP customer-notice + tax/bank-facing → `cpa_of_record` flows
**Spec:** §2, `APPROVAL_AUTHORITY` (line ~357) — `HARD_STOP` carries a customer
notice; tax-filing and bank-facing decisions escalate to `cpa_of_record`.
**Now:** the escalation ladder routes lanes and enforces role authorization; the
customer-notice side effect and the `cpa_of_record` authority tier are not
built.
**Size:** M (~1–2 days: notification seam + a new authority role + routing).
