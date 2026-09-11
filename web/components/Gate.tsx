"use client";
// The gate: the writes agents proposed and nobody has answered, from GET /ops/gate, answered through
// POST /ops/gate behind the proxy. A model never performs a write; a human says yes or no here.
// A pause answered this session stays on screen with its buttons, so a second answer shows the
// API's 409 as a sentence rather than vanishing, which is what the CLI does too.
import { useState } from "react";
import type { Decision, GateDecision, PendingWrite } from "@/lib/types";
import { describe, postJson } from "@/lib/client";
import { age, hms, shortRun } from "@/lib/format";

export interface Answered { audit_id: string; pending: PendingWrite; result: GateDecision | null; error: string | null; at: number }

interface Props { pending: PendingWrite[]; answered: Answered[]; onAnswered: (a: Answered) => void; error: string | null }

function Args({ args }: { args: Record<string, unknown> }) {
  return <pre className="args">{JSON.stringify(args, null, 1).replace(/\n\s*/g, " ")}</pre>;
}

function Card({ p, prior, onAnswered }: { p: PendingWrite; prior: Answered | null; onAnswered: (a: Answered) => void }) {
  const [reason, setReason] = useState(prior?.result?.reason ?? "");
  const [busy, setBusy] = useState(false);
  const decide = async (decision: Decision) => {
    if (decision === "reject" && !reason.trim()) { onAnswered({ audit_id: p.audit_id, pending: p, result: null, error: "a reject needs a reason; write one in the box first", at: Date.now() }); return; }
    // An approve performs the write on the deployed ranch, and the gate is the belt: the one action in this window that is hard to reverse gets one confirm naming what it does. A reject gets none.
    if (decision === "approve" && !window.confirm(`Approve ${p.tool} on ${p.incident_key}?\n\n${JSON.stringify(p.args)}\n\nThis performs the write on the deployed ranch.`)) return;
    setBusy(true);
    try {
      const out = await postJson<{ data: GateDecision }>("/api/ops/gate", { audit_id: p.audit_id, decision, reason: reason.trim() });
      onAnswered({ audit_id: p.audit_id, pending: p, result: out.data, error: null, at: Date.now() });
    } catch (err) {
      onAnswered({ audit_id: p.audit_id, pending: p, result: prior?.result ?? null, error: describe(err), at: Date.now() });
    } finally {
      setBusy(false);
    }
  };
  const cls = prior?.error ? "rejected" : prior?.result ? (prior.result.decision === "approve" ? "approved" : "rejected") : "paused";
  return (
    <div className={`gate ${cls}`}>
      <div className="gate-head"><b>{prior?.result ? (prior.result.decision === "approve" ? "✅ approved" : "⛔ rejected") : "⏸ paused"}</b> <span className="dim">{p.agent} proposes {p.tool} on {p.incident_key}</span></div>
      <Args args={p.args} />
      <div className="dim gate-meta">audit {p.audit_id} · tick {p.tick} · run {shortRun(p.run_id)} · proposed {hms(p.proposed_at)} · waiting {age(p.age_s)}</div>
      {prior?.result ? <div className="gate-result">{prior.result.decided_by} said {prior.result.decision} at {hms(prior.result.decided_at)} after {age(Math.round(prior.result.latency_to_decision_ms / 1000))}{prior.result.reason ? `: ${prior.result.reason}` : ""}. Result: {prior.result.result}{prior.result.upstream ? ` (${prior.result.upstream})` : ""}.</div> : null}
      {prior?.error ? <div className="gate-error">{prior.error}</div> : null}
      <div className="gate-buttons">
        <button className="approve" disabled={busy} onClick={() => decide("approve")}>approve</button>
        <button className="reject" disabled={busy} onClick={() => decide("reject")}>reject</button>
        <input className="reason" placeholder="reason (required to reject)" value={reason} onChange={(e) => setReason(e.target.value)} disabled={busy} />
      </div>
    </div>
  );
}

export function Gate({ pending, answered, onAnswered, error }: Props) {
  const byId = new Map(answered.map((a) => [a.audit_id, a]));
  const pendingIds = new Set(pending.map((p) => p.audit_id));
  const decided = answered.filter((a) => !pendingIds.has(a.audit_id)).sort((a, b) => b.at - a.at);
  return (
    <>
      {error ? <div className="banner">{error}</div> : null}
      {pending.length === 0 && decided.length === 0 && !error ? <div className="gate idle">no pending approval</div> : null}
      {pending.map((p) => <Card key={p.audit_id} p={p} prior={byId.get(p.audit_id) ?? null} onAnswered={onAnswered} />)}
      {decided.length ? <div className="panel-title decided-title">ANSWERED THIS SESSION</div> : null}
      {decided.map((a) => <Card key={a.audit_id} p={a.pending} prior={a} onAnswered={onAnswered} />)}
    </>
  );
}
