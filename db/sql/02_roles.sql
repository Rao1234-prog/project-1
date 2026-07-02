-- Bedrock — Phase A schema, part 2: roles
--
-- Three roles form the trust boundary that the rest of the system leans on.
-- Privileges are granted in 04_grants.sql; this file only creates the roles.
--
--   bedrock      -- owner/superuser. Owns the schema and the SECURITY DEFINER
--                   trigger functions. DDL and break-glass admin only.
--   bedrock_app  -- the FastAPI service layer connects as this. Append-only on
--                   the journal tables (no UPDATE/DELETE), read-only on proposals.
--   bedrock_ai   -- the categorizer connects as this. It may write ONLY to the
--                   proposals table. It has no write path to the ledger at all.
--
-- Passwords are supplied as psql variables :app_pw and :ai_pw so they never live
-- in source. (Under local trust auth they are irrelevant but still set.)

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bedrock_app') THEN
    CREATE ROLE bedrock_app LOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bedrock_ai') THEN
    CREATE ROLE bedrock_ai LOGIN;
  END IF;
END
$$;

ALTER ROLE bedrock_app PASSWORD :'app_pw';
ALTER ROLE bedrock_ai  PASSWORD :'ai_pw';
