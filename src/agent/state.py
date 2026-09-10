"""The typed things that move between stages of a tick.

`Finding` lands here in M1 because `triage.py` authors it. `Incident` and `RanchState`
join it in the same file as the store and the graph arrive: everything a stage hands to
the next stage is described in one place, so a reader can see the whole tick's data flow
without opening five modules.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["nominal", "warning", "critical"]
IncidentStatus = Literal["opened", "ongoing", "resolved"]

SEVERITY_ORDER: dict[Severity, int] = {"nominal": 0, "warning": 1, "critical": 2}


class Finding(BaseModel):
    """One thing that is wrong with one sensor, right now.

    This is the **handoff contract** between a sub-agent and the supervisor. A sub-agent
    inherits no context, so anything the supervisor will need has to be carried here
    rather than assumed to be in shared state.

    `severity`, `category`, and `summary` are authored by `triage.py`, in code, always.
    A model may echo them and may never author them: one handed a verdict and asked to
    justify it fabricates the justification.
    """

    model_config = ConfigDict(frozen=True)

    sensor_id: str
    sensor_type: str
    location: str
    category: str
    severity: Severity
    value: float | bool | None = None
    unit: str = ""
    threshold: float | None = None
    observed_at: str | None = None
    summary: str
    source: str = "triage"

    # Filled by a sub-agent from M2 onward, never in M1. Present now because the
    # escalation rule (`src/agent/CLAUDE.md`) is defined in terms of them: a Finding
    # that proposes a write escalates to Tier 2 regardless of severity.
    assessment: str | None = None
    proposed_write: dict[str, Any] | None = Field(default=None)

    @property
    def key(self) -> str:
        """`sensor:category`. The same tank going dry twice in one afternoon is one incident."""
        return f"{self.sensor_id}:{self.category}"


class Incident(BaseModel):
    """A finding that has been remembered. One row of `sw_ops.incidents`.

    The difference between a `Finding` and an `Incident` is time. A finding is what this
    tick saw; an incident is what the ranch has been living with, and it is the reason a
    persisting fault reads as `ongoing` instead of alarming every five minutes. Duplicate
    alerts train the client to ignore the service, which is worse than no service at all.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    sensor_id: str
    sensor_type: str
    location: str
    category: str
    severity: Severity
    status: IncidentStatus
    summary: str
    last_value: str | None = None
    unit: str = ""
    threshold: float | None = None
    occurrences: int = 1
    first_seen_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None = None
    tick_opened: int = 0
    tick_last_seen: int = 0
    run_id: str = ""
    # The owning sub-agent, from `routing.py`. Nullable because an incident is stored
    # before anyone is assigned it: the store is the record of what is true, and routing
    # is a decision about it.
    owner: str | None = None
    row_id: int | None = None


class RanchState(BaseModel):
    """What one tick knows, carried between stages.

    This becomes the LangGraph channel at M4, where the fan-out stages need reducers to
    merge concurrent sub-agent writes. In M1 the tick is a straight line, so it is a
    plain model and nothing merges. Written as one object now rather than as seven
    arguments threaded through `main.py`, because the graph will need exactly this.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str = ""
    tick: int = 0
    catalog_source: str = ""
    sensors_read: int = 0
    sensors_failed: int = 0
    findings: tuple[Finding, ...] = ()
    opened: tuple[Incident, ...] = ()
    ongoing: tuple[Incident, ...] = ()
    resolved: tuple[Incident, ...] = ()
    routed: dict[str, tuple[str, ...]] = Field(default_factory=dict)

    # Set BEFORE a stage is attempted, never after. A line reading `failed_stage: null`
    # next to an error says a tick died without saying where, which is the one question
    # the field exists to answer.
    failed_stage: str | None = None
    error: str | None = None
