-- Bedrock — Phase C schema: bank reconciliation approvals.
--
-- A close cannot be approved until every reconciliation-required account has an
-- approved reconciliation for the period. The check is computed server-side
-- (see close_service.py); this table is the durable record of the approval.
CREATE TABLE reconciliations (
  org_id       uuid NOT NULL REFERENCES orgs(org_id),
  account_code text NOT NULL,
  period_key   text NOT NULL CHECK (period_key ~ '^[0-9]{4}-[0-9]{2}$'),
  approved_by  text NOT NULL,
  approved_role text NOT NULL,
  approved_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, account_code, period_key)
);

GRANT SELECT, INSERT, UPDATE ON reconciliations TO bedrock_app;
