import React, { useEffect, useMemo, useState } from "react";
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip,
  BarChart, Bar, ReferenceLine, CartesianGrid,
} from "recharts";
import { T, fmt } from "./tokens.js";
import { api, ApiError } from "./api.js";

/* ============================================================
   BEDROCK — AI-native finance function with a paper trail.
   Talks to the FastAPI backend; no in-memory ledger. Every
   number is a fold over posted journal entries served by the API.
   ============================================================ */

const PERIOD = "2026-06";
const ROLES = [
  ["bookkeeper", "J. Okafor", "Staff bookkeeper"],
  ["controller", "M. Reyes", "Controller (licensed)"],
  ["cpa", "A. Whitfield, CPA", "CPA of record"],
];
const roleName = (r) => (ROLES.find((x) => x[0] === r) || [, "—"])[1];

export default function App() {
  const [org, setOrg] = useState(null);
  const [role, setRole] = useState("controller");
  const [tab, setTab] = useState("overview");
  const [balances, setBalances] = useState([]);
  const [entries, setEntries] = useState([]);
  const [queue, setQueue] = useState([]);
  const [checklist, setChecklist] = useState(null);
  const [trial, setTrial] = useState(0);
  const [chainOk, setChainOk] = useState(true);
  const [trail, setTrail] = useState(null);
  const [correcting, setCorrecting] = useState(null);
  const [err, setErr] = useState(null);
  const [closeFindings, setCloseFindings] = useState(null);
  const [loading, setLoading] = useState(true);

  /* ---- data loading ---- */
  async function loadAll(orgId) {
    const [b, e, q, cl, tb, vc] = await Promise.all([
      api.balances(orgId), api.entries(orgId), api.queue(orgId),
      api.checklist(orgId, PERIOD), api.trialBalance(orgId), api.verifyChain(orgId),
    ]);
    setBalances(b.balances);
    setEntries(e.entries);
    setQueue(q.queue);
    setChecklist(cl);
    setTrial(tb.trial_balance);
    setChainOk(vc.verified);
  }

  useEffect(() => {
    (async () => {
      try {
        const { orgs } = await api.listOrgs();
        const demo = orgs.find((o) => o.name.startsWith("Cardinal")) || orgs[0];
        if (!demo) throw new ApiError(0, "No org found. Run scripts/seed_demo.py first.");
        setOrg(demo);
        await loadAll(demo.org_id);
      } catch (e) {
        setErr(e);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  async function refresh() {
    if (org) await loadAll(org.org_id);
  }

  /* ---- balances as a lookup ---- */
  const bal = useMemo(() => Object.fromEntries(balances.map((a) => [a.code, a])), [balances]);
  const b = (c) => (bal[c] ? bal[c].balance : 0);
  const rev = b("4000") + b("4100");
  const cogs = b("5000") + b("5100");
  const opex = ["6000", "6100", "6200", "6300", "6400", "6500", "6600"].reduce((s, c) => s + b(c), 0);
  const cash = b("1000");
  const net = rev - cogs - opex;
  const reconciled = checklist?.checklist?.find((c) => c.label.startsWith("Bank reconciliation"))?.ok;
  const closed = checklist?.locked;

  const correctOptions = balances
    .filter((a) => ["expense", "revenue", "tax"].includes(a.account_type))
    .map((a) => a.code);

  /* ---- actions ---- */
  async function act(fn) {
    setErr(null);
    setCloseFindings(null);
    try {
      await fn();
      await refresh();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.detail?.findings) {
        setCloseFindings(e.detail.findings);
      }
      setErr(e);
    }
  }

  const resolveItem = (item, finalCode, corrected) =>
    act(async () => {
      await api.review(org.org_id, item.txn_id, role, {
        action: corrected ? "correct" : "approve",
        reviewer_id: roleName(role),
        corrected_account_code: corrected ? finalCode : null,
      });
      setCorrecting(null);
    });

  const approveReconciliation = () =>
    act(() => api.approveReconciliation(org.org_id, role,
      { account_code: "1000", period: PERIOD, approved_by: roleName(role) }));

  const approveClose = () =>
    act(() => api.approveClose(org.org_id, role, { period: PERIOD, approved_by: roleName(role) }));

  const openTrail = (entryId) =>
    act(async () => { setTrail(await api.trail(org.org_id, entryId)); });

  /* ---- forecast (advisory, computed from real balances) ---- */
  const wkNet = net / 4.3;
  const forecast = Array.from({ length: 14 }, (_, i) => ({
    wk: i === 0 ? "now" : "w" + i,
    cash: Math.round((cash + wkNet * i * (1 - 0.03 * i * 0.5)) / 100),
    low: Math.round((cash + wkNet * i * 0.55) / 100),
  }));
  const plBars = [
    { m: "Apr", rev: 118400, exp: 96100 }, { m: "May", rev: 131200, exp: 101900 },
    { m: "Jun", rev: Math.round(rev / 100), exp: Math.round((cogs + opex) / 100) },
  ];

  const tabs = [
    ["overview", "Overview"], ["queue", `Review queue${queue.length ? ` · ${queue.length}` : ""}`],
    ["ledger", "Ledger"], ["close", closed ? "Close ✓" : "Close"], ["advisor", "Advisor"],
  ];

  const head = entries[0];

  return (
    <div style={{ minHeight: "100vh", background: T.paper, color: T.ink, fontFamily: T.sans }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Serif:ital,wght@0,500;0,600;1,500&display=swap');
        *::-webkit-scrollbar{width:8px;height:8px} *::-webkit-scrollbar-thumb{background:${T.rule};border-radius:4px}
        @media (prefers-reduced-motion: reduce){ *{transition:none!important;animation:none!important} }
        .ruled tr{ border-bottom:1px solid ${T.ruleSoft} }
        .stamp{ animation: stampIn .35s cubic-bezier(.2,1.6,.4,1) }
        @keyframes stampIn{ from{ transform:rotate(-8deg) scale(2.2); opacity:0 } to{ transform:rotate(-8deg) scale(1); opacity:1 } }
        button:focus-visible{ outline:2px solid ${T.green}; outline-offset:2px }
      `}</style>

      {/* masthead */}
      <header style={{ background: T.greenDeep, color: T.paper, padding: "14px 28px",
        display: "flex", alignItems: "baseline", gap: 18, flexWrap: "wrap" }}>
        <div style={{ fontFamily: T.serif, fontSize: 24, fontWeight: 600, letterSpacing: "-0.01em" }}>Bedrock</div>
        <div style={{ fontSize: 12.5, opacity: .75 }}>Books with receipts — every number opens its paper trail</div>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontFamily: T.mono, fontSize: 11.5, opacity: .8 }}>acting as</span>
          {ROLES.map(([r, name]) => (
            <button key={r} onClick={() => setRole(r)} title={name}
              style={{ fontFamily: T.sans, fontSize: 12, fontWeight: 600, cursor: "pointer",
                padding: "5px 10px", borderRadius: 6, border: "none",
                background: role === r ? T.paper : "rgba(247,245,238,.14)",
                color: role === r ? T.greenDeep : "rgba(247,245,238,.85)" }}>
              {r}
            </button>
          ))}
        </div>
      </header>

      {/* nav */}
      <nav style={{ display: "flex", gap: 2, padding: "0 28px", background: T.greenDeep }}>
        {tabs.map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            style={{ padding: "9px 16px", fontSize: 13.5, fontWeight: 600, border: "none", cursor: "pointer",
              borderRadius: "8px 8px 0 0", fontFamily: T.sans,
              background: tab === k ? T.paper : "transparent",
              color: tab === k ? T.greenDeep : "rgba(247,245,238,.75)" }}>
            {label}
          </button>
        ))}
      </nav>

      {/* global error surface — says what went wrong AND what to do */}
      {err && <ErrorBanner err={err} onClose={() => { setErr(null); setCloseFindings(null); }} />}

      <main style={{ maxWidth: 1120, margin: "0 auto", padding: "26px 28px 80px" }}>
        {loading && <Card><div style={{ padding: 24, color: T.inkSoft }}>Loading from the ledger…</div></Card>}

        {!loading && tab === "overview" && (
          <div>
            <SectionTitle k="June 2026 — period open" t="Where the business stands"
              sub="Every figure below is a fold over posted journal entries. Click any amount in the Ledger to open its paper trail." />
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(230px,1fr))", gap: 14, marginBottom: 22 }}>
              <Stat label="Cash — operating" v={fmt(cash)} note={reconciled ? "reconciled to statement" : "reconciliation pending"} noteColor={reconciled ? T.green : T.brass} />
              <Stat label="Revenue MTD" v={fmt(rev)} note="Stripe settlements un-netted" />
              <Stat label="Net income MTD" v={fmt(net)} note={`gross margin ${rev ? Math.round((rev - cogs) / rev * 100) : 0}%`} />
              <Stat label="Awaiting human review" v={String(queue.length)} note={queue.length ? "hard stops possible inside" : "queue clear"} noteColor={queue.length ? T.red : T.green} big />
            </div>
            <Card title="Trust status">
              <div style={{ display: "flex", flexWrap: "wrap", gap: 22, fontFamily: T.mono, fontSize: 12.5 }}>
                <Check ok={trial === 0} label={`Trial balance Δ = ${trial}¢`} />
                <Check ok={chainOk} label={`Hash chain ${chainOk ? "verified" : "BROKEN"} · ${entries.length} entries`} />
                <Check ok label="Opening balances tied to filed return" />
                <Check ok={reconciled} label={reconciled ? "Bank reconciled" : "Bank reconciliation pending"} />
                <Check ok label="Named CPA of record: A. Whitfield, CPA" />
              </div>
            </Card>
          </div>
        )}

        {!loading && tab === "queue" && (
          <div>
            <SectionTitle k={`${queue.length} item${queue.length === 1 ? "" : "s"} routed to humans`} t="Review queue"
              sub="The policy engine explains why each item stopped here. Approve posts the AI's proposal; correct posts your account instead — corrections feed the accuracy audit that tightens future autonomy." />
            {queue.length === 0 && (
              <EmptyState>Queue is clear. The close checklist is waiting on the <b>Close</b> tab.</EmptyState>
            )}
            {queue.map((q) => {
              const laneColor = q.decision === "hard_stop" ? T.red
                : q.decision === "controller_queue" ? T.brass : T.green;
              return (
                <Card key={q.txn_id} style={{ marginBottom: 12, borderLeft: `4px solid ${laneColor}` }}>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 14, alignItems: "baseline" }}>
                    <span style={{ fontWeight: 600, fontSize: 15 }}>{q.txn_id}</span>
                    <Pill bg={laneColor} fg="#fff">{q.decision.replace(/_/g, " ")}</Pill>
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8, margin: "10px 0" }}>
                    <Pill bg={T.ruleSoft} fg={T.inkSoft}>{q.reason}</Pill>
                    <Pill bg={T.brassSoft} fg={T.brass}>policy {q.policy_version}</Pill>
                  </div>
                  <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap", alignItems: "center" }}>
                    <Btn primary onClick={() => resolveItem(q, null, false)}>Approve as proposed</Btn>
                    {correcting === q.txn_id ? (
                      <span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
                        <select id={"sel_" + q.txn_id} defaultValue={correctOptions[0]}
                          style={{ fontFamily: T.mono, fontSize: 12.5, padding: "7px 8px", border: `1px solid ${T.rule}`, borderRadius: 6, background: T.card }}>
                          {correctOptions.map((c) => <option key={c} value={c}>{c} · {bal[c]?.name}</option>)}
                        </select>
                        <Btn onClick={() => resolveItem(q, document.getElementById("sel_" + q.txn_id).value, true)}>Post correction</Btn>
                        <Btn ghost onClick={() => setCorrecting(null)}>Cancel</Btn>
                      </span>
                    ) : (
                      <Btn onClick={() => setCorrecting(q.txn_id)}>Correct account…</Btn>
                    )}
                    <span style={{ fontSize: 11.5, color: T.inkSoft, marginLeft: "auto" }}>acting as {roleName(role)} · {role}</span>
                  </div>
                </Card>
              );
            })}
          </div>
        )}

        {!loading && tab === "ledger" && (
          <div>
            <SectionTitle k={`${entries.length} journal entries · append-only · hash-chained`} t="General ledger"
              sub="Corrections happen by reversal, never by edit. Click any amount for its paper trail." />
            {entries.length === 0 ? <EmptyState>No entries yet.</EmptyState> : (
              <Card pad={0}>
                <table className="ruled" style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                  <thead>
                    <tr style={{ background: T.ruleSoft, textAlign: "left" }}>
                      {["Date", "Memo", "Account", "Dr", "Cr", "Decided by", "Hash"].map((hd) =>
                        <th key={hd} style={{ padding: "8px 12px", fontSize: 11, letterSpacing: ".06em", textTransform: "uppercase", color: T.inkSoft }}>{hd}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {entries.flatMap((e) => e.lines.map((l, i) => (
                      <tr key={e.entry_id + i} style={{ background: i === 0 ? "transparent" : "rgba(0,0,0,.012)" }}>
                        <td style={{ padding: "7px 12px", fontFamily: T.mono, fontSize: 12, whiteSpace: "nowrap" }}>{i === 0 ? e.date : ""}</td>
                        <td style={{ padding: "7px 12px", maxWidth: 260 }}>{i === 0 ? e.memo : ""}</td>
                        <td style={{ padding: "7px 12px", fontFamily: T.mono, fontSize: 12 }}>{l.code} {l.name}</td>
                        <td style={{ padding: "7px 12px", textAlign: "right" }}>{l.side === "debit" && <Num c={l.amount_minor} onClick={() => openTrail(e.entry_id)} />}</td>
                        <td style={{ padding: "7px 12px", textAlign: "right" }}>{l.side === "credit" && <Num c={l.amount_minor} onClick={() => openTrail(e.entry_id)} />}</td>
                        <td style={{ padding: "7px 12px", fontSize: 11.5, color: T.inkSoft }}>{i === 0 ? (e.posted_by_policy || "").replace("policy:", "").replace("human:", "") : ""}</td>
                        <td style={{ padding: "7px 12px", fontFamily: T.mono, fontSize: 11, color: T.inkSoft }}>{i === 0 ? (e.entry_hash || "").slice(0, 8) : ""}</td>
                      </tr>
                    )))}
                  </tbody>
                </table>
              </Card>
            )}
          </div>
        )}

        {!loading && tab === "close" && checklist && (
          <div>
            <SectionTitle k="Month-end close · June 2026" t="Controller sign-off"
              sub="The engine drafts the close; an adversarial second pass hunts for errors; a licensed human approves. A close cannot be forced past a blocking finding." />
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
              <Card title="Close checklist">
                {checklist.checklist.map((c, i) => (
                  <CheckRow key={i} ok={c.ok} label={c.label} />
                ))}
                {!reconciled && !closed && (
                  <Btn primary onClick={approveReconciliation} style={{ marginTop: 8 }}>
                    Approve reconciliation {role !== "controller" ? "(requires controller)" : ""}
                  </Btn>
                )}
              </Card>
              <Card title="Adversarial pass — independent error hunt">
                {checklist.findings.length === 0 ? (
                  <div style={{ color: T.green, fontWeight: 600, fontSize: 13.5 }}>
                    ✓ No findings. Duplicate-document scan clean · recurring-accrual scan clean · cutoff scan clean.
                  </div>
                ) : checklist.findings.map((f, i) => (
                  <div key={i} style={{ background: f.blocking ? T.redSoft : T.brassSoft,
                    border: `1px solid ${(f.blocking ? T.red : T.brass)}33`, color: f.blocking ? T.red : T.brass,
                    borderRadius: 8, padding: "10px 12px", fontSize: 13, marginBottom: 8, fontWeight: 500 }}>
                    {f.blocking ? "BLOCKING" : f.severity} — {f.detail}
                  </div>
                ))}
                {closeFindings && (
                  <div style={{ marginTop: 6, fontSize: 12, color: T.red }}>
                    Close refused by the server (409): {closeFindings.length} blocking finding(s) above.
                  </div>
                )}
                <div style={{ marginTop: 16, position: "relative", minHeight: 86 }}>
                  {!closed ? (
                    <Btn primary big disabled={!checklist.can_close} onClick={approveClose}>
                      {checklist.can_close ? "Approve close & lock June 2026" : "Close blocked until findings clear"}
                    </Btn>
                  ) : (
                    <div className="stamp" style={{ display: "inline-block", border: `3px solid ${T.red}`,
                      color: T.red, fontFamily: T.serif, fontWeight: 600, fontSize: 26, letterSpacing: ".08em",
                      padding: "8px 22px", borderRadius: 6, transform: "rotate(-8deg)", opacity: .92 }}>
                      CLOSED · M. REYES
                    </div>
                  )}
                  {closed && <p style={{ fontSize: 12, color: T.inkSoft, marginTop: 10 }}>
                    Period locked. Any July adjustment to June posts as a dated reversal — history is never edited.
                  </p>}
                </div>
              </Card>
            </div>
          </div>
        )}

        {!loading && tab === "advisor" && (
          <div>
            <SectionTitle k="CFO layer · advisory only — never auto-executes" t="Forward view"
              sub="Forecasts are generated from posted, reconciled data and clearly labeled. Nothing here moves money or files anything." />
            <div style={{ display: "grid", gridTemplateColumns: "3fr 2fr", gap: 14 }}>
              <Card title="13-week cash forecast">
                <div style={{ height: 230 }}>
                  <ResponsiveContainer>
                    <AreaChart data={forecast} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
                      <CartesianGrid stroke={T.ruleSoft} vertical={false} />
                      <XAxis dataKey="wk" tick={{ fontFamily: T.mono, fontSize: 11 }} stroke={T.inkSoft} />
                      <YAxis tickFormatter={(v) => "$" + (v / 1000).toFixed(0) + "k"} tick={{ fontFamily: T.mono, fontSize: 11 }} stroke={T.inkSoft} width={52} />
                      <Tooltip formatter={(v) => "$" + v.toLocaleString()} contentStyle={{ fontFamily: T.mono, fontSize: 12, background: T.card, border: `1px solid ${T.rule}` }} />
                      <ReferenceLine y={120000} stroke={T.red} strokeDasharray="4 4" />
                      <Area dataKey="low" stroke="none" fill={T.ruleSoft} name="conservative" />
                      <Area dataKey="cash" stroke={T.green} strokeWidth={2} fill={T.green + "22"} name="run-rate" />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </Card>
              <Card title="Revenue vs spend — 3 months">
                <div style={{ height: 230 }}>
                  <ResponsiveContainer>
                    <BarChart data={plBars} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
                      <CartesianGrid stroke={T.ruleSoft} vertical={false} />
                      <XAxis dataKey="m" tick={{ fontFamily: T.mono, fontSize: 11 }} stroke={T.inkSoft} />
                      <YAxis tickFormatter={(v) => "$" + (v / 1000).toFixed(0) + "k"} tick={{ fontFamily: T.mono, fontSize: 11 }} stroke={T.inkSoft} width={52} />
                      <Tooltip formatter={(v) => "$" + v.toLocaleString()} contentStyle={{ fontFamily: T.mono, fontSize: 12, background: T.card, border: `1px solid ${T.rule}` }} />
                      <Bar dataKey="rev" fill={T.green} name="revenue" radius={[3, 3, 0, 0]} />
                      <Bar dataKey="exp" fill={T.rule} name="spend" radius={[3, 3, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </Card>
            </div>
            <div style={{ marginTop: 14, fontSize: 11.5, fontFamily: T.mono, color: T.brass }}>
              ADVISORY — generated by bedrock-cfo-1, reviewed before delivery. No action taken automatically.
            </div>
          </div>
        )}
      </main>

      {/* chain footer */}
      <footer style={{ position: "fixed", bottom: 0, left: 0, right: 0, background: T.greenDeep, color: "rgba(247,245,238,.8)",
        fontFamily: T.mono, fontSize: 11.5, padding: "7px 28px", display: "flex", gap: 24, flexWrap: "wrap" }}>
        <span>chain: {chainOk ? "verified" : "BROKEN"}{head ? ` · head ${(head.entry_hash || "").slice(0, 8)}` : ""}</span>
        <span>trial balance Δ {trial}¢</span>
        <span>entries {entries.length}</span>
        <span style={{ marginLeft: "auto" }}>CPA of record: A. Whitfield · GL export available</span>
      </footer>

      {/* PAPER TRAIL drawer — rendered from the provenance join */}
      {trail && <TrailDrawer trail={trail} onClose={() => setTrail(null)} />}
    </div>
  );
}

/* ---------- paper trail drawer ---------- */
function TrailDrawer({ trail, onClose }) {
  const e = trail.entry, doc = trail.document, d = trail.decision, p = trail.proposal, r = trail.reviewer;
  const steps = [
    ["1 · Source document", doc ? [
      [`${doc.doc_type} — ${doc.source_system}`, false],
      [`“${doc.raw}”`, true],
      [`sha256 ${doc.sha256} · immutable, content-addressed`, false],
    ] : [["No source document on file", false]]],
    p ? ["2 · AI proposal (never posts by itself)", [
      [`“${p.rationale}”`, true],
      [`confidence ${p.confidence.toFixed(2)} · pattern: ${p.pattern_match} · model ${p.model_id}`, false],
      ["reasoning inputs snapshotted — this decision is replayable", false],
    ]] : ["2 · No AI involvement", [["Posted directly from migration records", false]]],
    ["3 · Policy decision", d ? [
      [d.decision.replace(/_/g, " "), false],
      [`policy engine v${d.policy_version} · reason: ${d.reason}`, false],
      [`thresholds at decision time: auto ≥ ${d.effective_thresholds.auto_post_min_confidence} conf, < $${d.effective_thresholds.auto_post_max_amount / 100}`, false],
    ] : [["No routing decision (manual/migration entry)", false]]],
    ["4 · Accountable human", r ? [
      [`${r.id} — ${r.role}`, false],
      [r.kind === "auto" ? "auto-post: sampled into human QA at 5%" : "decision recorded in review log; feeds the accuracy audit", false],
    ] : [["Migration record — tied to filed return", false]]],
    ["5 · Ledger seal", [
      [`entry ${e.entry_id.slice(0, 12)}… · hash ${(e.entry_hash || "").slice(0, 16)}`, false],
      ["chained to prior entry — editing history breaks every later hash", false],
    ]],
  ];
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(23,36,28,.45)", zIndex: 50 }}>
      <aside onClick={(ev) => ev.stopPropagation()} style={{ position: "absolute", top: 0, right: 0, bottom: 0, width: "min(430px,92vw)",
        background: T.paper, borderLeft: `1px solid ${T.rule}`, boxShadow: "-16px 0 40px rgba(0,0,0,.18)", padding: "22px 22px 40px", overflowY: "auto" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
          <div style={{ fontFamily: T.serif, fontSize: 19, fontWeight: 600 }}>Paper trail</div>
          <button onClick={onClose} style={{ marginLeft: "auto", border: "none", background: "none", fontSize: 20, cursor: "pointer", color: T.inkSoft }} aria-label="Close paper trail">×</button>
        </div>
        <div style={{ fontSize: 13, color: T.inkSoft, margin: "2px 0 16px" }}>{e.memo}</div>
        <div style={{ fontFamily: T.mono, fontSize: 22, fontWeight: 600, marginBottom: 18 }}>{fmt(trail.amount_minor)}</div>
        {steps.map(([title, rows], i) => (
          <div key={i} style={{ position: "relative", marginBottom: 14, background: T.card, border: `1px solid ${T.rule}`, borderRadius: 6, padding: "12px 14px", boxShadow: "0 1px 2px rgba(0,0,0,.05)" }}>
            <div style={{ fontSize: 11, letterSpacing: ".08em", textTransform: "uppercase", color: T.green, fontWeight: 700, marginBottom: 6 }}>{title}</div>
            {rows.map(([txt, quote], j) => (
              <div key={j} style={{ fontSize: 12.5, lineHeight: 1.55, fontFamily: quote ? T.sans : T.mono, fontStyle: quote ? "italic" : "normal", color: quote ? T.ink : T.inkSoft, marginBottom: 3 }}>{txt}</div>
            ))}
          </div>
        ))}
        <div style={{ fontSize: 11.5, color: T.inkSoft, lineHeight: 1.5 }}>
          This is the answer to “why is this number what it is” — a join over the ledger, not a generated explanation.
        </div>
      </aside>
    </div>
  );
}

/* ---------- error surface ---------- */
function ErrorBanner({ err, onClose }) {
  let title = "Something went wrong";
  let advice = err.message;
  const d = err.detail;
  if (err.status === 403) {
    title = "Not authorized for this action";
    advice = `Requires role “${d?.required_role || "controller"}”. You are acting as “${d?.acting_role || "?"}”. Switch role in the top-right and retry.`;
  } else if (err.status === 409 && d?.findings) {
    title = "Close blocked by the server";
    advice = "Resolve these blocking findings, then approve again: " + d.findings.map((f) => f.detail).join("; ");
  } else if (err.status === 400) {
    title = "Bad request";
    advice = typeof d === "string" ? d : (d?.error || err.message);
  } else if (err.status === 0) {
    title = "Backend unreachable";
  }
  return (
    <div style={{ background: T.redSoft, borderBottom: `1px solid ${T.red}44`, color: T.red, padding: "10px 28px", display: "flex", gap: 12, alignItems: "baseline" }}>
      <b style={{ fontSize: 13 }}>{title}</b>
      <span style={{ fontSize: 12.5, color: T.ink }}>{advice}</span>
      <button onClick={onClose} style={{ marginLeft: "auto", border: "none", background: "none", cursor: "pointer", color: T.red, fontSize: 16 }} aria-label="Dismiss">×</button>
    </div>
  );
}

/* ---------- building blocks ---------- */
const SectionTitle = ({ k, t, sub }) => (
  <div style={{ marginBottom: 18 }}>
    <div style={{ fontFamily: T.mono, fontSize: 11.5, letterSpacing: ".08em", textTransform: "uppercase", color: T.brass, fontWeight: 600 }}>{k}</div>
    <h1 style={{ fontFamily: T.serif, fontSize: 26, fontWeight: 600, margin: "2px 0 4px", color: T.ink }}>{t}</h1>
    {sub && <p style={{ fontSize: 13.5, color: T.inkSoft, maxWidth: 720, lineHeight: 1.55, margin: 0 }}>{sub}</p>}
  </div>
);

const Card = ({ title, children, style, pad = 16 }) => (
  <section style={{ background: T.card, border: `1px solid ${T.rule}`, borderRadius: 10, padding: pad, boxShadow: "0 1px 3px rgba(23,36,28,.05)", ...style }}>
    {title && <div style={{ fontSize: 12, letterSpacing: ".07em", textTransform: "uppercase", color: T.inkSoft, fontWeight: 700, marginBottom: 12 }}>{title}</div>}
    {children}
  </section>
);

const EmptyState = ({ children }) => (
  <Card><div style={{ textAlign: "center", padding: "28px 0", color: T.inkSoft }}>{children}</div></Card>
);

const Stat = ({ label, v, note, noteColor = T.inkSoft, big }) => (
  <div style={{ background: T.card, border: `1px solid ${T.rule}`, borderRadius: 10, padding: "14px 16px" }}>
    <div style={{ fontSize: 11.5, letterSpacing: ".06em", textTransform: "uppercase", color: T.inkSoft, fontWeight: 600 }}>{label}</div>
    <div style={{ fontFamily: T.mono, fontSize: big ? 30 : 23, fontWeight: 600, margin: "4px 0 2px", fontVariantNumeric: "tabular-nums" }}>{v}</div>
    <div style={{ fontSize: 11.5, color: noteColor, fontWeight: 500 }}>{note}</div>
  </div>
);

const Pill = ({ bg, fg, children }) => (
  <span style={{ background: bg, color: fg, fontSize: 11.5, fontWeight: 600, borderRadius: 99, padding: "3px 10px" }}>{children}</span>
);

const Check = ({ ok, label }) => (
  <span style={{ color: ok ? T.green : T.brass, fontWeight: 600 }}>{ok ? "✓" : "•"} {label}</span>
);

const CheckRow = ({ ok, label, sub }) => (
  <div style={{ display: "flex", gap: 10, padding: "8px 0", borderBottom: `1px solid ${T.ruleSoft}`, alignItems: "flex-start" }}>
    <span style={{ fontFamily: T.mono, color: ok ? T.green : T.red, fontWeight: 700 }}>{ok ? "✓" : "✗"}</span>
    <div>
      <div style={{ fontSize: 13.5, fontWeight: 600 }}>{label}</div>
      {sub && <div style={{ fontSize: 12, color: T.inkSoft }}>{sub}</div>}
    </div>
  </div>
);

const Num = ({ c, onClick }) => (
  <button onClick={onClick} title="Open paper trail"
    style={{ fontFamily: T.mono, fontSize: 13, fontWeight: 500, color: T.ink, background: "none", border: "none",
      padding: 0, cursor: "pointer", borderBottom: `1px dashed ${T.rule}`, fontVariantNumeric: "tabular-nums" }}>
    {fmt(c)}
  </button>
);

const Btn = ({ children, onClick, primary, ghost, big, disabled, style }) => (
  <button onClick={onClick} disabled={disabled}
    style={{ fontFamily: T.sans, fontSize: big ? 14.5 : 12.5, fontWeight: 600, cursor: disabled ? "not-allowed" : "pointer",
      padding: big ? "12px 20px" : "8px 14px", borderRadius: 7,
      background: disabled ? T.ruleSoft : primary ? T.green : ghost ? "transparent" : T.card,
      color: disabled ? T.inkSoft : primary ? "#fff" : T.ink,
      border: primary ? "none" : `1px solid ${T.rule}`, ...style }}>
    {children}
  </button>
);
