# Deviations from the spec (kept on purpose)

`pre-code-deliverables.md` Sections 1–2 are the binding contract. The A–D
cross-check surfaced the divergences below. Each was **adjudicated as kept** —
the implementation differs from the letter of the spec, and this file is the
honest record of why. (Adjudicated *conformance* changes are not here; they were
made — see `db/sql/08_conformance.sql`. Deferred larger items live in
`docs/deferred-conformance.md`.)

## 1. Auto-post share cap checked in projected form
Spec: `monthly_auto_share < cap`. We use `(auto+1)/total <= 0.90`, counting the
post about to happen — strictly more conservative, and conservative wins ties.

## 2. Routing split across `routing_decisions` + `review_queue`
Spec implies one routing record. We persist the immutable decision (policy
version, thresholds, reason) separately from the mutable work item (lane,
status), so an audit trail never mutates and the queue stays workable.

## 3. Balance invariant as a deferred trigger on `journal_entries`
Spec describes it narratively. We enforce it as a DEFERRABLE INITIALLY DEFERRED
constraint trigger firing at COMMIT, so even two concurrent unbalanced inserts
both fail at the DB — not in application code.

## 4. `journal_lines.line_id` is BIGSERIAL
Spec is silent on line identity. A surrogate BIGSERIAL gives lines a stable PK
without overloading `(entry_id, account_id)`, which is not unique per entry.

## 5. `entry_hash` filled at commit time
Spec lists the column. We compute the per-entry hash at commit inside the same
transaction as the post, so the hash always matches the committed rows and never
lags a separate write.

## 6. Accounts joined by `account_code`, not UUID, in the service layer
Spec models accounts by UUID PK. The service resolves by `(org_id,
account_code)` for readability; the UUID PK still exists. Full account-by-UUID is
deferred (`docs/deferred-conformance.md`).

## 7. Source documents store inline `raw` alongside (not instead of) `storage_uri`
Spec assumes external object storage. For a self-contained demo we keep the raw
bytes inline and content-hash them; `storage_uri`/`external_ref` remain for the
real-storage path.

## 8. Table/column cosmetic names
A few names differ from the spec's prose (e.g. lane/queue terminology). Renames
that were cheap and load-bearing were conformed (`legal_name`,
`content_sha256`); purely cosmetic ones were left to avoid churn without
behavioral gain.

## 9. Confidence stored as `NUMERIC(5,4)`
Spec is unspecific on precision. `NUMERIC(5,4)` fixes confidence to four decimals
(0.0000–1.0000), avoiding float drift while covering the full [0,1] range the
policy compares against.
