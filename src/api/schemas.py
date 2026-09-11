"""The wire shapes. M8.

Pydantic v2 models for what the read API returns and the one body it accepts. Every response
rides in the `{data}` or `{data, meta}` envelope from `src/api/CLAUDE.md`, and every error in
`{error: {code, message, details}}`, so the window reads this service the way it reads the four
ranch services.

Cross-service IDs are **plain strings, never foreign keys**. Orphans are allowed on purpose
and no schema here adds an existence check against another service.

These are projections of the ledger rows, not the ledger's own models: `state.Incident` is
what the tick reasons with, `IncidentOut` is what a browser sees, and they are allowed to
differ (`key` becomes `incident_key`, `row_id` becomes `id`) without either side noticing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.agent.gate import GateState, PendingWrite
from src.agent.state import Incident, IncidentStatus, Severity

Decision = Literal["approve", "reject"]
#: The owners an incident can carry, for the `owner` filter. Literal rather than imported from
#: `agent.AGENTS` because FastAPI validates a query parameter from the type, and a tuple is not a type.
AgentName = Literal["water_feed", "herd_health", "infrastructure", "compliance", "chaos"]


# --------------------------------------------------------------------------- #
# the envelope
# --------------------------------------------------------------------------- #
class Meta(BaseModel):
    """`count` is items **returned** (`len(data)`), not total matching rows. Same as the ranch APIs."""

    count: int
    limit: int
    offset: int


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    error: ErrorBody


# --------------------------------------------------------------------------- #
# resources
# --------------------------------------------------------------------------- #
class HealthOut(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "sweetwater-ops"
    time: str


class IncidentOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int | None
    incident_key: str
    subject_id: str
    subject_type: str
    location: str
    category: str
    severity: Severity
    status: IncidentStatus
    summary: str
    last_value: str | None
    unit: str
    threshold: float | None
    occurrences: int
    first_seen_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None
    tick_opened: int
    tick_last_seen: int
    run_id: str
    owner: str | None

    @classmethod
    def from_incident(cls, inc: Incident) -> IncidentOut:
        return cls(
            id=inc.row_id, incident_key=inc.key, subject_id=inc.subject_id, subject_type=inc.subject_type, location=inc.location, category=inc.category, severity=inc.severity, status=inc.status,
            summary=inc.summary, last_value=inc.last_value, unit=inc.unit, threshold=inc.threshold, occurrences=inc.occurrences, first_seen_at=inc.first_seen_at, last_seen_at=inc.last_seen_at,
            resolved_at=inc.resolved_at, tick_opened=inc.tick_opened, tick_last_seen=inc.tick_last_seen, run_id=inc.run_id, owner=inc.owner,
        )


class ShiftReportOut(BaseModel):
    """One row of `sw_ops.shift_reports`. `source` is the field to read first: `code` is not a
    degraded mode, and `violations` is what tells a calm tick from a model that failed."""

    id: int
    run_id: str
    tick: int
    at: datetime
    source: str
    headline: str
    situation: str
    priorities: list[str]
    linked: list[str]
    escalations: list[str]
    worlds: list[str]
    incident_keys: list[str]
    work_orders: int
    violations: list[str]
    provider: str
    model: str
    finish_reason: str
    latency_ms: int
    input_tokens: int
    output_tokens: int


class TickOut(BaseModel):
    """One row of `sw_ops.ticks`: the tick line, whole, in `fields`, plus what the API indexes on.
    `id` doubles as the SSE event id, so a reconnecting window resumes from where it left off."""

    id: int
    run_id: str
    tick: int
    at: datetime
    store: str
    duration_ms: int
    cost_usd: float
    error: str | None
    failed_stage: str | None
    fields: dict[str, Any]


class PendingWriteOut(BaseModel):
    """A paused write, as `python -m src.agent.gate list` prints it."""

    audit_id: str
    incident_key: str
    agent: str
    tool: str
    args: dict[str, Any]
    proposed_at: str
    tick: int
    run_id: str
    age_s: int

    @classmethod
    def from_pending(cls, p: PendingWrite, *, now: datetime | None = None) -> PendingWriteOut:
        return cls(audit_id=p.audit_id, incident_key=p.incident_key, agent=p.agent, tool=p.tool, args=dict(p.args), proposed_at=p.proposed_at, tick=p.tick, run_id=p.run_id, age_s=p.age_s(now))


class GateDecisionIn(BaseModel):
    """The body of `POST /ops/gate`. No `decided_by`: that is the name the bearer token maps to."""

    model_config = ConfigDict(extra="forbid")

    audit_id: str = Field(min_length=1)
    decision: Decision
    reason: str = ""


class GateDecisionOut(BaseModel):
    """The finished `GateState`, which is also what the `decided` receipt says."""

    audit_id: str
    incident_key: str
    agent: str
    tool: str
    args: dict[str, Any]
    decision: str
    decided_by: str
    reason: str
    decided_at: str
    result: str
    upstream: str
    latency_to_decision_ms: int

    @classmethod
    def from_state(cls, final: GateState) -> GateDecisionOut:
        return cls(
            audit_id=final.get("audit_id", ""), incident_key=final.get("incident_key", ""), agent=final.get("agent", ""), tool=final.get("tool", ""), args=dict(final.get("args") or {}),
            decision=final.get("decision", ""), decided_by=final.get("decided_by", ""), reason=final.get("reason", ""), decided_at=final.get("decided_at", ""), result=final.get("result", ""),
            upstream=final.get("upstream", ""), latency_to_decision_ms=int(final.get("latency_to_decision_ms", 0)),
        )
