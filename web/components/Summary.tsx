"use client";
// The summary card: the latest shift report from /ops/report. `source` first: `code` is the fallback
// having shipped, and `violations` beside it is what tells a calm tick from a model that failed.
import type { ShiftReport } from "@/lib/types";
import { hms, num, shortRun } from "@/lib/format";

export function Summary({ report, error }: { report: ShiftReport | null; error: string | null }) {
  if (error) return <div className="banner">{error}</div>;
  if (!report) return <div className="dim">no shift report yet</div>;
  const r = report;
  return (
    <>
      <div className="summary-head">
        <span className={`tag src-${r.source}`}>{r.source}</span>
        <span className="dim">tick {r.tick} · run {shortRun(r.run_id)} · {hms(r.at)}</span>
        {r.violations.length ? <span className="tag sev-critical">{r.violations.length} violation{r.violations.length === 1 ? "" : "s"}</span> : null}
      </div>
      <div className="headline">{r.headline}</div>
      <div className="summary-lesson">{r.situation}</div>
      {r.priorities.length ? <ol className="priorities">{r.priorities.map((p, i) => <li key={i}>{p}</li>)}</ol> : null}
      <dl className="summary-stats">
        <dt>linked</dt><dd>{r.linked.length ? r.linked.map((k) => <code key={k} className="key">{k}</code>) : <span className="dim">none</span>}</dd>
        <dt>escalations</dt><dd>{r.escalations.length ? r.escalations.join("; ") : <span className="dim">none</span>}</dd>
        <dt>worlds</dt><dd>{r.worlds.join(", ") || <span className="dim">none</span>}</dd>
        <dt>work orders</dt><dd>{r.work_orders} over {r.incident_keys.length} incident{r.incident_keys.length === 1 ? "" : "s"}</dd>
        <dt>violations</dt><dd>{r.violations.length ? r.violations.join("; ") : <span className="dim">none</span>}</dd>
        <dt>written by</dt><dd>{r.source === "model" ? `${r.provider} ${r.model} · ${num(r.input_tokens)} in, ${num(r.output_tokens)} out · ${num(r.latency_ms)} ms · ${r.finish_reason}` : "code, the fallback"}</dd>
      </dl>
    </>
  );
}
