"""The read API. Arrives at M8.

`/ops/incidents`, `/ops/report`, `/ops/stream` (SSE), `/ops/gate` for approve and reject,
and `/health`. Read-only apart from the gate: this repo adds no endpoints to the ranch, and
the only way to ranch data is over HTTP through the deployed APIs.

Same `{data, meta}` envelope and error shape as the four ranch services, so the whole
system reads consistently from the window. See `src/api/CLAUDE.md`.
"""
