-- GreenLedger — Phase A schema, part 1: extensions
-- pgcrypto provides digest() (sha256 for the hash chain) and gen_random_uuid().
CREATE EXTENSION IF NOT EXISTS pgcrypto;
