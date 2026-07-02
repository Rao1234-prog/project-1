-- Bedrock — Phase A schema, part 4: grants (the enforced trust boundary)
--
-- The constraints "no mutation paths on posted entries" and "the AI writes only
-- to the proposals table" are enforced HERE, by privilege, not by application
-- code. Even a compromised service or a mis-wired categorizer cannot violate
-- them because the database refuses the statement.

-- Start from nothing.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO bedrock_app, bedrock_ai;

-- --- bedrock_app: the service layer -----------------------------------------
-- Append-only on the journal + reference tables.
GRANT SELECT, INSERT ON accounts, source_documents, journal_entries,
                        journal_lines, audit_log, orgs                TO bedrock_app;
-- Periods can be opened and transitioned (open -> closed -> locked).
GRANT SELECT, INSERT, UPDATE ON periods                              TO bedrock_app;
-- The app READS AI proposals but may never write them.
GRANT SELECT ON proposals                                            TO bedrock_app;
-- Sequences (journal_lines.line_id, audit_log.log_id, ...).
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public               TO bedrock_app;

-- Belt-and-suspenders: journals are append-only. Posted entries and their
-- lines can never be updated or deleted through this role.
REVOKE UPDATE, DELETE ON journal_entries FROM bedrock_app;
REVOKE UPDATE, DELETE ON journal_lines   FROM bedrock_app;

-- --- bedrock_ai: the categorizer --------------------------------------------
-- May ONLY write proposals. No grant of any kind on the ledger tables, so the
-- AI has no path to post, alter, or delete a journal entry.
GRANT SELECT, INSERT ON proposals                                   TO bedrock_ai;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public               TO bedrock_ai;
-- Read-only context so it can categorize (chart of accounts + the raw docs).
GRANT SELECT ON accounts, source_documents                          TO bedrock_ai;

-- The SECURITY DEFINER trigger function (finalize_entry) is owned by the
-- superuser, so it retains the UPDATE privilege the app role has been denied —
-- that is how entry_hash/prev_hash/chain_seq get written despite the REVOKE.
