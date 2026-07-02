#!/usr/bin/env bash
# Local Postgres 16 runner — a drop-in for `docker compose up db` in environments
# where the Docker registry is unreachable. Same engine (postgres 16), same SQL
# (db/apply.sh), same roles. Emits the same connection URLs the API expects.
#
#   scripts/pg_local.sh up      # initdb (once), start, apply schema
#   scripts/pg_local.sh down    # stop the server
#   scripts/pg_local.sh env     # print export lines for the connection URLs
#   scripts/pg_local.sh psql    # open a superuser psql shell
#
# State lives under .pglocal/ (gitignored). Data dir is owned by the unprivileged
# `postgres` OS user because the server refuses to run as root.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PGBIN=/usr/lib/postgresql/16/bin
STATE="${ROOT}/.pglocal"
DATA="${STATE}/data"
SOCK="${STATE}/sock"
LOG="${STATE}/server.log"
PORT="${BEDROCK_DB_PORT:-55432}"
HOST=127.0.0.1
PGUSER_SUPER=bedrock
PGDB=bedrock
APP_PW="${BEDROCK_APP_PASSWORD:-app_secret}"
AI_PW="${BEDROCK_AI_PASSWORD:-ai_secret}"

# Run a command as the unprivileged postgres user (server won't run as root).
as_pg() { runuser -u postgres -- "$@"; }

emit_env() {
  echo "export BEDROCK_ADMIN_URL='postgresql://${PGUSER_SUPER}@${HOST}:${PORT}/${PGDB}'"
  echo "export BEDROCK_DATABASE_URL='postgresql://bedrock_app@${HOST}:${PORT}/${PGDB}'"
  echo "export BEDROCK_AI_URL='postgresql://bedrock_ai@${HOST}:${PORT}/${PGDB}'"
}

cmd_up() {
  mkdir -p "${STATE}" "${SOCK}"
  chown -R postgres:postgres "${STATE}"

  if [ ! -s "${DATA}/PG_VERSION" ]; then
    echo "initdb -> ${DATA}"
    as_pg "${PGBIN}/initdb" -D "${DATA}" -U "${PGUSER_SUPER}" --auth=trust -E UTF8 >/dev/null
    # trust auth on localhost + socket is fine for a dev/CI cluster; privilege
    # separation is still fully enforced by the role GRANTs.
    echo "host all all 127.0.0.1/32 trust" >> "${DATA}/pg_hba.conf"
  fi

  if as_pg "${PGBIN}/pg_ctl" -D "${DATA}" status >/dev/null 2>&1; then
    echo "server already running on :${PORT}"
  else
    echo "starting postgres on :${PORT}"
    as_pg "${PGBIN}/pg_ctl" -D "${DATA}" -l "${LOG}" \
      -o "-p ${PORT} -k ${SOCK} -c listen_addresses=${HOST}" -w start
  fi

  # Ensure the application database exists (idempotent).
  if ! as_pg "${PGBIN}/psql" -h "${SOCK}" -p "${PORT}" -U "${PGUSER_SUPER}" -d postgres \
        -tAc "SELECT 1 FROM pg_database WHERE datname='${PGDB}'" | grep -q 1; then
    as_pg "${PGBIN}/createdb" -h "${SOCK}" -p "${PORT}" -U "${PGUSER_SUPER}" "${PGDB}"
  fi

  # Apply the schema only if it hasn't been applied yet (the SQL files assume a
  # fresh database, exactly as the docker init-once flow does). Use `reset` for
  # a clean slate.
  if as_pg "${PGBIN}/psql" -h "${SOCK}" -p "${PORT}" -U "${PGUSER_SUPER}" -d "${PGDB}" \
        -tAc "SELECT to_regclass('public.orgs')" | grep -q orgs; then
    echo "schema already applied (use 'reset' to rebuild)"
  else
    apply_schema
  fi

  echo "ready."
  emit_env
}

apply_schema() {
  echo "applying schema"
  PGHOST="${SOCK}" PGPORT="${PORT}" PGUSER="${PGUSER_SUPER}" PGDATABASE="${PGDB}" \
    BEDROCK_APP_PASSWORD="${APP_PW}" BEDROCK_AI_PASSWORD="${AI_PW}" \
    runuser -u postgres --preserve-environment -- \
    env PATH="${PGBIN}:${PATH}" "${ROOT}/db/apply.sh"
}

cmd_reset() {
  cmd_up >/dev/null 2>&1 || true   # ensure server is running
  echo "dropping and recreating database ${PGDB}"
  as_pg "${PGBIN}/dropdb" -h "${SOCK}" -p "${PORT}" -U "${PGUSER_SUPER}" --force --if-exists "${PGDB}"
  as_pg "${PGBIN}/createdb" -h "${SOCK}" -p "${PORT}" -U "${PGUSER_SUPER}" "${PGDB}"
  apply_schema
  echo "reset complete."
  emit_env
}

cmd_down() {
  if as_pg "${PGBIN}/pg_ctl" -D "${DATA}" status >/dev/null 2>&1; then
    as_pg "${PGBIN}/pg_ctl" -D "${DATA}" -w -m fast stop
  else
    echo "not running"
  fi
}

case "${1:-up}" in
  up)    cmd_up ;;
  reset) cmd_reset ;;
  down)  cmd_down ;;
  env)  emit_env ;;
  psql) as_pg "${PGBIN}/psql" -h "${SOCK}" -p "${PORT}" -U "${PGUSER_SUPER}" -d "${PGDB}" ;;
  *) echo "usage: $0 {up|down|env|psql}" >&2; exit 1 ;;
esac
