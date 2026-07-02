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

# Phase B summary is only meaningful when the AI role URL is available.
if [ -n "${BEDROCK_AI_URL:-}" ]; then
echo
echo "== Phase B gate summary (Phase 1 simulation invariants) =="
python3 - <<'PY'
import os, uuid
from bedrock.policy_service import PolicyService
from bedrock.phase1_sim import load_coa, simulate
from bedrock.policy import BASE

p = PolicyService(app_dsn=os.environ["BEDROCK_DATABASE_URL"], ai_url=os.environ["BEDROCK_AI_URL"])
org = p.ledger.ensure_org(f"GATE-B-{uuid.uuid4()}")
load_coa(p.ledger, org)
st = simulate(p, org, seed=42)
r = st["routed"]

hard_ok = all(d == "hard_stop" for a,b,d,amt,pat in st["audit"] if amt >= BASE["hard_stop_amount"])
novel_ok = all(d != "auto_post" for a,b,d,amt,pat in st["audit"] if pat == "novel")

print(f"  transactions processed          : {st['total']}")
print(f"  routing  auto={r['auto_post']} bk={r['bookkeeper_queue']+r['bookkeeper_queue_lowconf']} "
      f"ctrl={r['controller_queue']} hard={r['hard_stop']}")
print(f"  wrong categorizations auto-posted: {st['auto_wrong']}   (must be 0)")
print(f"  every >= $10,000 hard-stopped    : {hard_ok}")
print(f"  every novel pattern queued       : {novel_ok}")
print(f"  auto-post share                  : {st['auto_share']:.1%}   (cap {BASE['monthly_auto_post_share_cap']:.0%})")
print(f"  trial balance                    : {st['trial_balance']}   (must be 0)")
print(f"  chain verified                   : {st['chain_verified']}")
p.close()
PY
fi

exit $rc
