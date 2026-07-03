-- GreenLedger — Phase B schema: policy engine state, decision provenance, review.
--
-- All policy state that must survive restarts lives here (never in process
-- memory): per-counterparty daily cumulative totals, monthly auto-post share,
-- per-account error rates, and the QA sampling of auto-posts.

-- ---------------------------------------------------------------------------
-- Normalized transactions (idempotent by source-document content hash)
-- ---------------------------------------------------------------------------
CREATE TABLE transactions (
  txn_id       text NOT NULL,
  org_id       uuid NOT NULL REFERENCES orgs(org_id),
  doc_id       uuid NOT NULL REFERENCES source_documents(doc_id),
  content_sha256 text NOT NULL,          -- = source document content hash
  txn_date     date NOT NULL,
  amount_minor bigint NOT NULL CHECK (amount_minor > 0),
  counterparty text NOT NULL,
  description  text NOT NULL,
  direction    text NOT NULL CHECK (direction IN ('inflow','outflow')),
  fraud_flags  text[] NOT NULL DEFAULT '{}',
  created_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, txn_id),
  -- Re-pulled bank feeds resend identical lines; the same content dedupes to a
  -- single transaction (and therefore a single routing decision).
  UNIQUE (org_id, content_sha256)
);

-- ---------------------------------------------------------------------------
-- Routing decisions — full provenance, rendered verbatim by the paper trail.
-- ---------------------------------------------------------------------------
CREATE TABLE routing_decisions (
  decision_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id               uuid NOT NULL REFERENCES orgs(org_id),
  txn_id               text NOT NULL,
  proposal_id          uuid NOT NULL REFERENCES proposals(proposal_id),
  decision             text NOT NULL CHECK (decision IN
                         ('auto_post','bookkeeper_queue','bookkeeper_queue_lowconf',
                          'controller_queue','hard_stop')),
  reason               text NOT NULL,
  policy_version       text NOT NULL,
  effective_thresholds jsonb NOT NULL,     -- the thresholds AT decision time
  qa_sampled           boolean NOT NULL DEFAULT false,
  -- lifecycle (a decision is not a journal, so UPDATE here is legitimate):
  status               text NOT NULL DEFAULT 'open' CHECK (status IN
                         ('auto_posted','queued','resolved','rejected')),
  posted_entry_id      uuid REFERENCES journal_entries(entry_id),
  created_at           timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (org_id, txn_id) REFERENCES transactions(org_id, txn_id),
  UNIQUE (org_id, txn_id)                   -- one decision per transaction
);
CREATE INDEX idx_rd_org_status ON routing_decisions(org_id, status);

-- ---------------------------------------------------------------------------
-- Review queue — one row per item that needs (or received QA) human review.
-- ---------------------------------------------------------------------------
CREATE TABLE review_queue (
  queue_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        uuid NOT NULL REFERENCES orgs(org_id),
  txn_id        text NOT NULL,
  decision_id   uuid NOT NULL REFERENCES routing_decisions(decision_id),
  lane          text NOT NULL CHECK (lane IN
                  ('bookkeeper_queue','bookkeeper_queue_lowconf',
                   'controller_queue','hard_stop','qa_sample')),
  status        text NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved')),
  action        text CHECK (action IN ('approve','correct','reject')),
  reviewer_id   text,
  reviewer_role text,
  corrected_account_code text,
  resolved_at   timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (org_id, txn_id) REFERENCES transactions(org_id, txn_id)
);
CREATE INDEX idx_rq_org_lane_status ON review_queue(org_id, lane, status);

-- ---------------------------------------------------------------------------
-- Persistent policy state
-- ---------------------------------------------------------------------------
-- Per-account correction tally. error_rate = corrected/decided once decided>=10.
CREATE TABLE account_error_rates (
  org_id       uuid NOT NULL REFERENCES orgs(org_id),
  account_code text NOT NULL,
  corrected    integer NOT NULL DEFAULT 0,
  decided      integer NOT NULL DEFAULT 0,
  PRIMARY KEY (org_id, account_code)
);

-- Monthly auto-post share: [auto_count, total_count] per org-month.
CREATE TABLE policy_month_counts (
  org_id      uuid NOT NULL REFERENCES orgs(org_id),
  month       text NOT NULL CHECK (month ~ '^[0-9]{4}-[0-9]{2}$'),
  auto_count  integer NOT NULL DEFAULT 0,
  total_count integer NOT NULL DEFAULT 0,
  PRIMARY KEY (org_id, month)
);

-- Daily per-counterparty cumulative outflow, in minor units.
CREATE TABLE policy_daily_cum (
  org_id       uuid NOT NULL REFERENCES orgs(org_id),
  day          date NOT NULL,
  counterparty text NOT NULL,
  cum_minor    bigint NOT NULL DEFAULT 0,
  PRIMARY KEY (org_id, day, counterparty)
);

-- ---------------------------------------------------------------------------
-- Grants (the trust boundary continues to be enforced by privilege)
-- ---------------------------------------------------------------------------
-- The service role reads/writes policy state and the queue, but proposals stay
-- write-only-by-AI: greenledger_app still has SELECT on proposals only.
GRANT SELECT, INSERT ON transactions                         TO greenledger_app;
GRANT SELECT, INSERT, UPDATE ON routing_decisions            TO greenledger_app;
GRANT SELECT, INSERT, UPDATE ON review_queue                 TO greenledger_app;
GRANT SELECT, INSERT, UPDATE ON account_error_rates          TO greenledger_app;
GRANT SELECT, INSERT, UPDATE ON policy_month_counts          TO greenledger_app;
GRANT SELECT, INSERT, UPDATE ON policy_daily_cum             TO greenledger_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public        TO greenledger_app;

-- The AI role gains read-only visibility of transactions (to categorize) but
-- STILL may write only proposals — no grant here changes that.
GRANT SELECT ON transactions                                 TO greenledger_ai;
