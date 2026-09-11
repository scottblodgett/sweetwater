# web/ - the window (M9)

What the agents decided, on one page. The window reads the M8 read API and nothing else, and the
browser never holds the API token: Next.js route handlers proxy every call server-side.

The old UI (`agent-lab-ui`, landed here untouched as the first M9 commit) showed what the sensors
said. This one shows what the agents decided, from `sw_ops` through `/ops/*`.

## Run

```bash
cd web
npm install                   # Node 22; its own project, not a workspace
cp .env.example .env.local    # OPS_API_URL (default http://127.0.0.1:8000) and OPS_API_TOKEN
npm run dev                   # http://localhost:3000
```

`OPS_API_TOKEN` here is **one secret** from the API's `OPS_API_TOKEN` (the part after `name:`), not the
`name:secret` pair. The API maps it back to the name, and that name is `decided_by` on every gate
receipt this window produces. `.env*.local` is gitignored at the repo root and here.

The API has to be up: `python main.py --api` from the repo root, on the same ledger you want to
watch (`SW_OPS_TARGET=test` for the local one). With the API down every panel says so in a sentence
and the header says `reconnecting`; nothing is blank and nothing retries a write.

## The gate, in three commands

```bash
npx tsc --noEmit    # no type errors
npm run lint        # eslint-config-next, zero problems
npm run build       # next build succeeds
```

All three are in the root `CLAUDE.md` Commands block and re-run at every phase close. The Python gate
(`pytest`, `ruff`, `mypy`) does not know this directory exists, on purpose.

## The proxy: why the browser never calls the API

`POST /ops/gate` approves a real write on the deployed ranch and needs a bearer token. A token in
browser JavaScript is a token in every visitor's dev tools. So:

| Browser calls | Handler | Forwards to | Notes |
| --- | --- | --- | --- |
| `GET /api/ops/incidents?…` | `app/api/ops/[...path]/route.ts` | `GET $OPS_API_URL/ops/incidents?…` | query string verbatim |
| `GET /api/ops/report` | same | `GET /ops/report` | 404 `REPORT_NOT_FOUND` passes through; the panel renders it as "no shift report yet" |
| `GET /api/ops/stream` | same | `GET /ops/stream` | the SSE body is piped, not buffered; `Last-Event-ID` forwarded, so a reconnect resumes |
| `GET /api/ops/gate` | same | `GET /ops/gate` | |
| `POST /api/ops/gate` | same | `POST /ops/gate` + `Authorization: Bearer` | the only call that carries the token; 503 `WINDOW_NOT_CONFIGURED` if the window has none |
| `GET /api/health` | `app/api/health/route.ts` | `GET /health` | |

`lib/proxy.ts` is the whole thing: it forwards `X-Request-ID` if the browser sent one, mints one if not,
and echoes the API's back, so one id names a request in both logs. An unreachable API is a 502
`API_UNREACHABLE` in the same `{error: {code, message, details}}` envelope, so the client has one error
shape to render. Anything under `/api/ops/` that is not one of the four paths is a 404 here, never a
probe upstream. `API_CORS_ORIGINS` on the API is moot for this window: same origin, always.

`lib/proxy.ts` runs only in route handlers. Nothing under `components/` or `lib/client.ts` may import it.

## The panels, and what each reads

| Panel | Reads | What it shows |
| --- | --- | --- |
| Token gauge | `/ops/stream`, one point per tick row | input and output tokens per tick on one chart, `cost_usd` per tick on a second beneath it on the same x-axis. Two charts and not a dual axis: tokens and dollars never share a scale. Cost seen this session in the legend |
| Rails | the latest tick row's `fields`, plus the pending count from `/ops/gate` | eight chips: tick, upstreams, herd, orders, cascade, report, held, gate. The API carries no work orders, so per-order violations stay in `logs/agent.jsonl`; what the line carries is counts, and a count that should be zero and is not is a rail that fired |
| Shift report | `/ops/report` | `source` first (`code` is the fallback having shipped), headline, situation, priorities, `linked`, escalations, violations, and who wrote it |
| Gate | `/ops/gate`, decisions through `POST` | every pending write with agent, tool, args, age. **Approve asks once**, naming the tool and args, because it performs the write on the deployed ranch. Reject needs a reason. A pause answered this session stays on screen with its buttons, so a second answer shows the API's 409 as a sentence naming the earlier decision |
| Incidents | `/ops/incidents?limit=100` with `status`, `owner`, `subject_type` filters | newest first: severity, status, key, owner, summary and last value |
| Ranch map | nothing yet | a placeholder that says why: coordinates live in the ranch's catalog, the API never calls the ranch, and neither does this window. Waits on `docs/issues.md` #21 |

The stream is the clock. Every tick that lands refreshes the feed, the report, and the gate; the gate
also polls every 20s, because a decision made at the CLI does not land a tick. `EventSource` reconnects
on its own and the proxy forwards its `Last-Event-ID`.

## Files

| File | Role |
| --- | --- |
| `app/layout.tsx`, `app/page.tsx` | the shell and the one page; the stream hook and the three loaders live in `page.tsx` |
| `app/globals.css` | the light-mode stylesheet, carried over from `agent-lab-ui/style.css` and extended |
| `app/api/ops/[...path]/route.ts`, `app/api/health/route.ts` | the proxy's route handlers |
| `lib/proxy.ts` | server only: forward one request, token, request id, streaming body |
| `lib/client.ts` | browser only: same-origin fetch, `OpsError` with the sentence a human reads |
| `lib/types.ts` | the wire shapes, mirroring `src/api/schemas.py` by hand |
| `lib/format.ts` | times, dollars, counts, ages |
| `components/` | `Gauge`, `Rails`, `Summary`, `Gate`, `Feed`, `MapPlaceholder` |

## What carried over from `agent-lab-ui`, and what did not

Carried: the panel set (gauge, feed, rails, summary card, interactive gate), the grid layout, the
light-mode styling, the `EventSource` client pattern, the approve/reject UX. Left behind, because
none of it has a job here: `emit.mjs` and the `@@` line seam, `events.ts`, `server.ts` and the run
spawner, replay, the model selector, and the hand-rolled canvas gauge, which the old README itself said
was the one piece a port should replace with a chart library.

## Deploying

Not yet. Vercel cannot reach `127.0.0.1`, and where the loop and API run is `docs/issues.md` #5. When
that lands on a reachable host, set `OPS_API_URL` and `OPS_API_TOKEN` in the Vercel project's
environment (server-side, never `NEXT_PUBLIC_`), and the plan's "three ticks land without a refresh at
the Vercel URL" is the check. It is owed, not faked with a tunnel.
