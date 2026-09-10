"""The API rails. Arrive at M8, with `src/api/routes.py`.

Two things to prove, per `tests/CLAUDE.md`:

  * the `{data, meta}` envelope and the error shape match the four ranch services, because
    a window that has to special-case one of five services is a window that grows a
    translation layer
  * the gate endpoints approve and reject a real paused tick, and every `audit_id` in
    `logs/audit.jsonl` appears exactly twice. A single occurrence is a pause nobody
    answered, and it has to be visible as a dangling record rather than an absence.

No tests here yet. An empty module collects nothing and fails nothing, which is the honest
state of an unbuilt milestone.
"""
