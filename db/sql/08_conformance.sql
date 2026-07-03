-- GreenLedger — Section 1/2 conformance pass (adjudicated against pre-code-deliverables.md).
-- Applied on top of 01-07. Idempotent-friendly (fresh reset re-applies cleanly).

-- ============================================================
-- organizations: rename + spec columns (defaults keep ensure_org(name) working)
-- ============================================================
ALTER TABLE orgs RENAME COLUMN name TO legal_name;
ALTER TABLE orgs
  ADD COLUMN IF NOT EXISTS entity_type      text NOT NULL DEFAULT 'llc',
  ADD COLUMN IF NOT EXISTS fiscal_year_end  date NOT NULL DEFAULT '2026-12-31',
  ADD COLUMN IF NOT EXISTS accounting_basis text NOT NULL DEFAULT 'accrual'
                            CHECK (accounting_basis IN ('cash','accrual','both')),
  ADD COLUMN IF NOT EXISTS home_state       char(2) NOT NULL DEFAULT 'TX';

-- ============================================================
-- accounts: 6-type enum, explicit sensitivity flag, hierarchy
-- ============================================================
ALTER TABLE accounts
  ADD COLUMN IF NOT EXISTS is_sensitive boolean NOT NULL DEFAULT false,  -- policy sensitivity (spec: touches_equity_or_tax)
  ADD COLUMN IF NOT EXISTS parent_id    uuid REFERENCES accounts(account_id),
  ADD COLUMN IF NOT EXISTS is_active    boolean NOT NULL DEFAULT true;
ALTER TABLE accounts DROP CONSTRAINT IF EXISTS accounts_account_type_check;
ALTER TABLE accounts ADD CONSTRAINT accounts_account_type_check
  CHECK (account_type IN ('asset','liability','equity','revenue','expense','contra'));

-- ============================================================
-- source_documents: content_sha256 + external_ref
-- ============================================================
ALTER TABLE source_documents RENAME COLUMN sha256 TO content_sha256;
ALTER TABLE source_documents ADD COLUMN IF NOT EXISTS external_ref text;

-- ============================================================
-- journal_entries: memo nullable (spec)
-- ============================================================
ALTER TABLE journal_entries ALTER COLUMN memo DROP NOT NULL;

-- ============================================================
-- accounting_periods: soft_close status (spec / continuous close)
-- ============================================================
ALTER TABLE periods DROP CONSTRAINT IF EXISTS periods_status_check;
ALTER TABLE periods ADD CONSTRAINT periods_status_check
  CHECK (status IN ('open','soft_close','closed','locked'));

-- ============================================================
-- categorization_proposals: replayability invariants
-- ============================================================
ALTER TABLE proposals
  ADD COLUMN IF NOT EXISTS features_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb;  -- inputs the model saw
UPDATE proposals SET prompt_hash = encode(digest(proposal_id::text,'sha256'),'hex')
  WHERE prompt_hash IS NULL;
ALTER TABLE proposals ALTER COLUMN prompt_hash SET NOT NULL;

-- ============================================================
-- review_decisions: escalate outcome
-- ============================================================
ALTER TABLE review_queue DROP CONSTRAINT IF EXISTS review_queue_action_check;
ALTER TABLE review_queue ADD CONSTRAINT review_queue_action_check
  CHECK (action IN ('approve','correct','reject','escalate'));

-- ============================================================
-- related_parties: real seam for is_related_party (empty for the demo)
-- ============================================================
CREATE TABLE IF NOT EXISTS related_parties (
  org_id       uuid NOT NULL REFERENCES orgs(org_id),
  counterparty text NOT NULL,
  note         text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, counterparty)
);
GRANT SELECT, INSERT ON related_parties TO greenledger_app;

-- ============================================================
-- audit_log: hash-chained (append-only tamper evidence, spec §1.3)
-- ============================================================
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS row_hash char(64);

-- Canonical serialization of one audit row given the previous row's hash.
CREATE OR REPLACE FUNCTION audit_canonical(p_prev text, p_org uuid, p_actor text,
                                           p_action text, p_object text, p_detail jsonb)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT p_prev || '|' || p_org::text || '|' || p_actor || '|' || p_action || '|'
         || COALESCE(p_object,'') || '|' || COALESCE(p_detail::text,'');
$$;

-- BEFORE INSERT: chain each row to the previous one for its org. Serialized by
-- the same per-org advisory lock the ledger chain uses (re-entrant within a txn).
CREATE OR REPLACE FUNCTION audit_row_hash() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_prev text;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended(NEW.org_id::text, 0));
  SELECT row_hash INTO v_prev FROM audit_log
   WHERE org_id = NEW.org_id AND row_hash IS NOT NULL ORDER BY log_id DESC LIMIT 1;
  IF v_prev IS NULL THEN v_prev := repeat('0', 64); END IF;
  NEW.row_hash := encode(digest(
    audit_canonical(v_prev, NEW.org_id, NEW.actor, NEW.action, NEW.object, NEW.detail),
    'sha256'), 'hex');
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_audit_hash ON audit_log;
CREATE TRIGGER trg_audit_hash BEFORE INSERT ON audit_log
  FOR EACH ROW EXECUTE FUNCTION audit_row_hash();

ALTER TABLE audit_log ALTER COLUMN row_hash SET NOT NULL;

CREATE OR REPLACE FUNCTION verify_audit_chain(p_org uuid)
RETURNS boolean LANGUAGE plpgsql STABLE AS $$
DECLARE r record; v_prev text := repeat('0',64); v_comp text;
BEGIN
  FOR r IN SELECT org_id, actor, action, object, detail, row_hash
             FROM audit_log WHERE org_id = p_org ORDER BY log_id LOOP
    v_comp := encode(digest(
      audit_canonical(v_prev, r.org_id, r.actor, r.action, r.object, r.detail),
      'sha256'), 'hex');
    IF v_comp IS DISTINCT FROM r.row_hash THEN RETURN false; END IF;
    v_prev := r.row_hash;
  END LOOP;
  RETURN true;
END $$;
