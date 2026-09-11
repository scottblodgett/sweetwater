// The server side of the window. Every browser call to /api/ops/* lands here and is forwarded to the
// M8 API with OPS_API_URL and OPS_API_TOKEN read from the server environment, so the token is never
// in a byte the browser receives. X-Request-ID is forwarded if the browser sent one and minted if not,
// and the upstream's echo comes back on the response, so one id names a request across both logs.
//
// This file runs only in route handlers. Nothing in components/ or lib/client.ts may import it.

import { randomUUID } from "node:crypto";

export const REQUEST_ID = "X-Request-ID";
const HOP_BY_HOP = new Set(["connection", "keep-alive", "transfer-encoding", "content-length", "content-encoding"]);

export function apiBase(): string {
  return (process.env.OPS_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
}

function envelope(status: number, code: string, message: string, requestId: string, details: Record<string, unknown> = {}): Response {
  return Response.json({ error: { code, message, details } }, { status, headers: { [REQUEST_ID]: requestId, "Cache-Control": "no-store" } });
}

/** Forward one request. `path` is the upstream path (`/ops/incidents`); the query string is passed through verbatim. */
export async function forward(req: Request, path: string, opts: { auth?: boolean } = {}): Promise<Response> {
  const requestId = req.headers.get(REQUEST_ID) ?? randomUUID();
  const url = new URL(req.url);
  const target = `${apiBase()}${path}${url.search}`;
  const headers = new Headers({ [REQUEST_ID]: requestId, Accept: req.headers.get("accept") ?? "application/json" });
  const lastEventId = req.headers.get("last-event-id");
  if (lastEventId) headers.set("Last-Event-ID", lastEventId);
  if (req.method === "POST") headers.set("Content-Type", "application/json");
  if (opts.auth) {
    const token = process.env.OPS_API_TOKEN?.trim();
    if (!token) return envelope(503, "WINDOW_NOT_CONFIGURED", "OPS_API_TOKEN is not set in the window's server environment; the gate cannot decide", requestId);
    headers.set("Authorization", `Bearer ${token}`);
  }

  let upstream: Response;
  try {
    upstream = await fetch(target, { method: req.method, headers, body: req.method === "POST" ? await req.text() : undefined, signal: req.signal, cache: "no-store", redirect: "manual" });
  } catch (err) {
    if (req.signal.aborted) return new Response(null, { status: 499 });
    return envelope(502, "API_UNREACHABLE", `the ops API at ${apiBase()} did not answer: ${err instanceof Error ? err.message : String(err)}`, requestId);
  }

  const out = new Headers();
  upstream.headers.forEach((v, k) => { if (!HOP_BY_HOP.has(k.toLowerCase())) out.set(k, v); });
  out.set(REQUEST_ID, upstream.headers.get(REQUEST_ID) ?? requestId);
  out.set("Cache-Control", "no-store");
  // Bodies pass through as streams: a JSON envelope is one chunk, the SSE stream is many, and neither is buffered here.
  return new Response(upstream.body, { status: upstream.status, headers: out });
}
