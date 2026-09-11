"use client";
// The rails: what the latest tick line says about the tick's own health. The API carries no work
// orders, so the per-order violations live in logs/agent.jsonl and not here; what the tick line
// carries is the counts, and a count that should be zero and is not is a rail that fired.
import type { Tick } from "@/lib/types";
import { num } from "@/lib/format";

type Status = "pass" | "fail" | "pause" | "idle";
interface Rail { name: string; status: Status; detail: string }

const ICON: Record<Status, string> = { pass: "✅", fail: "❌", pause: "⏸", idle: "·" };

export function railsFor(t: Tick | null, pendingWrites: number): Rail[] {
  if (!t) return [];
  const f = t.fields;
  const rails: Rail[] = [];
  rails.push(t.error ? { name: "tick", status: "fail", detail: `${t.failed_stage ?? "?"}: ${t.error}` } : { name: "tick", status: "pass", detail: `#${t.tick} in ${num(t.duration_ms)} ms` });
  const skipped = f.skipped_upstreams ?? [];
  rails.push(skipped.length ? { name: "upstreams", status: "fail", detail: `backing off: ${skipped.join(", ")}` } : { name: "upstreams", status: "pass", detail: "all answered" });
  rails.push(f.herd_error ? { name: "herd", status: "fail", detail: String(f.herd_error) } : { name: "herd", status: f.herd_animals == null ? "idle" : "pass", detail: f.herd_animals == null ? "not swept" : `${num(f.herd_animals)} animals` });
  const shipped = f.work_orders_shipped ?? 0, rejected = f.work_orders_rejected ?? 0;
  rails.push(rejected ? { name: "orders", status: "fail", detail: `${rejected} rejected by a rail, ${shipped} shipped` } : { name: "orders", status: shipped ? "pass" : "idle", detail: shipped ? `${shipped} shipped` : "none this tick" });
  const esc = f.escalations ?? 0;
  rails.push(esc ? { name: "cascade", status: "pause", detail: `${esc} to Opus: ${(f.escalation_reasons ?? []).join(", ")}` } : { name: "cascade", status: "idle", detail: f.tier ? `tier ${f.tier}, ${f.tier1_orders ?? 0} local` : "no escalations" });
  const viol = f.shift_report_violations ?? [];
  const src = f.shift_report;
  rails.push(viol.length ? { name: "report", status: "fail", detail: `${src} with violations: ${viol.join(", ")}` } : { name: "report", status: src ? "pass" : "idle", detail: src ? `written by ${src}` : "none this tick" });
  const held = f.held ?? 0;
  rails.push(held ? { name: "held", status: "pause", detail: `${held} carried to the next tick` } : { name: "held", status: "pass", detail: "nothing carried" });
  // From GET /ops/gate, not the tick line: `writes_pending` is null on a tick that proposed nothing, and a pause planted by another process is still a pause.
  rails.push(pendingWrites ? { name: "gate", status: "pause", detail: `${pendingWrites} write${pendingWrites === 1 ? "" : "s"} waiting on a human` } : { name: "gate", status: "pass", detail: "no writes waiting" });
  return rails;
}

export function Rails({ tick, pendingWrites }: { tick: Tick | null; pendingWrites: number }) {
  const rails = railsFor(tick, pendingWrites);
  if (!rails.length) return <div className="dim">no tick yet</div>;
  return (
    <div className="rails">
      {rails.map((r) => (
        <div className={`rail ${r.status}`} key={r.name}>
          <span className="rail-icon">{ICON[r.status]}</span>
          <span className="rail-name">{r.name}</span>
          <span className="rail-detail">{r.detail}</span>
        </div>
      ))}
    </div>
  );
}
