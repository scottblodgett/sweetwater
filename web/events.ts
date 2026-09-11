// events.ts — the RunEvent contract between the agent-lab scripts and the UI.
//
// This discriminated union is the single most important artifact in agent-lab-ui:
// it is the reusable ~60% that survives any future framework swap (a Next.js route
// handler would import this file verbatim). The scripts emit these shapes via
// emit.mjs; the server and client both parse against this type so the schema is
// defined exactly once.
//
// The scripts emit only the fields they have — optional fields are genuinely
// absent, not null. The UI must tolerate unknown/missing fields (forward-compat).

export type Rail = "gauge" | "citations" | "reachback" | "gate";
export type RailStatus = "pass" | "fail" | "pending" | "pause";

export type RunEvent =
  | { type: "run_start"; rung: string; model: string; n: number; animals: number }
  | {
      type: "turn";
      turn: number;
      animal: string;
      sent: number;
      full?: number; // II.2: what an unbounded transcript WOULD have sent (climbs)
      proc?: number; // model's own prompt_eval_count (prefix-cache lesson)
      ms?: number;
      cumulative?: number; // II.1: running total sent across the whole sweep
      peak?: number; // II.6
      store?: number; // II.4/II.6: size of the externalized fact store
      recalled?: boolean; // II.6: [order recalled] — a planted fact was reached back
    }
  | { type: "verdict"; animal: string; verdict: string }
  | { type: "rail"; name: Rail; status: RailStatus; detail: string }
  | { type: "gate_pause"; animal: string; proposed: string }
  | { type: "gate_resume"; animal: string; decision: "approve" | "reject"; reason?: string }
  | {
      type: "run_end";
      swept?: number;
      roundTrips?: number;
      cumulative?: number;
      // The end-of-run summary footer, promoted out of the raw console so it is
      // always visible: `stats` are the label/value lines each rung prints, and
      // `lesson` is that rung's one-line takeaway ("the climbing line IS the
      // deliverable"). Both optional — older captures won't have them.
      stats?: { label: string; value: string }[];
      lesson?: string;
    }
  | { type: "log"; line: string }; // server-synthesized from non-@@ stdout

/**
 * Parse one line of a script's stdout into a RunEvent.
 * - An "@@" line is a machine event: parse the JSON after the marker.
 * - Any other non-blank line is wrapped as { type: "log" } for the raw pane.
 * - A blank line returns null (nothing to render).
 * Never throws: a malformed "@@" line degrades to a log line.
 */
export function parseEventLine(line: string): RunEvent | null {
  if (line.trim() === "") return null;
  if (line.startsWith("@@")) {
    try {
      const evt = JSON.parse(line.slice(2)) as RunEvent;
      if (evt && typeof (evt as { type?: unknown }).type === "string") return evt;
    } catch {
      // fall through — treat a broken marker line as raw output
    }
    return { type: "log", line };
  }
  return { type: "log", line };
}
