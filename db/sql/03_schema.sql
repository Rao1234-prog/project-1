-- GreenLedger — Phase A schema, part 3: tables, functions, triggers
-- Section 1.2 data model. All amounts are integer minor units (BIGINT cents).
--
-- Invariants enforced HERE (by the schema, not by application code):
--   1. Every journal entry balances (deferred constraint trigger at COMMIT).
--   2. Journals are append-only; corrections are reversal entries. UPDATE/DELETE
--      are revoked from the app role (04_grants.sql). No mutation path exists.
--   3. Every journal line carries a source-document reference (doc_id NOT NULL).
--   4. Entries are hash-chained per org; tampering is detectable (verify_chain).
--   5. Entries cannot post into a closed/locked period (period-lock trigger).
--   6. All amounts are positive integer minor units (BIGINT + CHECK > 0).

-- ---------------------------------------------------------------------------
-- Core tables
-- ---------------------------------------------------------------------------

CREATE TABLE orgs (
  org_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name       text NOT NULL UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE accounts (
  account_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL REFERENCES orgs(org_id),
  code           text NOT NULL,
  name           text NOT NULL,
  account_type   text NOT NULL,
  normal_balance text NOT NULL CHECK (normal_balance IN ('debit','credit')),
  UNIQUE (org_id, code)
);

-- Immutable, content-addressed, deduped source documents.
CREATE TABLE source_documents (
  doc_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        uuid NOT NULL REFERENCES orgs(org_id),
  doc_type      text NOT NULL,
  source_system text NOT NULL,
  raw           text NOT NULL,
  sha256        text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, sha256)                    -- identical content dedupes
);

CREATE TABLE periods (
  org_id     uuid NOT NULL REFERENCES orgs(org_id),
  period_key text NOT NULL CHECK (period_key ~ '^[0-9]{4}-[0-9]{2}$'),
  status     text NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed','locked')),
  closed_by  text,
  closed_at  timestamptz,
  PRIMARY KEY (org_id, period_key)
);

CREATE TABLE journal_entries (
  entry_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL REFERENCES orgs(org_id),
  entry_date       date NOT NULL,
  entry_type       text NOT NULL CHECK (entry_type IN
                     ('standard','accrual','depreciation','reclass','reversal','opening_balance')),
  memo             text NOT NULL,
  posted_by_policy text NOT NULL,
  reverses         uuid REFERENCES journal_entries(entry_id),
  -- filled in at COMMIT by the finalize trigger; never set by the app:
  entry_hash       text,
  prev_hash        text,
  chain_seq        bigint,
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_je_org_date ON journal_entries(org_id, entry_date);
CREATE INDEX idx_je_org_chain ON journal_entries(org_id, chain_seq);

CREATE TABLE journal_lines (
  line_id      bigserial PRIMARY KEY,          -- insertion order within the table
  entry_id     uuid NOT NULL REFERENCES journal_entries(entry_id),
  org_id       uuid NOT NULL REFERENCES orgs(org_id),
  account_id   uuid NOT NULL REFERENCES accounts(account_id),
  side         text   NOT NULL CHECK (side IN ('debit','credit')),
  amount_minor bigint NOT NULL CHECK (amount_minor > 0),   -- integer minor units, positive
  doc_id       uuid   NOT NULL REFERENCES source_documents(doc_id),  -- provenance mandatory
  txn_id       text
);
CREATE INDEX idx_jl_entry ON journal_lines(entry_id);
CREATE INDEX idx_jl_account ON journal_lines(account_id);
CREATE INDEX idx_jl_org ON journal_lines(org_id);

CREATE TABLE audit_log (
  log_id     bigserial PRIMARY KEY,
  org_id     uuid REFERENCES orgs(org_id),
  actor      text NOT NULL,
  action     text NOT NULL,
  object     text,
  detail     jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- The ONLY table the AI role may write to (grants in 04_grants.sql).
CREATE TABLE proposals (
  proposal_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        uuid NOT NULL REFERENCES orgs(org_id),
  txn_id        text NOT NULL,
  account_code  text NOT NULL,
  account_type  text NOT NULL,
  rationale     text NOT NULL,
  confidence    numeric(5,4) NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  pattern_match text NOT NULL CHECK (pattern_match IN ('seen','similar','novel')),
  model_id      text NOT NULL DEFAULT 'greenledger-cat-1',
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Hash chain
-- ---------------------------------------------------------------------------
-- Canonical serialization of one entry, given the previous entry's hash.
-- Used identically by the finalize trigger and by verify_chain(), so the two
-- can never disagree about what a "clean" hash is.
CREATE FUNCTION entry_canonical(p_entry_id uuid, p_prev text)
RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT json_build_object(
           'date',     to_char(e.entry_date, 'YYYY-MM-DD'),
           'type',     e.entry_type,
           'memo',     e.memo,
           'lines',    COALESCE(
                         (SELECT json_agg(
                                   json_build_array(l.account_id::text, l.side,
                                                    l.amount_minor, l.doc_id::text)
                                   ORDER BY l.line_id)
                          FROM journal_lines l WHERE l.entry_id = p_entry_id),
                         '[]'::json),
           'reverses', e.reverses,
           'prev',     p_prev
         )::text
  FROM journal_entries e
  WHERE e.entry_id = p_entry_id;
$$;

-- Deferred constraint trigger: runs at COMMIT, once per inserted entry.
-- Validates balance/non-emptiness, then assigns the per-org hash-chain link.
-- SECURITY DEFINER so it may write entry_hash/prev_hash/chain_seq even though
-- the app role has no UPDATE privilege on the table.
CREATE FUNCTION finalize_entry()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp AS $$
DECLARE
  v_cnt  int;
  v_dr   bigint;
  v_cr   bigint;
  v_prev text;
  v_seq  bigint;
  v_hash text;
BEGIN
  SELECT count(*),
         COALESCE(sum(amount_minor) FILTER (WHERE side = 'debit'), 0),
         COALESCE(sum(amount_minor) FILTER (WHERE side = 'credit'), 0)
    INTO v_cnt, v_dr, v_cr
    FROM journal_lines
   WHERE entry_id = NEW.entry_id;

  IF v_cnt = 0 THEN
    RAISE EXCEPTION 'empty entry %', NEW.entry_id USING ERRCODE = '23514';
  END IF;
  IF v_dr <> v_cr THEN
    RAISE EXCEPTION 'unbalanced entry: dr % != cr %', v_dr, v_cr USING ERRCODE = '23514';
  END IF;

  -- Serialize the chain per org so prev_hash/chain_seq are assigned in a
  -- well-defined order even under concurrent commits.
  PERFORM pg_advisory_xact_lock(hashtextextended(NEW.org_id::text, 0));

  SELECT entry_hash, chain_seq
    INTO v_prev, v_seq
    FROM journal_entries
   WHERE org_id = NEW.org_id AND chain_seq IS NOT NULL
   ORDER BY chain_seq DESC
   LIMIT 1;

  IF v_prev IS NULL THEN
    v_prev := repeat('0', 64);
    v_seq  := 0;
  END IF;

  v_hash := encode(digest(entry_canonical(NEW.entry_id, v_prev), 'sha256'), 'hex');

  UPDATE journal_entries
     SET entry_hash = v_hash, prev_hash = v_prev, chain_seq = v_seq + 1
   WHERE entry_id = NEW.entry_id;

  RETURN NULL;
END
$$;

CREATE CONSTRAINT TRIGGER trg_finalize_entry
  AFTER INSERT ON journal_entries
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION finalize_entry();

-- Recompute the chain and compare to stored hashes. Any direct-SQL tampering
-- with a memo, amount, date, etc. changes the canonical form and is detected.
CREATE FUNCTION verify_chain(p_org uuid)
RETURNS boolean
LANGUAGE plpgsql STABLE AS $$
DECLARE
  r        record;
  v_prev   text := repeat('0', 64);
  v_comp   text;
BEGIN
  FOR r IN
    SELECT entry_id, entry_hash
      FROM journal_entries
     WHERE org_id = p_org AND chain_seq IS NOT NULL
     ORDER BY chain_seq
  LOOP
    v_comp := encode(digest(entry_canonical(r.entry_id, v_prev), 'sha256'), 'hex');
    IF v_comp IS DISTINCT FROM r.entry_hash THEN
      RETURN false;
    END IF;
    v_prev := r.entry_hash;
  END LOOP;
  RETURN true;
END
$$;

-- ---------------------------------------------------------------------------
-- Period lock
-- ---------------------------------------------------------------------------
CREATE FUNCTION check_period_open()
RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_status text;
BEGIN
  SELECT status INTO v_status
    FROM periods
   WHERE org_id = NEW.org_id AND period_key = to_char(NEW.entry_date, 'YYYY-MM');
  IF v_status IN ('closed', 'locked') THEN
    RAISE EXCEPTION 'period % is not open (status=%)',
      to_char(NEW.entry_date, 'YYYY-MM'), v_status USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END
$$;

CREATE TRIGGER trg_period_lock
  BEFORE INSERT ON journal_entries
  FOR EACH ROW EXECUTE FUNCTION check_period_open();

-- ---------------------------------------------------------------------------
-- Read helpers
-- ---------------------------------------------------------------------------
CREATE FUNCTION trial_balance(p_org uuid)
RETURNS bigint
LANGUAGE sql STABLE AS $$
  SELECT COALESCE(sum(CASE WHEN side = 'debit' THEN amount_minor ELSE -amount_minor END), 0)
  FROM journal_lines WHERE org_id = p_org;
$$;

CREATE FUNCTION account_balance(p_org uuid, p_code text, p_asof date DEFAULT NULL)
RETURNS bigint
LANGUAGE sql STABLE AS $$
  SELECT COALESCE(sum(
           CASE WHEN l.side = a.normal_balance THEN l.amount_minor ELSE -l.amount_minor END), 0)
  FROM journal_lines l
  JOIN accounts a         ON a.account_id = l.account_id
  JOIN journal_entries e  ON e.entry_id = l.entry_id
  WHERE l.org_id = p_org AND a.code = p_code
    AND (p_asof IS NULL OR e.entry_date <= p_asof);
$$;
