# src/api - the read path

Arrives at **M8**. FastAPI, read-mostly: the window (M9) and a human on shift are the
only consumers. The one write is the gate decision, and that resumes a paused graph
rather than mutating the ranch directly.

## Envelope, matching the ranch APIs exactly

The four upstream services already established this shape and the whole system should
read consistently, so a human moving between them never has to re-learn anything.

```jsonc
{ "data": {} }                                                      // single resource
{ "data": [], "meta": { "count": 3, "limit": 100, "offset": 0 } }   // collection
```

`meta.count` is items **returned** (`len(data)`), not total matching rows.

## Errors, same shape everywhere

```json
{ "error": { "code": "INCIDENT_NOT_FOUND", "message": "...", "details": {} } }
```

Status split, copied deliberately: **400** unparsable · **404** not found · **409**
valid but conflicts with current state · **422** parsed but validation failed · **500**
unexpected. Most client mistakes are **422, not 400**. Validation errors name the
field: `details: { "field": "...", "reason": "..." }`.

## Routes

| Route | Notes |
| --- | --- |
| `GET /health` | Never touches the database. A liveness check that can fail for a database reason cannot distinguish "down" from "degraded". |
| `GET /ops/incidents` | Paginated, newest first by `last_seen_at`. `limit` default 100, max 500; `offset` default 0. Filters `status`, `owner`, `subject_type` (M7A put animals and sensors in one table, and the window wants them apart). A bad `status` or `owner` is 422 naming the field. |
| `GET /ops/report` | The latest shift report, from `sw_ops.shift_reports`. 404 `REPORT_NOT_FOUND` on an empty ledger. |
| `GET /ops/stream` | SSE. The window watches ticks land without polling. Polls `sw_ops.ticks` for rows newer than the last one sent, sends the latest row on connect so the window is never blank, honours `Last-Event-ID`. `?limit=N` closes the stream after N events, for a curl or a rail. |
| `GET /ops/gate` | The writes agents proposed and nobody has answered. **Added at M8**, a sixth route, with Scott's yes: the window cannot approve a pause it cannot see, and until then `python -m src.agent.gate list` was the only way to see one. Read-only, the CLI's `list` in an envelope. |
| `POST /ops/gate` | Approve or reject a paused write; resumes the checkpointed graph. **Bearer token required.** |

**`POST /ops/gate` is the CLI with an envelope around it.** Body `{audit_id, decision, reason}`.
`gate.decide()` does the work and the endpoint adds nothing: unknown id is 404 `GATE_NOT_FOUND`, an
already-decided pause is 409 `GATE_ALREADY_DECIDED` (a second answer is refused by name, never 200 with
a shrug), a reject with no reason is 422 naming `reason`. If the API ever needs a behaviour the CLI
cannot express, the fix goes in `gate.py` and the CLI gets it too.

## Who may approve a write over HTTP

Reads are open. The POST is a human saying yes to a real write against the deployed Care API, so
unauthenticated it is an approval anyone who can reach the port can make. `OPS_API_TOKEN` holds
`name:secret` pairs, comma-separated, at least one, each secret at least 16 characters. `--api`
refuses to start without one (exit 2, a config refusal, not a server that boots and approves
nothing). `decided_by` is the name the presented token maps to, never a field in the request body.
A missing or wrong token is 401 `UNAUTHORIZED` in the envelope with `WWW-Authenticate: Bearer`. The
loop never reads the setting.

`API_CORS_ORIGINS` is the allowed origins, comma-separated, **default empty**: M9's window on Vercel
is the one origin that needs it, and a wildcard in a default is a default that ships.

## Two rules

- **This API never calls the ranch APIs and never calls a model.** It reads `sw_ops`, and that
  sentence is the whole architecture. The orchestrator is the only thing that talks upstream, so a
  browser refresh can never spend a token or fire a sweep. What the API shows has to be in the
  ledger first: from M8 the loop writes every tick line to `sw_ops.ticks`, every shift report to
  `sw_ops.shift_reports`, and every gate receipt to `sw_ops.audit_receipts` beside the log line, so
  a separate process on a separate box sees exactly what the loop saw.
- Every request carries `X-Request-ID`: accept it if given, generate it if not, always
  echo it back.
