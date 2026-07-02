"""
Runs Phases 0-3 end to end on a seeded messy pilot business
(Cardinal Heating & Air — pilot archetype #1) and prints the phase-gate report.
"""
import random
from datetime import date
from ledger import Ledger, JournalLine, LedgerError
from policy import (PolicyEngine, Proposal, Txn, adversarial_check,
                    AUTO_POST, BK_QUEUE, BK_QUEUE_LOWCONF, CTRL_QUEUE, HARD_STOP)

random.seed(42)
ok = lambda m: print(f"  PASS  {m}")

# ============================================================
# PHASE 0 — engine invariants
# ============================================================
print("\n=== PHASE 0: LEDGER ENGINE INVARIANTS ===")
L = Ledger("Cardinal Heating & Air LLC")
COA = [("1000","Operating Checking","asset","debit"), ("1200","Accounts Receivable","asset","debit"),
       ("1500","Equipment","asset","debit"), ("1510","Accum. Depreciation","contra","credit"),
       ("2000","Accounts Payable","liability","credit"), ("2100","Payroll Liabilities","liability","credit"),
       ("2200","Sales Tax Payable","tax","credit"), ("3000","Owner's Equity","equity","credit"),
       ("4000","Service Revenue","revenue","credit"), ("4100","Install Revenue","revenue","credit"),
       ("5000","Parts & Materials COGS","expense","debit"), ("5100","Subcontractor COGS","expense","debit"),
       ("6000","Payroll Expense","expense","debit"), ("6100","Fuel & Vehicle","expense","debit"),
       ("6200","Software & Office","expense","debit"), ("6300","Rent","expense","debit"),
       ("6400","Insurance","expense","debit"), ("6500","Depreciation Expense","expense","debit"),
       ("6600","Meals","expense","debit")]
acct = {c: L.add_account(c,n,t,nb) for c,n,t,nb in COA}

d0 = L.ingest_document("opening_balance","manual","OB 2026-03-31 checking 84,213.55 equity")
L.post(date(2026,3,31), "opening_balance", "Opening balances",
       [JournalLine(acct["1000"].account_id,"debit",8_421_355,d0.doc_id),
        JournalLine(acct["3000"].account_id,"credit",8_421_355,d0.doc_id)], "migration")

# 1. unbalanced entry must be rejected
try:
    L.post(date(2026,4,1),"standard","bad",
           [JournalLine(acct["6100"].account_id,"debit",5000,d0.doc_id),
            JournalLine(acct["1000"].account_id,"credit",4999,d0.doc_id)],"test")
    raise SystemExit("FAIL: unbalanced entry accepted")
except LedgerError: ok("unbalanced entry rejected by engine")

# 2. provenance is mandatory
try:
    JournalLine(acct["6100"].account_id,"debit",5000,"")
    raise SystemExit("FAIL: line without doc accepted")
except LedgerError: ok("journal line without source document rejected")

# 3. floats rejected
try:
    JournalLine(acct["6100"].account_id,"debit",49.99,d0.doc_id)  # type: ignore
    raise SystemExit("FAIL: float amount accepted")
except LedgerError: ok("non-integer amount rejected (integer minor units only)")

# 4. duplicate document dedupe
dA = L.ingest_document("receipt","email_ingest","Shell 04/02 61.20 card *4411")
dB = L.ingest_document("receipt","upload","Shell 04/02 61.20 card *4411")
assert dA.doc_id == dB.doc_id; ok("identical document content-hash deduped")

# 5. reversal-only correction + immutability
e_bad = L.post(date(2026,4,2),"standard","Fuel misposted to Meals",
    [JournalLine(acct["6600"].account_id,"debit",6120,dA.doc_id),
     JournalLine(acct["1000"].account_id,"credit",6120,dA.doc_id)],"bookkeeper")
L.reverse(e_bad.entry_id, date(2026,4,2), "Reverse mispost", "bookkeeper")
L.post(date(2026,4,2),"standard","Fuel — Shell (corrected)",
    [JournalLine(acct["6100"].account_id,"debit",6120,dA.doc_id),
     JournalLine(acct["1000"].account_id,"credit",6120,dA.doc_id)],"bookkeeper")
assert L.balance("6600")==0 and L.balance("6100")==6120
ok("correction via reversal chain; net balances correct; original entry untouched")

# 6. hash chain detects tampering
assert L.verify_chain(); ok("hash chain verifies clean")
victim = L.entries[1]; orig_memo = victim.memo
object.__setattr__(victim, "memo", "tampered")
assert not L.verify_chain(); ok("tampered history detected by hash chain")
object.__setattr__(victim, "memo", orig_memo)  # restore canonical value used at hash time
assert L.verify_chain()

# 7. period locking
L.close_period("2026-04","controller.demo")
try:
    L.post(date(2026,4,30),"standard","late",
           [JournalLine(acct["6100"].account_id,"debit",100,dA.doc_id),
            JournalLine(acct["1000"].account_id,"credit",100,dA.doc_id)],"test")
    raise SystemExit("FAIL: posted into closed period")
except LedgerError: ok("posting into closed period rejected")
assert L.trial_balance()==0; ok(f"trial balance nets to zero across {len(L.entries)} entries")

# ============================================================
# PHASE 0 exit criterion + PHASE 1 — full pipeline on messy data
# ============================================================
print("\n=== PHASE 1: CATEGORIZATION PIPELINE ON SEEDED MESSY DATA (May+Jun 2026) ===")
L2 = Ledger("Cardinal Heating & Air LLC")
acct = {c: L2.add_account(c,n,t,nb) for c,n,t,nb in COA}
d0 = L2.ingest_document("opening_balance","manual","OB 2026-04-30 checking 91,882.10 equity")
L2.post(date(2026,4,30),"opening_balance","Opening balances",
        [JournalLine(acct["1000"].account_id,"debit",9_188_210,d0.doc_id),
         JournalLine(acct["3000"].account_id,"credit",9_188_210,d0.doc_id)],"migration")

VENDORS = [  # (name, truth_code, seen?, base_conf, lo, hi)
    ("Shell Fuel","6100","seen",0.99,2500,9500), ("QuickFuel Fleet","6100","seen",0.98,8000,22000),
    ("Ferguson Supply","5000","seen",0.985,4500,48000), ("Johnstone Supply","5000","seen",0.975,3000,42000),
    ("Gusto Payroll","6000","seen",0.995,180000,240000), ("State Farm","6400","seen",0.99,61000,61000),
    ("ServiceTitan","6200","seen",0.99,39900,39900), ("Verizon","6200","seen",0.97,18000,21000),
    ("Chick-fil-A","6600","seen",0.96,1400,6200), ("NEW: Rodriguez Duct LLC","5100","novel",0.72,60000,180000),
    ("NEW: TX Comptroller","2200","novel",0.65,90000,140000), ("Crane Rental Co","5000","similar",0.83,25000,95000),
]
CUSTOMERS = [("Residential service call","4000",18000,65000),("Commercial install draw","4100",250000,1450000)]

pe = PolicyEngine()
L2.recon_required = ["1000"]; L2.reconciled = set()
routed = {AUTO_POST:0,BK_QUEUE:0,BK_QUEUE_LOWCONF:0,CTRL_QUEUE:0,HARD_STOP:0}
audit_pairs = []   # (ai_code, controller_truth_code)
queue_sizes = 0; txn_n = 0

def make_txn(day, name, amount, direction):
    global txn_n; txn_n += 1
    raw = f"{day} {name} {amount/100:.2f} {direction} #{txn_n}"
    doc = L2.ingest_document("bank_feed_line","plaid",raw)
    return Txn(f"t{txn_n:04d}", day, amount, name, raw, direction, doc.doc_id)

for month, days in [("2026-05",31),("2026-06",30)]:
    for _ in range(120):
        name,truth,pat,conf,lo,hi = random.choice(VENDORS)
        day = f"{month}-{random.randint(1,days):02d}"
        amount = random.randint(lo,hi)
        t = make_txn(day,name,amount,"outflow")
        # AI proposal: right ~96% of the time on seen, ~85% similar, ~70% novel
        p_right = {"seen":0.965,"similar":0.85,"novel":0.70}[pat]
        ai_code = truth if random.random() < p_right else random.choice(["5000","6100","6200","6600"])
        c = min(0.999, max(0.30, random.gauss(conf if ai_code==truth else conf-0.25, 0.02)))
        prop = Proposal(t.txn_id, ai_code, L2.by_code(ai_code).account_type,
                        f"Matched pattern for {name}", round(c,3), pat)
        decision, why = pe.route(t, prop)
        routed[decision]+=1
        final = ai_code
        if decision == AUTO_POST:
            pass  # posts as proposed; 5% sampled to QA below
        else:
            queue_sizes += 1
            final = truth  # human review corrects to truth
            pe.record_review(ai_code, corrected=(ai_code!=truth))
        audit_pairs.append((ai_code, truth, decision))
        if decision != HARD_STOP:
            L2.post(date(*map(int,t.day.split("-"))),"standard",f"{name}",
                [JournalLine(acct[final].account_id,"debit",amount,t.doc_id),
                 JournalLine(acct["1000"].account_id,"credit",amount,t.doc_id)],
                f"policy:{decision}")
    for _ in range(26):
        desc,code,lo,hi = random.choice(CUSTOMERS)
        day=f"{month}-{random.randint(1,days):02d}"; amount=random.randint(lo,hi)
        t = make_txn(day,desc,amount,"inflow")
        prop = Proposal(t.txn_id, code, "revenue", desc, 0.985, "seen")
        decision,_ = pe.route(t,prop); routed[decision]+=1
        if decision != HARD_STOP:
            L2.post(date(*map(int,t.day.split("-"))),"standard",desc,
                [JournalLine(acct["1000"].account_id,"debit",amount,t.doc_id),
                 JournalLine(acct[code].account_id,"credit",amount,t.doc_id)],
                f"policy:{decision}")
        else:
            # controller reviews large deposits, then posts
            L2.post(date(*map(int,t.day.split("-"))),"standard",desc+" (controller approved)",
                [JournalLine(acct["1000"].account_id,"debit",amount,t.doc_id),
                 JournalLine(acct[code].account_id,"credit",amount,t.doc_id)],
                "human:controller")

total = sum(routed.values())
auto_share = routed[AUTO_POST]/total
# categorization accuracy on FIRST PASS (AI proposal vs controller truth)
first_pass_agree = sum(1 for a,b,_ in audit_pairs if a==b)/len(audit_pairs)
# accuracy of what actually reached the ledger unreviewed (auto-posted only)
auto_wrong = sum(1 for a,b,d in audit_pairs if d==AUTO_POST and a!=b)

print(f"  transactions processed: {total}")
print(f"  routing: auto {routed[AUTO_POST]} | bookkeeper {routed[BK_QUEUE]+routed[BK_QUEUE_LOWCONF]}"
      f" | controller {routed[CTRL_QUEUE]} | hard-stop {routed[HARD_STOP]}")
print(f"  auto-post share: {auto_share:.1%}  (cap 90%)")
print(f"  AI first-pass agreement with controller truth: {first_pass_agree:.1%}  (gate: >95%)")
print(f"  wrong categorizations that auto-posted unreviewed: {auto_wrong}")
print(f"  trial balance: {L2.trial_balance()} (must be 0)")
assert L2.trial_balance()==0 and L2.verify_chain()
ok("pipeline ledger balanced + chain verified")

# ============================================================
# PHASE 2 — close cycle with adversarial pass
# ============================================================
print("\n=== PHASE 2: MAY 2026 CLOSE (accruals, recon, adversarial check) ===")
d_dep = L2.ingest_document("schedule","system","Truck fleet depreciation schedule May-2026 3 trucks SL 60mo")
L2.post(date(2026,5,31),"depreciation","Fleet depreciation — May",
    [JournalLine(acct["6500"].account_id,"debit",145000,d_dep.doc_id),
     JournalLine(acct["1510"].account_id,"credit",145000,d_dep.doc_id)],"ai_draft:controller_approved")
d_acc = L2.ingest_document("invoice","email_ingest","Rodriguez Duct LLC inv#88 May work 4,250.00 net30")
L2.post(date(2026,5,31),"accrual","Accrue subcontractor inv#88 (unpaid at close)",
    [JournalLine(acct["5100"].account_id,"debit",425000,d_acc.doc_id),
     JournalLine(acct["2000"].account_id,"credit",425000,d_acc.doc_id)],"ai_draft:controller_approved")

f1 = adversarial_check(L2,"2026-05")
print(f"  adversarial pass (pre-reconciliation): {len(f1)} finding(s)")
for f in f1: print(f"    [{f['severity']}] {f['check']}: {f['detail']}  blocking={f['blocking']}")
blocking = [f for f in f1 if f["blocking"]]
assert blocking, "expected the unreconciled-bank block"
ok("close blocked until bank reconciliation approved (as designed)")

d_stmt = L2.ingest_document("statement","plaid","Chase stmt May-2026 ending per bank")
L2.reconciled = {"1000"}
f2 = adversarial_check(L2,"2026-05")
assert not [f for f in f2 if f["blocking"]]
L2.close_period("2026-05","controller.demo")
ok(f"May close approved by controller; period locked; residual findings: {len(f2)} non-blocking")

# ============================================================
# PHASE 3 — CFO layer (advisory only, on closed data)
# ============================================================
print("\n=== PHASE 3: CFO LAYER (advisory output) ===")
rev = L2.balance("4000")+L2.balance("4100")
cogs = L2.balance("5000")+L2.balance("5100")
opex = sum(L2.balance(c) for c in ["6000","6100","6200","6300","6400","6500","6600"])
cash = L2.balance("1000")
print(f"  Revenue (May+Jun): ${rev/100:,.0f}   COGS: ${cogs/100:,.0f}   Gross margin: {(rev-cogs)/rev:.1%}")
print(f"  Opex: ${opex/100:,.0f}   Net income: ${(rev-cogs-opex)/100:,.0f}   Cash: ${cash/100:,.0f}")
wk = (rev-cogs-opex)/8.7/100
print(f"  13-week cash forecast (naive run-rate): week13 ≈ ${cash/100 + 13*wk:,.0f}")
print(f"  Advisory flag: subcontractor COGS introduced in May (Rodriguez Duct) — monitor margin mix.")
print(f"\n  audit log entries: {len(L2.audit_log)} | documents: {len(L2.documents)} | entries: {len(L2.entries)}")
print("\nALL PHASE GATES EXERCISED.")
