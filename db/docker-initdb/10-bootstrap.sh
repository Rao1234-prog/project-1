#!/usr/bin/env bash
# Runs once, inside the postgres container, on first initialization.
# The official image has already created POSTGRES_USER / POSTGRES_DB and started
# a local server; we just apply the Bedrock schema against it.
set -euo pipefail
export PGUSER="${POSTGRES_USER:-bedrock}"
export PGDATABASE="${POSTGRES_DB:-bedrock}"
exec /bedrock-db/apply.sh
