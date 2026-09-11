// server.ts — the agent-lab-ui dev server. Run with:  tsx agent-lab-ui/server.ts
//
// DESIGN RULE (the thing that keeps a later Next.js migration a wrapper swap, not a
// rewrite): ALL capability lives in the exported functions below. The http handler at
// the bottom is a thin router that does nothing but parse the request and call one of
// them. A Next.js route handler could import these functions verbatim.
//
// Session 1 scope: listRuns(), startLiveStream(), startReplayStream(). The gate
// (sendGateDecision) and rails/drill-down come in Session 2.

import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createInterface } from "node:readline";
import { readFile, readdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join, extname } from "node:path";
import { parseEventLine, type RunEvent } from "./events.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const AGENT_LAB = join(HERE, "..", "agent-lab");
const PORT = Number(process.env.PORT ?? 4500);

// ---- the rung map: rung id -> script + which env var carries the sweep size ----
// Keeping it as data (not code) means the UI's rung dropdown is generated from one source.
// `nEnv` differs by script: sweep/truncate read SWEEP_N; compress/memory/ii6 read N.
// `gate: true` marks a rung whose interactive gate the server drives via stdin.
type RungDef = { script: string; nEnv?: string; gate?: boolean };
const RUNGS: Record<string, RungDef> = {
  "II.1": { script: "sweep-loop.mjs", nEnv: "SWEEP_N" },
  "II.2": { script: "truncate-loop.mjs", nEnv: "SWEEP_N" },
  "II.3": { script: "compress-loop.mjs", nEnv: "N" },
  "II.4": { script: "memory-loop.mjs", nEnv: "N" },
  "II.6": { script: "ii6-loop.mjs", nEnv: "N", gate: true },
  "II.7": { script: "gate-demo.mjs", gate: true },
};

// The actor choices the UI offers (Scott's worst→best comparison). Passed as PROVIDER.
export const PROVIDERS = ["qwen2.5", "qwen3.5", "kimi", "opus"];

// ---- a live stream handle: the child + a way to feed its stdin (gate, Session 2) ----
export type StreamHandle = {
  child: ChildProcessWithoutNullStreams;
  writeStdin: (line: string) => void;
};

// A callback the transport (SSE) supplies; each parsed event is pushed to it.
export type EventSink = (evt: RunEvent) => void;

// Live children indexed by pid, so a later POST /gate can find the running child and
// write the human's decision to its stdin. Populated by the /stream live handler.
export const liveHandles = new Map<number, StreamHandle>();

// =========================================================================
// EXPORTED CAPABILITIES — the reusable core. No http types leak in here.
// =========================================================================

/** List replayable captures + the rung map, for the UI's controls. */
export async function listRuns(): Promise<{
  rungs: Array<{ id: string; script: string; gate: boolean }>;
  providers: string[];
  captures: string[];
}> {
  let captures: string[] = [];
  try {
    const files = await readdir(AGENT_LAB);
    captures = files.filter((f) => f.endsWith(".run.txt")).sort();
  } catch {
    captures = [];
  }
  const rungs = Object.entries(RUNGS).map(([id, def]) => ({ id, script: def.script, gate: def.gate ?? false }));
  return { rungs, providers: PROVIDERS, captures };
}

/**
 * Spawn a rung's script and stream its parsed events to `sink`.
 * `n` (optional) overrides the sweep size via SWEEP_N — small N = watchable.
 * Returns a handle so callers can feed stdin (gate) or kill the child.
 */
export function startLiveStream(
  rung: string,
  sink: EventSink,
  opts: { n?: number; provider?: string } = {}
): StreamHandle {
  const def = RUNGS[rung];
  if (!def) throw new Error(`unknown rung: ${rung}`);

  const env: Record<string, string | undefined> = { ...process.env };
  if (opts.n != null && def.nEnv) env[def.nEnv] = String(opts.n);
  if (opts.provider) env.PROVIDER = opts.provider;
  if (def.gate) env.GATE_MODE = "interactive"; // the browser button pipes the decision to stdin

  // .mjs scripts run under bare node; a future .ts script would use tsx. Decide by ext.
  const runner = extname(def.script) === ".ts" ? "tsx" : "node";
  const child = spawn(runner, [join(AGENT_LAB, def.script)], {
    cwd: join(HERE, ".."), // repo root — scripts import ../agent-lab-ui/emit.mjs relative to agent-lab/
    env,
  }) as ChildProcessWithoutNullStreams;

  pipeLines(child.stdout, sink);
  pipeLines(child.stderr, sink); // stderr lines surface in the raw pane too
  child.on("close", (code) => sink({ type: "log", line: `[process exited: ${code}]` }));
  child.on("error", (err) => sink({ type: "log", line: `[spawn error: ${err.message}]` }));

  return { child, writeStdin: (line) => child.stdin.write(line.endsWith("\n") ? line : line + "\n") };
}

/**
 * Replay a captured *.run.txt: read it, emit its lines on a timer over the same sink.
 * Old captures (pre-emit) have no @@ lines — they degrade to log-only, no crash.
 */
export async function startReplayStream(
  file: string,
  sink: EventSink,
  opts: { intervalMs?: number; onDone?: () => void } = {}
): Promise<{ stop: () => void }> {
  // guard against path traversal — only a bare filename in agent-lab/ is allowed
  if (file.includes("/") || file.includes("\\") || file.includes("..")) {
    throw new Error(`invalid capture file: ${file}`);
  }
  const text = await readFile(join(AGENT_LAB, file), "utf8");
  const lines = text.split(/\r?\n/);
  const interval = opts.intervalMs ?? 120;

  // If a capture predates emit() it has NO @@ lines, so the gauge would sit empty and the
  // whole replay looks broken. Detect that and tell the client up front, so it can show a
  // "no machine events — raw only, re-capture to populate the gauge" hint instead of a
  // silent dead panel.
  const machineLines = lines.filter((l) => l.startsWith("@@")).length;
  sink({ type: "log", line: `[replay ${file}: ${lines.length} lines, ${machineLines} @@ events]` });

  let i = 0;
  let timer: ReturnType<typeof setInterval> | null = null;
  const stop = () => {
    if (timer) clearInterval(timer);
    timer = null;
  };
  timer = setInterval(() => {
    if (i >= lines.length) {
      stop();
      opts.onDone?.();
      return;
    }
    const evt = parseEventLine(lines[i++] ?? "");
    if (evt) sink(evt);
  }, interval);

  return { stop };
}

/**
 * Feed a gate decision to a live child's stdin. The gate scripts read a line and treat
 * anything starting with "a" as approve. `pid` addresses which live run (there's usually
 * one). Returns false if no live child matches (already exited / wrong pid).
 */
export function sendGateDecision(pid: number, decision: "approve" | "reject"): boolean {
  const handle = liveHandles.get(pid);
  if (!handle) return false;
  handle.writeStdin(decision);
  return true;
}

// ---- helper: read a stream line-by-line, parse, push to sink ----
function pipeLines(stream: NodeJS.ReadableStream, sink: EventSink): void {
  const rl = createInterface({ input: stream, crlfDelay: Infinity });
  rl.on("line", (line) => {
    const evt = parseEventLine(line);
    if (evt) sink(evt);
  });
}

// =========================================================================
// HTTP TRANSPORT — thin router. Parses the request, calls a function above,
// pipes RunEvents out as SSE. No business logic lives here.
// =========================================================================

function sseInit(res: ServerResponse): EventSink {
  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache, no-store",
    Connection: "keep-alive",
  });
  return (evt: RunEvent) => res.write(`data: ${JSON.stringify(evt)}\n\n`);
}

const CONTENT_TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
};

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve) => {
    let data = "";
    req.on("data", (chunk) => (data += chunk));
    req.on("end", () => resolve(data));
  });
}

async function serveStatic(res: ServerResponse, name: string): Promise<void> {
  try {
    const body = await readFile(join(HERE, name));
    res.writeHead(200, { "Content-Type": CONTENT_TYPES[extname(name)] ?? "application/octet-stream" });
    res.end(body);
  } catch {
    res.writeHead(404).end("not found");
  }
}

const server = createServer(async (req: IncomingMessage, res: ServerResponse) => {
  const url = new URL(req.url ?? "/", `http://localhost:${PORT}`);
  const path = url.pathname;

  try {
    if (path === "/" || path === "/index.html") return void (await serveStatic(res, "index.html"));
    if (path === "/app.js") return void (await serveStatic(res, "app.js"));
    if (path === "/style.css") return void (await serveStatic(res, "style.css"));

    if (path === "/runs") {
      const runs = await listRuns();
      res.writeHead(200, { "Content-Type": "application/json" });
      return void res.end(JSON.stringify(runs));
    }

    if (path === "/stream") {
      const mode = url.searchParams.get("mode");
      const sink = sseInit(res);
      if (mode === "live") {
        const rung = url.searchParams.get("rung") ?? "II.1";
        const nParam = url.searchParams.get("n");
        const provider = url.searchParams.get("provider") ?? undefined;
        const handle = startLiveStream(rung, sink, { n: nParam ? Number(nParam) : undefined, provider });
        const pid = handle.child.pid ?? -1;
        liveHandles.set(pid, handle);
        // tell the client its run's pid so the gate button can target POST /gate
        sink({ type: "log", line: `[live pid ${pid}]` });
        // When the child exits, END the SSE response. Without this the connection stays
        // open, the browser's EventSource.onerror never fires, and the ▶ button is never
        // re-enabled (the replay path already does this via onDone → res.end()).
        handle.child.on("close", () => {
          liveHandles.delete(pid);
          // Small drain delay: `close` can fire before readline has flushed the last
          // stdout line, and that line is often the run_end summary. Give it a tick.
          setTimeout(() => res.end(), 50);
        });
        req.on("close", () => {
          liveHandles.delete(pid);
          handle.child.kill();
        });
      } else if (mode === "replay") {
        const file = url.searchParams.get("file") ?? "";
        const { stop } = await startReplayStream(file, sink, {
          onDone: () => {
            sink({ type: "log", line: "[replay complete]" });
            res.end();
          },
        });
        req.on("close", stop);
      } else {
        sink({ type: "log", line: `[unknown stream mode: ${mode}]` });
        res.end();
      }
      return;
    }

    if (path === "/gate" && req.method === "POST") {
      const body = await readBody(req);
      const { pid, decision } = JSON.parse(body || "{}") as { pid?: number; decision?: string };
      const ok =
        typeof pid === "number" && (decision === "approve" || decision === "reject")
          ? sendGateDecision(pid, decision)
          : false;
      res.writeHead(ok ? 200 : 409, { "Content-Type": "application/json" });
      return void res.end(JSON.stringify({ ok }));
    }

    res.writeHead(404).end("not found");
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    if (!res.headersSent) res.writeHead(500, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: msg }));
  }
});

server.listen(PORT, () => {
  console.log(`agent-lab-ui → http://localhost:${PORT}`);
});
