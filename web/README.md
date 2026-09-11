# agent-lab-ui

A localhost **mission-control dashboard** for watching the Phase II agent-lab runs.
The scripts only print to a terminal, which hides the *shape over time* that is the
whole Phase-II lesson (tokens climb in II.1, go flat in II.2). This shows that shape:
a live **token-gauge** line chart plus a scrolling **verdict feed**.

Full design brief: [`docs/agent-lab-ui-plan.md`](../docs/agent-lab-ui-plan.md).

> **Status: built.** Shipped: the `emit()` seam, the `RunEvent` schema, the server (live
> spawn + replay over SSE), and the full panel set — token gauge, verdict feed, **rails**,
> a **summary card** (the end-of-run footer, promoted out of the raw console), and an
> **interactive gate** with real approve/reject buttons that pipe the decision to the
> child's stdin. Six rungs are wired — **II.1** (`sweep-loop`), **II.2** (`truncate-loop`),
> **II.3** (`compress-loop`), **II.4** (`memory-loop`), **II.6** (`ii6-loop`, gated), and
> **II.7** (`gate-demo`, the real keyboard gate) — plus a **model selector** (qwen2.5 →
> qwen3.5 → kimi → opus, worst→best, via `agent-lab/model.mjs`). The one spec item still
> open is the gauge→transcript drill-down drawer.

## Run

```bash
cd agent-lab-ui
npm install          # tsx + typescript + @types/node (standalone, not a workspace)
npm run dev          # → http://localhost:4500   (PORT=4599 npm run dev to change)
```

Then open the URL and use the footer controls:

- **Replay** (no model needed): pick a `*.run.txt` capture and hit ▶ run. Note: only
  captures produced *after* `emit()` landed carry the machine-readable `@@` lines the
  gauge needs — older captures replay into the raw-console pane only (they degrade
  gracefully, no crash). Re-capture with the command below.
- **Live**: pick a rung, a **model** (the selector — qwen2.5/qwen3.5 need a local Ollama
  up; kimi needs a funded Moonshot key; opus needs AWS creds for Bedrock), set **N** small
  (e.g. 10) so it's watchable, hit ▶ run, and watch `sent` climb in II.1 vs stay flat
  (while `full` climbs) in II.2 — the money shot. Every rung now goes through **one shared
  LangChain actor** (`agent-lab/model.mjs`), so the same run works on any of the four
  models; II.7 (the gate) ignores the model entirely — it has no reasoner.

## The `emit()` seam

The scripts keep their exact terminal output. Alongside each meaningful `console.log`,
they call `emit(evt)` (`emit.mjs`), which prints one extra line: `@@` + JSON. A plain
terminal shows those as harmless noise; strip them with `grep -v '^@@'`. The UI parses
*only* the `@@` lines. This is why the terminal experience is unchanged and the UI needs
no brittle regex over the three different pretty-print formats.

The event shapes are a TypeScript discriminated union in **`events.ts`** — the single
reusable artifact that survives a future Next.js migration. `emit.mjs` stays plain `.mjs`
so the `node`-run scripts import it with zero build step.

## Re-capturing a replay file

```bash
# from repo root, with Ollama up:
node agent-lab/sweep-loop.mjs   > agent-lab/sweep-loop.run.txt
node agent-lab/truncate-loop.mjs > agent-lab/truncate-loop.run.txt
```

The capture now contains both the human lines and the `@@` events, so replay populates
the gauge and verdict feed exactly like a live run.

## Architecture note (why a Next.js port later is a wrapper swap, not a rewrite)

All server capability lives in **exported functions** in `server.ts`
(`listRuns`, `startLiveStream`, `startReplayStream`, `sendGateDecision`). The Node `http`
handler is a thin router that only parses the request and calls one of them — no business
logic inline. A Next.js route handler would import the same functions verbatim. The one
piece a Next.js version *would* rewrite is the hand-rolled canvas gauge in `app.js`
(swapped for a real chart lib); everything else ports directly.

## Files

| File           | Role                                                                 |
| -------------- | -------------------------------------------------------------------- |
| `emit.mjs`     | The one file the agent-lab scripts import. Prints `@@`-JSON lines.   |
| `events.ts`    | `RunEvent` union + `parseEventLine()`. The reusable contract.        |
| `server.ts`    | `tsx`-run dev server: exported capabilities + thin `http`/SSE router.|
| `index.html`   | Dashboard layout.                                                    |
| `app.js`       | Client: EventSource → panels; hand-rolled canvas gauge (plain JS).   |
| `style.css`    | Styling.                                                             |
