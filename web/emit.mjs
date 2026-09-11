// emit.mjs — the ONE seam between the agent-lab scripts and the UI.
//
// Each script keeps its exact human `console.log` output. Alongside it, it calls
// emit(evt), which prints ONE extra line: "@@" + JSON. The UI parses only the
// "@@" lines; a plain terminal shows them as harmless noise you can strip with
// `grep -v '^@@'`. This is deliberately plain .mjs + zero deps so the existing
// `node --env-file` scripts import it with NO build step.
//
// The event SHAPES are defined once, as a real discriminated union, in events.ts.
// This file only serializes; it does not validate. Keep them in sync.

export function emit(evt) {
  console.log("@@" + JSON.stringify(evt));
}

// --- tiny typed-ish helpers so call sites read as intent, not JSON plumbing ---

export function emitRunStart(rung, model, n, animals) {
  emit({ type: "run_start", rung, model, n, animals });
}

// Pass only the fields you have; the UI ignores the rest (II.1 has full/cumulative,
// II.4 has store, II.6 has peak+recalled).
export function emitTurn(fields) {
  emit({ type: "turn", ...fields });
}

export function emitVerdict(animal, verdict) {
  emit({ type: "verdict", animal, verdict });
}

export function emitRail(name, status, detail) {
  emit({ type: "rail", name, status, detail });
}

export function emitRunEnd(fields) {
  emit({ type: "run_end", ...fields });
}
