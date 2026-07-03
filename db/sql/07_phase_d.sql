-- GreenLedger — Phase D schema: replayable categorization provenance.
--
-- Every proposal records which layer produced it, the prompt template version,
-- a hash of the exact prompt, the raw model response, and the source-document
-- content hash (so an identical document reuses the cached proposal instead of
-- making a second API call).
ALTER TABLE proposals
  ADD COLUMN IF NOT EXISTS source_layer            text,   -- pattern_exact | pattern_fuzzy | llm | failed
  ADD COLUMN IF NOT EXISTS prompt_template_version text,
  ADD COLUMN IF NOT EXISTS prompt_hash             text,
  ADD COLUMN IF NOT EXISTS raw_response            text,
  ADD COLUMN IF NOT EXISTS content_sha256          text;

-- Cache lookup: same content hash -> reuse the existing proposal.
CREATE INDEX IF NOT EXISTS idx_proposals_content ON proposals(org_id, content_sha256);

-- Table-level grants from 04/05 already cover the new columns:
--   greenledger_ai  : INSERT, SELECT on proposals   (writes proposals only)
--   greenledger_app : SELECT on proposals           (reads, never writes)
