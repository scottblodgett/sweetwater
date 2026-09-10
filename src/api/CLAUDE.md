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
| `GET /ops/incidents` | Paginated, newest first. `limit` default 100, max 500; `offset` default 0. |
| `GET /ops/report` | The latest shift report. |
| `GET /ops/stream` | SSE. The window watches ticks land without polling. |
| `POST /ops/gate` | Approve or reject a paused write; resumes the checkpointed graph. |

## Two rules

- **This API never calls the ranch APIs.** It reads `sw_ops`. The orchestrator is the
  only thing that talks upstream, so a browser refresh can never spend tokens or fire a
  sweep.
- Every request carries `X-Request-ID`: accept it if given, generate it if not, always
  echo it back.
