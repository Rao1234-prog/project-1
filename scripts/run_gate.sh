#!/usr/bin/env bash
# Phase A gate runner.
#
# Brings up Postgres (docker-compose if the daemon+registry are reachable,
# otherwise the local postgres-16 cluster), applies the schema, exports the
# connection URLs, runs the pytest suite, and prints the gate summary.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

USE_DOCKER=0
if docker compose version >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  # Only use docker if the postgres image is actually pullable/available.
  if docker image inspect postgres:16-alpine >/dev/null 2>&1 || \
     docker pull postgres:16-alpine >/dev/null 2>&1; then
    USE_DOCKER=1
  fi
fi

if [ "${USE_DOCKER}" = "1" ]; then
  echo "== bringing up Postgres via docker compose =="
  docker compose up -d db
  # wait for health
  for _ in $(seq 1 30); do
    if docker compose exec -T db pg_isready -U bedrock -d bedrock >/dev/null 2>&1; then break; fi
    sleep 1
  done
  PORT="${BEDROCK_DB_PORT:-5432}"
  export BEDROCK_ADMIN_URL="postgresql://bedrock:${POSTGRES_PASSWORD:-bedrock}@127.0.0.1:${PORT}/bedrock"
  export BEDROCK_DATABASE_URL="postgresql://bedrock_app:${BEDROCK_APP_PASSWORD:-app_secret}@127.0.0.1:${PORT}/bedrock"
  export BEDROCK_AI_URL="postgresql://bedrock_ai:${BEDROCK_AI_PASSWORD:-ai_secret}@127.0.0.1:${PORT}/bedrock"
else
  echo "== docker registry unavailable; using local postgres-16 cluster (clean reset) =="
  bash scripts/pg_local.sh reset >/dev/null
  eval "$(bash scripts/pg_local.sh env)"
fi

echo "  BEDROCK_DATABASE_URL=${BEDROCK_DATABASE_URL}"
echo
echo "== running pytest =="
cd "${ROOT}/api"
python3 -m pytest "$@"
rc=$?

echo
echo "== gate summary =="
python3 - <<'PY'
import os
from datetime import date
from bedrock.service import LedgerService, LineInput

s = LedgerService(dsn=os.environ["BEDROCK_DATABASE_URL"])
org = s.ensure_org("GATE Cardinal Heating & Air LLC")
# fresh COA (ignore duplicates from a prior run)
COA = [("1000","Operating Checking","asset","debit"),("3000","Owner's Equity","equity","credit"),
       ("6100","Fuel & Vehicle","expense","debit"),("6600","Meals","expense","debit")]
for c,n,t,nb in COA:
    try: s.add_account(org,c,n,t,nb)
    except Exception: pass

d0 = s.ingest_document(org,"opening_balance","manual","GATE OB checking 84,213.55 equity")
s.post(org, date(2026,3,31),"opening_balance","Opening balances",
       [LineInput("1000","debit",8_421_355,d0), LineInput("3000","credit",8_421_355,d0)],"migration")
dA = s.ingest_document(org,"receipt","email_ingest","GATE Shell 04/02 61.20")
bad = s.post(org, date(2026,4,2),"standard","Fuel misposted to Meals",
       [LineInput("6600","debit",6120,dA), LineInput("1000","credit",6120,dA)],"bookkeeper")
s.reverse(org, bad["entry_id"], date(2026,4,2),"Reverse mispost","bookkeeper")
s.post(org, date(2026,4,2),"standard","Fuel — Shell (corrected)",
       [LineInput("6100","debit",6120,dA), LineInput("1000","credit",6120,dA)],"bookkeeper")

print(f"  trial balance after seeded run : {s.trial_balance(org)}  (must be 0)")
print(f"  chain verification             : {s.verify_chain(org)}")

# direct SQL tampering is detected
import psycopg
victim = None
with psycopg.connect(os.environ["BEDROCK_ADMIN_URL"], autocommit=True) as c:
    victim = c.execute("SELECT entry_id, memo FROM journal_entries WHERE org_id=%s ORDER BY chain_seq LIMIT 1",(org,)).fetchone()
    c.execute("UPDATE journal_entries SET memo='TAMPERED' WHERE entry_id=%s",(victim[0],))
detected = (s.verify_chain(org) is False)
with psycopg.connect(os.environ["BEDROCK_ADMIN_URL"], autocommit=True) as c:
    c.execute("UPDATE journal_entries SET memo=%s WHERE entry_id=%s",(victim[1], victim[0]))
print(f"  direct SQL tampering detected   : {detected}")
print(f"  chain re-verifies after restore : {s.verify_chain(org)}")
s.pool.close()
PY

exit $rc
