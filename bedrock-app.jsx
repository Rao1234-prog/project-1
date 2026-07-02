import React, { useMemo, useState } from "react";
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip,
  BarChart, Bar, ReferenceLine, CartesianGrid,
} from "recharts";

/* ============================================================
   BEDROCK — AI-native finance function with a paper trail.
   Demo org: Cardinal Heating & Air LLC (pilot archetype #1).
   Everything runs on an in-browser port of the deterministic
   ledger engine: balanced entries, provenance, policy routing.
   ============================================================ */

/* ---------- design tokens (ledger-paper direction) ---------- */
const T = {
  paper: "#F7F5EE",
  card: "#FDFCF8",
  ink: "#17241C",
  inkSoft: "#41544A",
  green: "#2F5D4A",
  greenDeep: "#1E3C30",
  rule: "#DCD5C4",
  ruleSoft: "#E9E4D6",
  red: "#A63B25",
  redSoft: "#F6E5E0",
  brass: "#8F7326",
  brassSoft: "#F3ECD8",
  mono: "'IBM Plex Mono', ui-monospace, monospace",
  sans: "'IBM Plex Sans', system-ui, sans-serif",
  serif: "'IBM Plex Serif', Georgia, serif",
};

const fmt = (c) =>
  (c < 0 ? "−$" : "$") +
  Math.abs(c / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtK = (c) => "$" + Math.round(c / 100).toLocaleString("en-US");

/* tiny deterministic hash for demo chain */
const h32 = (s) => {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193); }
  return ("0000000" + (h >>> 0).toString(16)).slice(-8);
};

/* ---------- chart of accounts ---------- */
const COA = [
  ["1000","Operating checking","asset","debit"],["1200","Accounts receivable","asset","debit"],
  ["1500","Equipment","asset","debit"],["1510","Accum. depreciation","contra","credit"],
  ["2000","Accounts payable","liability","credit"],["2200","Sales tax payable","tax","credit"],
  ["3000","Owner's equity","equity","credit"],
  ["4000","Service revenue","revenue","credit"],["4100","Install revenue","revenue","credit"],
  ["5000","Parts & materials","expense","debit"],["5100","Subcontractors","expense","debit"],
  ["6000","Payroll","expense","debit"],["6100","Fuel & vehicle","expense","debit"],
  ["6200","Software & office","expense","debit"],["6300","Rent","expense","debit"],
  ["6400","Insurance","expense","debit"],["6500","Depreciation","expense","debit"],
  ["6600","Meals","expense","debit"],
].map(([code,name,type,normal]) => ({ code, name, type, normal }));
const acct = Object.fromEntries(COA.map(a => [a.code, a]));

/* ---------- seed documents ---------- */
const D = (id, type, source, raw) => ({ id, type, source, raw, sha: h32(raw) + h32(raw + "x") });
const DOCS = {
  ob:   D("doc_001","Opening balance","Migration","OB 2026-05-31 · migrated from QuickBooks Online · verified against 2025 filed return, 0¢ discrepancy"),
  st_m: D("doc_002","Bank statement","Chase (Plaid)","Statement May 2026 · ending balance matches ledger to the cent"),
  gus:  D("doc_010","Payroll report","Gusto","Gusto payroll run 06/13 · 12 employees · gross 21,842.10 · taxes withheld itemized"),
  ferg: D("doc_011","Bank feed line","Plaid","06/17 FERGUSON SUPPLY #2214 · card *4411 · 391.24"),
  ferg2:D("doc_012","Bank feed line","Plaid","06/17 FERGUSON SUPPLY #2231 · card *4411 · 486.20 · 3rd charge today, cum 1,912.60"),
  qf:   D("doc_013","Fuel card feed","QuickFuel","06/18 QUICKFUEL FLEET · truck 2 · 214.77 · 41.2 gal"),
  vz:   D("doc_014","Bank feed line","Plaid","06/19 VERIZON WIRELESS · autopay · 189.44"),
  rod:  D("doc_015","Invoice (OCR)","Email ingest","Rodriguez Duct LLC · inv #91 · duct fab, Hobbs job · 1,840.00 · net 30 · NEW VENDOR"),
  tx:   D("doc_016","Bank feed line","Plaid","06/20 TEXAS COMPTROLLER · webfile · 1,212.40"),
  dep:  D("doc_017","Processor settlement","Stripe","06/23 payout 14,500.00 · commercial install draw #3, Hobbs Elementary · fees itemized"),
  crn:  D("doc_018","Receipt (photo)","Mobile upload","Crane Rental Co · 4hr boom lift · 612.90 · job: Hobbs"),
  cfa:  D("doc_019","Bank feed line","Plaid","06/21 CHICKFILA #0442 DRIVE THR · 38.40 · descriptor garbled in feed"),
  sv:   D("doc_020","Bank feed line","Plaid","06/02 SERVICETITAN INC · subscription · 399.00"),
  rent: D("doc_021","ACH record","Plaid","06/01 OAKDALE PROPERTIES LLC · shop rent · 3,150.00"),
  rev1: D("doc_022","Processor settlement","Stripe","06/05 payout 6,240.00 · 11 residential service calls, fee detail attached"),
  rev2: D("doc_023","Processor settlement","Stripe","06/12 payout 8,912.00 · 14 residential service calls, fee detail attached"),
  ins:  D("doc_024","ACH record","Plaid","06/03 STATE FARM COMM POLICY · 610.00"),
  deps: D("doc_025","Schedule","Bedrock engine","Fleet depreciation · 3 trucks · SL 60 mo · June 1,450.00 · schedule replayable"),
  accr: D("doc_026","Invoice (OCR)","Email ingest","Johnstone Supply · inv #5561 · received 06/28, unpaid at close · 2,208.50"),
};

/* ---------- reviewers ---------- */
const PEOPLE = {
  policy: { name: "Policy engine v1.0.0", role: "Deterministic auto-post rule", kind: "auto" },
  jo: { name: "J. Okafor", role: "Staff bookkeeper", kind: "human" },
  mr: { name: "M. Reyes", role: "Controller (licensed)", kind: "human" },
  aw: { name: "A. Whitfield, CPA", role: "CPA of record · TX lic. #114-882", kind: "human" },
};

/* ---------- seed ledger entries (June, open period) ---------- */
let seq = 0;
const mkEntry = (date, type, memo, lines, policy, ai, approver) => {
  seq += 1;
  const id = "je_" + String(seq).padStart(3, "0");
  return { id, date, type, memo, lines, policy, ai, approver,
    hash: h32(id + memo + lines.map(l => l.code + l.amt).join("")) };
};
const L = (code, side, amt, doc) => ({ code, side, amt, doc });

const SEED_ENTRIES = [
  mkEntry("May 31","opening","Opening balances (QBO migration, tied to filed return)",
    [L("1000","debit",18268855,DOCS.ob),L("1500","debit",8740000,DOCS.ob),
     L("1510","credit",2610000,DOCS.ob),L("3000","credit",24398855,DOCS.ob)],
    "migration",null,PEOPLE.aw),
  mkEntry("Jun 01","standard","Shop rent — Oakdale Properties",
    [L("6300","debit",315000,DOCS.rent),L("1000","credit",315000,DOCS.rent)],
    "auto_post",{conf:.995,pat:"seen",why:"Identical recurring ACH, 14 prior months"},PEOPLE.policy),
  mkEntry("Jun 02","standard","ServiceTitan subscription",
    [L("6200","debit",39900,DOCS.sv),L("1000","credit",39900,DOCS.sv)],
    "auto_post",{conf:.991,pat:"seen",why:"Known SaaS vendor, fixed amount"},PEOPLE.policy),
  mkEntry("Jun 03","standard","State Farm commercial policy",
    [L("6400","debit",61000,DOCS.ins),L("1000","credit",61000,DOCS.ins)],
    "auto_post",{conf:.99,pat:"seen",why:"Recurring insurer draft"},PEOPLE.policy),
  mkEntry("Jun 05","standard","Stripe payout — 11 residential service calls",
    [L("1000","debit",624000,DOCS.rev1),L("4000","credit",624000,DOCS.rev1)],
    "bookkeeper_review",{conf:.985,pat:"seen",why:"Settlement un-netted; fees verified"},PEOPLE.jo),
  mkEntry("Jun 12","standard","Stripe payout — 14 residential service calls",
    [L("1000","debit",891200,DOCS.rev2),L("4000","credit",891200,DOCS.rev2)],
    "bookkeeper_review",{conf:.985,pat:"seen",why:"Settlement un-netted; fees verified"},PEOPLE.jo),
  mkEntry("Jun 13","standard","Gusto payroll run — 12 employees",
    [L("6000","debit",2184210,DOCS.gus),L("1000","credit",2184210,DOCS.gus)],
    "bookkeeper_review",{conf:.995,pat:"seen",why:"Tied to Gusto register line-by-line"},PEOPLE.jo),
  mkEntry("Jun 17","standard","Ferguson Supply #2214 — parts",
    [L("5000","debit",39124,DOCS.ferg),L("1000","credit",39124,DOCS.ferg)],
    "auto_post",{conf:.988,pat:"seen",why:"Known supplier, under $500 gate"},PEOPLE.policy),
  mkEntry("Jun 18","standard","QuickFuel fleet — truck 2",
    [L("6100","debit",21477,DOCS.qf),L("1000","credit",21477,DOCS.qf)],
    "auto_post",{conf:.982,pat:"seen",why:"Fuel card feed, matched to truck"},PEOPLE.policy),
  mkEntry("Jun 19","standard","Verizon Wireless — field tablets",
    [L("6200","debit",18944,DOCS.vz),L("1000","credit",18944,DOCS.vz)],
    "auto_post",{conf:.973,pat:"seen",why:"Recurring autopay"},PEOPLE.policy),
  mkEntry("Jun 30","depreciation","Fleet depreciation — June (drafted by engine)",
    [L("6500","debit",145000,DOCS.deps),L("1510","credit",145000,DOCS.deps)],
    "ai_draft_pending_close",{conf:.999,pat:"schedule",why:"Deterministic schedule, replayable"},PEOPLE.mr),
  mkEntry("Jun 30","accrual","Accrue Johnstone inv #5561 (received, unpaid)",
    [L("5000","debit",220850,DOCS.accr),L("2000","credit",220850,DOCS.accr)],
    "ai_draft_pending_close",{conf:.94,pat:"seen",why:"Invoice dated in-period, payment out-of-period"},PEOPLE.mr),
];

/* ---------- review queue (routed by the policy engine) ---------- */
const SEED_QUEUE = [
  { id:"q1", date:"Jun 20", vendor:"Rodriguez Duct LLC", amt:184000, dir:"out", doc:DOCS.rod,
    proposal:{ code:"5100", conf:.72, pat:"novel", why:"Invoice text suggests fabricated ductwork for Hobbs job → subcontractor COGS" },
    decision:"Bookkeeper review", reason:"Novel vendor never auto-posts, at any amount or confidence", lane:"bk" },
  { id:"q2", date:"Jun 20", vendor:"Texas Comptroller", amt:121240, dir:"out", doc:DOCS.tx,
    proposal:{ code:"2200", conf:.65, pat:"novel", why:"Webfile descriptor pattern → likely sales tax remittance, reduces liability" },
    decision:"Controller review", reason:"Touches a tax account — never a bookkeeper-level decision", lane:"ctrl" },
  { id:"q3", date:"Jun 23", vendor:"Stripe payout — install draw #3", amt:1450000, dir:"in", doc:DOCS.dep,
    proposal:{ code:"4100", conf:.985, pat:"seen", why:"Matches Hobbs Elementary contract draw schedule" },
    decision:"Hard stop", reason:"Amount ≥ $10,000 — human review required, customer notified", lane:"stop" },
  { id:"q4", date:"Jun 24", vendor:"Crane Rental Co", amt:61290, dir:"out", doc:DOCS.crn,
    proposal:{ code:"5000", conf:.83, pat:"similar", why:"Resembles prior equipment-rental charges tied to install jobs" },
    decision:"Bookkeeper review", reason:"Over $500 and only a similar (not seen) pattern", lane:"bk" },
  { id:"q5", date:"Jun 21", vendor:"CHICKFILA #0442", amt:3840, dir:"out", doc:DOCS.cfa,
    proposal:{ code:"6600", conf:.61, pat:"seen", why:"Descriptor garbled in feed; weak match to crew-lunch pattern" },
    decision:"Bookkeeper review · low confidence", reason:"Confidence 0.61 below the 0.80 floor", lane:"bk" },
  { id:"q6", date:"Jun 17", vendor:"Ferguson Supply #2231", amt:48620, dir:"out", doc:DOCS.ferg2,
    proposal:{ code:"5000", conf:.99, pat:"seen", why:"Known supplier — but third charge today; cumulative $1,912.60" },
    decision:"Bookkeeper review", reason:"Daily same-counterparty cumulative cap ($2,000) reached — anti-structuring", lane:"bk" },
];

const CORRECT_OPTIONS = ["5000","5100","6100","6200","6300","6600","2200","4000","4100"];

/* ============================================================ */
export default function App() {
  const [tab, setTab] = useState("overview");
  const [entries, setEntries] = useState(SEED_ENTRIES);
  const [queue, setQueue] = useState(SEED_QUEUE);
  const [trail, setTrail] = useState(null); // provenance drawer payload
  const [reconciled, setReconciled] = useState(false);
  const [closed, setClosed] = useState(false);
  const [log, setLog] = useState([
    "May 2026 period locked by M. Reyes (controller) — chain verified",
    "Opening balances tied to 2025 filed return: 0¢ discrepancy",
  ]);
  const [correcting, setCorrecting] = useState(null);

  const addLog = (m) => setLog((l) => [m, ...l].slice(0, 30));

  /* ---- derived balances (pure fold over the ledger) ---- */
  const bal = useMemo(() => {
    const b = {};
    for (const e of entries) for (const l of e.lines) {
      const a = acct[l.code];
      b[l.code] = (b[l.code] || 0) + (l.side === a.normal ? l.amt : -l.amt);
    }
    return b;
  }, [entries]);
  const trialGap = useMemo(() => {
    let dr = 0, cr = 0;
    for (const e of entries) for (const l of e.lines) (l.side === "debit" ? (dr += l.amt) : (cr += l.amt));
    return dr - cr;
  }, [entries]);

  const rev = (bal["4000"] || 0) + (bal["4100"] || 0);
  const cogs = (bal["5000"] || 0) + (bal["5100"] || 0);
  const opex = ["6000","6100","6200","6300","6400","6500","6600"].reduce((s,c)=>s+(bal[c]||0),0);
  const cash = bal["1000"] || 0;
  const net = rev - cogs - opex;

  /* ---- act on a queue item ---- */
  const resolve = (item, finalCode, reviewer, corrected) => {
    const isIn = item.dir === "in";
    const lines = isIn
      ? [L("1000","debit",item.amt,item.doc), L(finalCode,"credit",item.amt,item.doc)]
      : [L(finalCode,"debit",item.amt,item.doc), L("1000","credit",item.amt,item.doc)];
    const e = mkEntry(item.date,"standard",
      item.vendor + (corrected ? " (corrected in review)" : ""),
      lines, "human:" + reviewer.name, item.proposal, reviewer);
    setEntries((es) => [...es, e]);
    setQueue((q) => q.filter((x) => x.id !== item.id));
    setCorrecting(null);
    addLog(`${reviewer.name} ${corrected ? "corrected → " + acct[finalCode].name : "approved"} ${item.vendor} ${fmt(item.amt)}${corrected ? " — feeds accuracy audit" : ""}`);
  };

  /* ---- close checklist ---- */
  const blocking = [];
  if (queue.length) blocking.push(`${queue.length} transaction${queue.length>1?"s":""} still in review — nothing unposted may remain at close`);
  if (!reconciled) blocking.push("Operating checking lacks an approved bank reconciliation");
  const canClose = blocking.length === 0 && !closed;

  const approveClose = () => {
    setClosed(true);
    addLog("June 2026 close approved by M. Reyes (controller) — period locked, hash chain sealed");
  };

  /* ---- forecast data ---- */
  const wkNet = net / 4.3;
  const forecast = Array.from({ length: 14 }, (_, i) => ({
    wk: i === 0 ? "now" : "w" + i,
    cash: Math.round((cash + wkNet * i * (1 - 0.03 * i * 0.5)) / 100),
    low: Math.round((cash + wkNet * i * 0.55) / 100),
  }));
  const plBars = [
    { m: "Apr", rev: 118400, exp: 96100 }, { m: "May", rev: 131200, exp: 101900 },
    { m: "Jun", rev: Math.round(rev/100), exp: Math.round((cogs+opex)/100) },
  ];

  /* ---- provenance drawer helper ---- */
  const openTrail = (label, amount, entry) => setTrail({ label, amount, entry });

  const Num = ({ c, entry, label, size = 15, color = T.ink, weight = 500 }) => (
    <button onClick={() => entry && openTrail(label, c, entry)} title={entry ? "Open paper trail" : ""}
      style={{ fontFamily: T.mono, fontSize: size, fontWeight: weight, color,
        background:"none", border:"none", padding:0, cursor: entry ? "pointer" : "default",
        borderBottom: entry ? `1px dashed ${T.rule}` : "none", fontVariantNumeric:"tabular-nums" }}>
      {fmt(c)}
    </button>
  );

  const tabs = [
    ["overview","Overview"],["queue",`Review queue${queue.length?` · ${queue.length}`:""}`],
    ["ledger","Ledger"],["close", closed ? "Close ✓" : "Close"],["advisor","Advisor"],
  ];

  return (
    <div style={{ minHeight:"100vh", background:T.paper, color:T.ink, fontFamily:T.sans }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Serif:ital,wght@0,500;0,600;1,500&display=swap');
        *::-webkit-scrollbar{width:8px;height:8px} *::-webkit-scrollbar-thumb{background:${T.rule};border-radius:4px}
        @media (prefers-reduced-motion: reduce){ *{transition:none!important;animation:none!important} }
        .ruled tr{ border-bottom:1px solid ${T.ruleSoft} }
        .stamp{ animation: stampIn .35s cubic-bezier(.2,1.6,.4,1) }
        @keyframes stampIn{ from{ transform:rotate(-8deg) scale(2.2); opacity:0 } to{ transform:rotate(-8deg) scale(1); opacity:1 } }
        button:focus-visible{ outline:2px solid ${T.green}; outline-offset:2px }
      `}</style>

      {/* ---------- masthead ---------- */}
      <header style={{ background:T.greenDeep, color:T.paper, padding:"14px 28px",
        display:"flex", alignItems:"baseline", gap:18, flexWrap:"wrap" }}>
        <div style={{ fontFamily:T.serif, fontSize:24, fontWeight:600, letterSpacing:"-0.01em" }}>
          Bedrock
        </div>
        <div style={{ fontSize:12.5, opacity:.75 }}>Books with receipts — every number opens its paper trail</div>
        <div style={{ marginLeft:"auto", fontFamily:T.mono, fontSize:12, opacity:.85 }}>
          Cardinal Heating &amp; Air LLC · FY2026 · viewing as M. Reyes (controller)
        </div>
      </header>

      {/* ---------- nav ---------- */}
      <nav style={{ display:"flex", gap:2, padding:"0 28px", background:T.greenDeep }}>
        {tabs.map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            style={{ padding:"9px 16px", fontSize:13.5, fontWeight:600, border:"none", cursor:"pointer",
              borderRadius:"8px 8px 0 0", fontFamily:T.sans,
              background: tab===k ? T.paper : "transparent",
              color: tab===k ? T.greenDeep : "rgba(247,245,238,.75)" }}>
            {label}
          </button>
        ))}
      </nav>

      <main style={{ maxWidth:1120, margin:"0 auto", padding:"26px 28px 80px" }}>

        {/* ================= OVERVIEW ================= */}
        {tab === "overview" && (
          <div>
            <SectionTitle k="June 2026 — period open" t="Where the business stands" sub="Every figure below is a fold over posted journal entries. Click any amount to open its paper trail." />
            <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fit,minmax(230px,1fr))", gap:14, marginBottom:22 }}>
              <Stat label="Cash — operating" v={fmt(cash)} note={reconciled ? "reconciled to statement" : "reconciliation pending"} noteColor={reconciled?T.green:T.brass}/>
              <Stat label="Revenue MTD" v={fmt(rev)} note="2 Stripe settlements un-netted" />
              <Stat label="Net income MTD" v={fmt(net)} note={`gross margin ${rev? Math.round((rev-cogs)/rev*100):0}%`} />
              <Stat label="Awaiting human review" v={String(queue.length)} note={queue.length ? "1 hard stop inside" : "queue clear"} noteColor={queue.length?T.red:T.green} big />
            </div>

            <Card title="How this month's numbers were decided">
              <div style={{ display:"flex", gap:0, borderRadius:8, overflow:"hidden", border:`1px solid ${T.rule}`, fontSize:12.5, fontWeight:600 }}>
                <Seg w={34} bg={T.green} fg={T.paper} label="34% auto-posted" sub="seen pattern · <$500 · conf ≥0.97" />
                <Seg w={48} bg="#6D8A7C" fg={T.paper} label="48% bookkeeper-reviewed" sub="novel, similar, or over gates" />
                <Seg w={12} bg={T.brass} fg={T.paper} label="12% controller" sub="tax/equity accounts, closes" />
                <Seg w={6} bg={T.red} fg={T.paper} label="6% hard stop" sub="≥$10k or fraud heuristics" />
              </div>
              <p style={{ fontSize:12.5, color:T.inkSoft, marginTop:10, lineHeight:1.55 }}>
                The AI proposes and explains; a deterministic policy engine decides who must look. Nothing reaches
                the ledger on an unverified model opinion — auto-posts are gated, sampled, and revocable per account
                the moment correction rates rise.
              </p>
            </Card>

            <Card title="Trust status" style={{ marginTop:14 }}>
              <div style={{ display:"flex", flexWrap:"wrap", gap:22, fontFamily:T.mono, fontSize:12.5 }}>
                <Check ok label={`Trial balance Δ = ${trialGap}¢`} />
                <Check ok label={`Hash chain verified · ${entries.length} entries`} />
                <Check ok label="Opening balances tied to filed return" />
                <Check ok={reconciled} label={reconciled ? "Bank reconciled" : "Bank reconciliation pending"} />
                <Check ok label="Named CPA of record: A. Whitfield, CPA" />
                <Check ok label="Data portability: GL export available any time" />
              </div>
            </Card>
          </div>
        )}

        {/* ================= REVIEW QUEUE ================= */}
        {tab === "queue" && (
          <div>
            <SectionTitle k={`${queue.length} item${queue.length===1?"":"s"} routed to humans`} t="Review queue"
              sub="The policy engine explains why each item stopped here. Approve posts the AI's proposal; correct posts your account instead — corrections feed the accuracy audit that tightens future autonomy." />
            {queue.length === 0 && (
              <Card><div style={{ textAlign:"center", padding:"28px 0", color:T.inkSoft }}>
                Queue is clear. The close checklist is waiting on the <b>Close</b> tab.
              </div></Card>
            )}
            {queue.map((q) => {
              const lane = q.lane;
              const laneColor = lane==="stop" ? T.red : lane==="ctrl" ? T.brass : T.green;
              const reviewer = lane==="ctrl"||lane==="stop" ? PEOPLE.mr : PEOPLE.jo;
              return (
                <Card key={q.id} style={{ marginBottom:12, borderLeft:`4px solid ${laneColor}` }}>
                  <div style={{ display:"flex", flexWrap:"wrap", gap:14, alignItems:"baseline" }}>
                    <span style={{ fontFamily:T.mono, fontSize:12, color:T.inkSoft }}>{q.date}</span>
                    <span style={{ fontWeight:600, fontSize:15 }}>{q.vendor}</span>
                    <span style={{ fontFamily:T.mono, fontSize:16, fontWeight:600, marginLeft:"auto",
                      color: q.dir==="in" ? T.green : T.ink }}>{q.dir==="in"?"+":"−"}{fmt(q.amt)}</span>
                  </div>
                  <div style={{ display:"flex", flexWrap:"wrap", gap:8, margin:"10px 0" }}>
                    <Pill bg={laneColor} fg="#fff">{q.decision}</Pill>
                    <Pill bg={T.ruleSoft} fg={T.inkSoft}>{q.reason}</Pill>
                  </div>
                  <div style={{ fontSize:13, color:T.inkSoft, lineHeight:1.5 }}>
                    <b style={{ color:T.ink }}>AI proposes:</b> {acct[q.proposal.code].code} {acct[q.proposal.code].name}
                    <span style={{ fontFamily:T.mono }}> · conf {q.proposal.conf.toFixed(2)} · {q.proposal.pat}</span>
                    <br/><span style={{ fontStyle:"italic" }}>“{q.proposal.why}”</span>
                    <br/><span style={{ fontFamily:T.mono, fontSize:11.5 }}>source: {q.doc.type} · {q.doc.source} · sha {q.doc.sha.slice(0,10)}…</span>
                  </div>
                  <div style={{ display:"flex", gap:8, marginTop:12, flexWrap:"wrap", alignItems:"center" }}>
                    <Btn primary onClick={() => resolve(q, q.proposal.code, reviewer, false)}>
                      Approve as {acct[q.proposal.code].name}
                    </Btn>
                    {correcting === q.id ? (
                      <span style={{ display:"inline-flex", gap:8, alignItems:"center" }}>
                        <select id={"sel_"+q.id} defaultValue={q.proposal.code}
                          style={{ fontFamily:T.mono, fontSize:12.5, padding:"7px 8px", border:`1px solid ${T.rule}`,
                            borderRadius:6, background:T.card }}>
                          {CORRECT_OPTIONS.map(c => <option key={c} value={c}>{c} · {acct[c].name}</option>)}
                        </select>
                        <Btn onClick={() => resolve(q, document.getElementById("sel_"+q.id).value, reviewer, true)}>Post correction</Btn>
                        <Btn ghost onClick={() => setCorrecting(null)}>Cancel</Btn>
                      </span>
                    ) : (
                      <Btn onClick={() => setCorrecting(q.id)}>Correct account…</Btn>
                    )}
                    <span style={{ fontSize:11.5, color:T.inkSoft, marginLeft:"auto" }}>
                      acting as {reviewer.name} · {reviewer.role}
                    </span>
                  </div>
                </Card>
              );
            })}
          </div>
        )}

        {/* ================= LEDGER ================= */}
        {tab === "ledger" && (
          <div>
            <SectionTitle k={`${entries.length} journal entries · append-only · hash-chained`} t="General ledger"
              sub="Corrections happen by reversal, never by edit. Click any amount for its paper trail." />
            <Card pad={0}>
              <table className="ruled" style={{ width:"100%", borderCollapse:"collapse", fontSize:13 }}>
                <thead>
                  <tr style={{ background:T.ruleSoft, textAlign:"left" }}>
                    {["Date","Memo","Account","Dr","Cr","Decided by","Hash"].map(hd =>
                      <th key={hd} style={{ padding:"8px 12px", fontSize:11, letterSpacing:".06em",
                        textTransform:"uppercase", color:T.inkSoft }}>{hd}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {[...entries].reverse().flatMap((e) =>
                    e.lines.map((l, i) => (
                      <tr key={e.id + i} style={{ background: i===0 ? "transparent" : "rgba(0,0,0,.012)" }}>
                        <td style={{ padding:"7px 12px", fontFamily:T.mono, fontSize:12, whiteSpace:"nowrap" }}>{i===0 ? e.date : ""}</td>
                        <td style={{ padding:"7px 12px", maxWidth:260 }}>{i===0 ? e.memo : ""}</td>
                        <td style={{ padding:"7px 12px", fontFamily:T.mono, fontSize:12 }}>{l.code} {acct[l.code].name}</td>
                        <td style={{ padding:"7px 12px", textAlign:"right" }}>{l.side==="debit" && <Num c={l.amt} entry={e} label={e.memo} size={13}/>}</td>
                        <td style={{ padding:"7px 12px", textAlign:"right" }}>{l.side==="credit" && <Num c={l.amt} entry={e} label={e.memo} size={13}/>}</td>
                        <td style={{ padding:"7px 12px", fontSize:11.5, color:T.inkSoft }}>{i===0 ? e.approver.name : ""}</td>
                        <td style={{ padding:"7px 12px", fontFamily:T.mono, fontSize:11, color:T.inkSoft }}>{i===0 ? e.hash : ""}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </Card>
          </div>
        )}

        {/* ================= CLOSE ================= */}
        {tab === "close" && (
          <div>
            <SectionTitle k="Month-end close · June 2026" t="Controller sign-off"
              sub="The engine drafts the close; an adversarial second pass hunts for errors; a licensed human approves. A close cannot be forced past a blocking finding." />
            <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:14 }}>
              <Card title="Close checklist">
                <CheckRow ok label="Depreciation drafted from replayable schedule" sub="Fleet · $1,450.00 · engine-drafted, awaiting lock" />
                <CheckRow ok label="Accruals drafted" sub="Johnstone inv #5561 · $2,208.50 received in-period, unpaid" />
                <CheckRow ok={queue.length===0} label="Review queue clear" sub={queue.length ? `${queue.length} item(s) outstanding — resolve on the Review tab` : "All routed items decided by named humans"} />
                <CheckRow ok={reconciled} label="Bank reconciliation approved" sub={reconciled ? "Chase statement ties to ledger to the cent" : "Statement imported; match pending"} />
                {!reconciled && !closed && (
                  <Btn primary onClick={() => { setReconciled(true); addLog("Bank reconciliation approved by M. Reyes — statement ties to the cent"); }} style={{ marginTop:8 }}>
                    Approve reconciliation
                  </Btn>
                )}
              </Card>
              <Card title="Adversarial pass — independent error hunt">
                {blocking.length === 0 ? (
                  <div style={{ color:T.green, fontWeight:600, fontSize:13.5 }}>
                    ✓ No blocking findings. Duplicate-document scan clean · recurring-accrual scan clean · cutoff scan clean.
                  </div>
                ) : blocking.map((b,i)=>(
                  <div key={i} style={{ background:T.redSoft, border:`1px solid ${T.red}33`, color:T.red,
                    borderRadius:8, padding:"10px 12px", fontSize:13, marginBottom:8, fontWeight:500 }}>
                    BLOCKING — {b}
                  </div>
                ))}
                <div style={{ marginTop:16, position:"relative", minHeight:86 }}>
                  {!closed ? (
                    <Btn primary big disabled={!canClose} onClick={approveClose}>
                      {canClose ? "Approve close & lock June 2026" : "Close blocked until findings clear"}
                    </Btn>
                  ) : (
                    <div className="stamp" style={{ display:"inline-block", border:`3px solid ${T.red}`,
                      color:T.red, fontFamily:T.serif, fontWeight:600, fontSize:26, letterSpacing:".08em",
                      padding:"8px 22px", borderRadius:6, transform:"rotate(-8deg)", opacity:.92 }}>
                      CLOSED · M. REYES
                    </div>
                  )}
                  {closed && <p style={{ fontSize:12, color:T.inkSoft, marginTop:10 }}>
                    Period locked. Any July adjustment to June posts as a dated reversal — history is never edited.
                    Filings drafted from this close route to A. Whitfield, CPA before anything leaves the building.
                  </p>}
                </div>
              </Card>
            </div>
            <Card title="Activity log" style={{ marginTop:14 }}>
              {log.map((m,i)=>(
                <div key={i} style={{ fontFamily:T.mono, fontSize:12, padding:"5px 0",
                  borderBottom:`1px solid ${T.ruleSoft}`, color: i===0 ? T.ink : T.inkSoft }}>{m}</div>
              ))}
            </Card>
          </div>
        )}

        {/* ================= ADVISOR ================= */}
        {tab === "advisor" && (
          <div>
            <SectionTitle k="CFO layer · advisory only — never auto-executes" t="Forward view"
              sub="Forecasts and recommendations are generated from closed, reconciled data and clearly labeled. Nothing here moves money or files anything." />
            <div style={{ display:"grid", gridTemplateColumns:"3fr 2fr", gap:14 }}>
              <Card title="13-week cash forecast">
                <div style={{ height:230 }}>
                  <ResponsiveContainer>
                    <AreaChart data={forecast} margin={{ top:8, right:8, bottom:0, left:8 }}>
                      <CartesianGrid stroke={T.ruleSoft} vertical={false}/>
                      <XAxis dataKey="wk" tick={{ fontFamily:T.mono, fontSize:11 }} stroke={T.inkSoft}/>
                      <YAxis tickFormatter={(v)=>"$"+(v/1000).toFixed(0)+"k"} tick={{ fontFamily:T.mono, fontSize:11 }} stroke={T.inkSoft} width={52}/>
                      <Tooltip formatter={(v)=>"$"+v.toLocaleString()} contentStyle={{ fontFamily:T.mono, fontSize:12, background:T.card, border:`1px solid ${T.rule}` }}/>
                      <ReferenceLine y={120000} stroke={T.red} strokeDasharray="4 4"
                        label={{ value:"payroll safety floor", fontSize:11, fill:T.red, position:"insideBottomRight" }}/>
                      <Area dataKey="low" stroke="none" fill={T.ruleSoft} name="conservative"/>
                      <Area dataKey="cash" stroke={T.green} strokeWidth={2} fill={T.green+"22"} name="run-rate"/>
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
                <p style={{ fontSize:12, color:T.inkSoft, marginTop:6 }}>
                  Bands: run-rate vs conservative (install draws slip 45 days). Floor = two payroll cycles.
                </p>
              </Card>
              <Card title="Revenue vs spend — 3 months">
                <div style={{ height:230 }}>
                  <ResponsiveContainer>
                    <BarChart data={plBars} margin={{ top:8, right:8, bottom:0, left:8 }}>
                      <CartesianGrid stroke={T.ruleSoft} vertical={false}/>
                      <XAxis dataKey="m" tick={{ fontFamily:T.mono, fontSize:11 }} stroke={T.inkSoft}/>
                      <YAxis tickFormatter={(v)=>"$"+(v/1000).toFixed(0)+"k"} tick={{ fontFamily:T.mono, fontSize:11 }} stroke={T.inkSoft} width={52}/>
                      <Tooltip formatter={(v)=>"$"+v.toLocaleString()} contentStyle={{ fontFamily:T.mono, fontSize:12, background:T.card, border:`1px solid ${T.rule}` }}/>
                      <Bar dataKey="rev" fill={T.green} name="revenue" radius={[3,3,0,0]}/>
                      <Bar dataKey="exp" fill={T.rule} name="spend" radius={[3,3,0,0]}/>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </Card>
            </div>
            <Card title="This month's advisory notes" style={{ marginTop:14 }}>
              {[
                ["Margin mix is shifting", "Subcontractor COGS (Rodriguez Duct) appeared in May and grew in June. If install work keeps scaling through subs, gross margin trends toward ~52% on that line vs ~68% on self-performed service. Worth pricing the next commercial bid against sub rates, not crew rates."],
                ["Install draw #3 cleared its hard stop", "The $14,500 Stripe payout was verified against the Hobbs Elementary contract schedule by the controller. Draw #4 (~$16,000) expected mid-July — the forecast's conservative band assumes it slips."],
                ["Sales tax cadence", "The Texas Comptroller webfile pattern suggests monthly remittance. Once confirmed by the CPA of record, Bedrock will draft each filing for A. Whitfield's signature — filings never leave without a licensed human."],
              ].map(([t, b], i) => (
                <div key={i} style={{ padding:"10px 0", borderBottom: i<2 ? `1px solid ${T.ruleSoft}` : "none" }}>
                  <div style={{ fontWeight:600, fontSize:13.5 }}>{t}</div>
                  <div style={{ fontSize:13, color:T.inkSoft, lineHeight:1.55, marginTop:3 }}>{b}</div>
                </div>
              ))}
              <div style={{ marginTop:10, fontSize:11.5, fontFamily:T.mono, color:T.brass }}>
                ADVISORY — generated by bedrock-cfo-1, reviewed by M. Reyes before delivery. No action taken automatically.
              </div>
            </Card>
          </div>
        )}
      </main>

      {/* ---------- chain footer ---------- */}
      <footer style={{ position:"fixed", bottom:0, left:0, right:0, background:T.greenDeep, color:"rgba(247,245,238,.8)",
        fontFamily:T.mono, fontSize:11.5, padding:"7px 28px", display:"flex", gap:24, flexWrap:"wrap" }}>
        <span>chain: verified · head {entries[entries.length-1].hash}</span>
        <span>trial balance Δ {trialGap}¢</span>
        <span>docs {Object.keys(DOCS).length} · entries {entries.length}</span>
        <span style={{ marginLeft:"auto" }}>CPA of record: A. Whitfield · SOC 2 controls active · GL export available</span>
      </footer>

      {/* ---------- PAPER TRAIL drawer (signature element) ---------- */}
      {trail && (
        <div onClick={() => setTrail(null)} style={{ position:"fixed", inset:0, background:"rgba(23,36,28,.45)", zIndex:50 }}>
          <aside onClick={(e)=>e.stopPropagation()} style={{ position:"absolute", top:0, right:0, bottom:0, width:"min(430px,92vw)",
            background:T.paper, borderLeft:`1px solid ${T.rule}`, boxShadow:"-16px 0 40px rgba(0,0,0,.18)",
            padding:"22px 22px 40px", overflowY:"auto" }}>
            <div style={{ display:"flex", alignItems:"baseline", gap:10 }}>
              <div style={{ fontFamily:T.serif, fontSize:19, fontWeight:600 }}>Paper trail</div>
              <button onClick={()=>setTrail(null)} style={{ marginLeft:"auto", border:"none", background:"none",
                fontSize:20, cursor:"pointer", color:T.inkSoft }} aria-label="Close paper trail">×</button>
            </div>
            <div style={{ fontSize:13, color:T.inkSoft, margin:"2px 0 16px" }}>{trail.label}</div>
            <div style={{ fontFamily:T.mono, fontSize:22, fontWeight:600, marginBottom:18 }}>{fmt(trail.amount)}</div>

            {(() => {
              const e = trail.entry;
              const doc = e.lines[0].doc;
              const steps = [
                ["1 · Source document", [
                  [doc.type + " — " + doc.source, false],
                  ["“" + doc.raw + "”", true],
                  ["sha256 " + doc.sha + " · immutable, content-addressed", false],
                ]],
                e.ai ? ["2 · AI proposal (never posts by itself)", [
                  ["“" + e.ai.why + "”", true],
                  [`confidence ${e.ai.conf.toFixed(2)} · pattern: ${e.ai.pat} · model bedrock-cat-1`, false],
                  ["reasoning inputs snapshotted — this decision is replayable", false],
                ]] : ["2 · No AI involvement", [["Posted directly from migration records", false]]],
                ["3 · Policy decision", [
                  [e.policy.replace("_"," ").replace("human:","reviewed by "), false],
                  ["policy engine v1.0.0 · thresholds at decision time logged", false],
                ]],
                ["4 · Accountable human", [
                  [e.approver.name + " — " + e.approver.role, false],
                  [e.approver.kind === "auto" ? "auto-post: sampled into human QA at 5%" : "decision recorded in review log; feeds the accuracy audit", false],
                ]],
                ["5 · Ledger seal", [
                  ["entry " + e.id + " · hash " + e.hash, false],
                  ["chained to prior entry — editing history breaks every later hash", false],
                ]],
              ];
              return steps.map(([title, rows], i) => (
                <div key={i} style={{ position:"relative", marginBottom:14, background:T.card,
                  border:`1px solid ${T.rule}`, borderRadius:6, padding:"12px 14px",
                  boxShadow:"0 1px 2px rgba(0,0,0,.05)" }}>
                  <div style={{ position:"absolute", top:-7, left:18, width:22, height:8, background:T.inkSoft,
                    borderRadius:2, opacity:.55, transform:"rotate(" + (i%2? 4 : -4) + "deg)" }} aria-hidden />
                  <div style={{ fontSize:11, letterSpacing:".08em", textTransform:"uppercase",
                    color:T.green, fontWeight:700, marginBottom:6 }}>{title}</div>
                  {rows.map(([txt, quote], j) => (
                    <div key={j} style={{ fontSize:12.5, lineHeight:1.55,
                      fontFamily: quote ? T.sans : T.mono,
                      fontStyle: quote ? "italic" : "normal",
                      color: quote ? T.ink : T.inkSoft, marginBottom:3 }}>{txt}</div>
                  ))}
                </div>
              ));
            })()}
            <div style={{ fontSize:11.5, color:T.inkSoft, lineHeight:1.5 }}>
              This is the answer to “why is this number what it is” — a join over the ledger, not a generated explanation.
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}

/* ---------- small building blocks ---------- */
const SectionTitle = ({ k, t, sub }) => (
  <div style={{ marginBottom:18 }}>
    <div style={{ fontFamily:T.mono, fontSize:11.5, letterSpacing:".08em", textTransform:"uppercase", color:T.brass, fontWeight:600 }}>{k}</div>
    <h1 style={{ fontFamily:T.serif, fontSize:26, fontWeight:600, margin:"2px 0 4px", color:T.ink }}>{t}</h1>
    {sub && <p style={{ fontSize:13.5, color:T.inkSoft, maxWidth:720, lineHeight:1.55, margin:0 }}>{sub}</p>}
  </div>
);

const Card = ({ title, children, style, pad = 16 }) => (
  <section style={{ background:T.card, border:`1px solid ${T.rule}`, borderRadius:10,
    padding:pad, boxShadow:"0 1px 3px rgba(23,36,28,.05)", ...style }}>
    {title && <div style={{ fontSize:12, letterSpacing:".07em", textTransform:"uppercase",
      color:T.inkSoft, fontWeight:700, marginBottom:12 }}>{title}</div>}
    {children}
  </section>
);

const Stat = ({ label, v, note, noteColor = T.inkSoft, big }) => (
  <div style={{ background:T.card, border:`1px solid ${T.rule}`, borderRadius:10, padding:"14px 16px" }}>
    <div style={{ fontSize:11.5, letterSpacing:".06em", textTransform:"uppercase", color:T.inkSoft, fontWeight:600 }}>{label}</div>
    <div style={{ fontFamily:T.mono, fontSize: big ? 30 : 23, fontWeight:600, margin:"4px 0 2px", fontVariantNumeric:"tabular-nums" }}>{v}</div>
    <div style={{ fontSize:11.5, color:noteColor, fontWeight:500 }}>{note}</div>
  </div>
);

const Seg = ({ w, bg, fg, label, sub }) => (
  <div style={{ width:w+"%", background:bg, color:fg, padding:"10px 10px", minWidth:0 }}>
    <div style={{ whiteSpace:"nowrap", overflow:"hidden", textOverflow:"ellipsis" }}>{label}</div>
    <div style={{ fontSize:10.5, fontWeight:400, opacity:.85, whiteSpace:"nowrap", overflow:"hidden", textOverflow:"ellipsis" }}>{sub}</div>
  </div>
);

const Pill = ({ bg, fg, children }) => (
  <span style={{ background:bg, color:fg, fontSize:11.5, fontWeight:600, borderRadius:99, padding:"3px 10px" }}>{children}</span>
);

const Check = ({ ok, label }) => (
  <span style={{ color: ok ? T.green : T.brass, fontWeight:600 }}>{ok ? "✓" : "•"} {label}</span>
);

const CheckRow = ({ ok, label, sub }) => (
  <div style={{ display:"flex", gap:10, padding:"8px 0", borderBottom:`1px solid ${T.ruleSoft}`, alignItems:"flex-start" }}>
    <span style={{ fontFamily:T.mono, color: ok ? T.green : T.red, fontWeight:700 }}>{ok ? "✓" : "✗"}</span>
    <div>
      <div style={{ fontSize:13.5, fontWeight:600 }}>{label}</div>
      <div style={{ fontSize:12, color:T.inkSoft }}>{sub}</div>
    </div>
  </div>
);

const Btn = ({ children, onClick, primary, ghost, big, disabled, style }) => (
  <button onClick={onClick} disabled={disabled}
    style={{ fontFamily:T.sans, fontSize: big ? 14.5 : 12.5, fontWeight:600, cursor: disabled ? "not-allowed" : "pointer",
      padding: big ? "12px 20px" : "8px 14px", borderRadius:7,
      background: disabled ? T.ruleSoft : primary ? T.green : ghost ? "transparent" : T.card,
      color: disabled ? T.inkSoft : primary ? "#fff" : T.ink,
      border: primary ? "none" : `1px solid ${T.rule}`, ...style }}>
    {children}
  </button>
);
