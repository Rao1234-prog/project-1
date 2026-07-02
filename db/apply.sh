#!/usr/bin/env bash
# Apply the ordered Bedrock schema files. Single source of truth used by BOTH
# docker-compose init and the local-cluster runner, so the two can't drift.
#
# Connection: standard libpq env vars (PGHOST/PGPORT/PGUSER/PGDATABASE).
# Role passwords: BEDROCK_APP_PASSWORD / BEDROCK_AI_PASSWORD (defaults for dev).
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/sql"
APP_PW="${BEDROCK_APP_PASSWORD:-app_secret}"
AI_PW="${BEDROCK_AI_PASSWORD:-ai_secret}"

PSQL=(psql -v ON_ERROR_STOP=1 -q -v "app_pw=${APP_PW}" -v "ai_pw=${AI_PW}")

# Apply every db/sql/*.sql in lexical order (01_, 02_, ... 05_phase_b, ...).
shopt -s nullglob
for f in "${DIR}"/*.sql; do
  echo "  applying $(basename "${f}")"
  "${PSQL[@]}" -f "${f}"
done
echo "  schema applied."
