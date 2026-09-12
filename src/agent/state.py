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
#: `pending` is seen but not yet confirmed: live in the ledger, never paged, never billed.
#: `dismissed` is a pending that read clean before confirmation: never alarmed, so not `resolved`.
IncidentStatus = Literal["pending", "opened", "ongoing", "resolved", "dismissed"]
LIVE_STATUSES: tuple[IncidentStatus, ...] = ("pending", "opened", "ongoing")

SEVERITY_ORDER: dict[Severity, int] = {"nominal": 0, "warning": 1, "critical": 2}

#: M7A. `subject_type` on a `Finding` or an `Incident` is one of the 13 sensor types for a sensor
#: subject, or this literal for an animal subject. That is how the two kinds of row are told apart
#: (migration `0006`): anything that is not `animal` is a sensor, including a sensor type triage
#: has never heard of, which keeps the unknown-type path a sensor path.
SUBJECT_ANIMAL = "animal"


class Finding(BaseModel):
    """One thing that is wrong with one subject, a sensor or an animal, right now.

    This is the **handoff contract** between a sub-agent and the supervisor. A sub-agent
    inherits no context, so anything the supervisor will need has to be carried here
    rather than assumed to be in shared state.

    `severity`, `category`, and `summary` are authored by `triage.py`, in code, always.
    A model may echo them and may never author them: one handed a verdict and asked to
    justify it fabricates the justification.
    """

    model_config = ConfigDict(frozen=True)

    #: The sensor id, or from M7A the animal id. `subject_type` is the sensor type, or `SUBJECT_ANIMAL`.
    subject_id: str
    subject_type: str
    #: The sensor's location name, or the animal's pasture id. Both slug to a pasture id in `evidence.py`.
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
        """`subject:category`. The same tank going dry twice in one afternoon is one incident, and so
        is the same cow reading deceased on every sweep until somebody restores her."""
        return f"{self.subject_id}:{self.category}"

    @property
    def is_animal(self) -> bool:
        return self.subject_type == SUBJECT_ANIMAL


class Incident(BaseModel):
    """A finding that has been remembered. One row of `sw_ops.incidents`.

    The difference between a `Finding` and an `Incident` is time. A finding is what this
    tick saw; an incident is what the ranch has been living with, and it is the reason a
    persisting fault reads as `ongoing` instead of alarming every five minutes. Duplicate
    alerts train the client to ignore the service, which is worse than no service at all.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    subject_id: str
    subject_type: str
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

    @property
    def is_animal(self) -> bool:
        """An animal row (migration `0006`). Decides which page `evidence.py` renders for it."""
        return self.subject_type == SUBJECT_ANIMAL


WorkOrderStatus = Literal["ok", "rejected", "no_answer"]


class WorkOrder(BaseModel):
    """What a sub-agent wrote about one incident, plus the receipt for the call.

    Arrives at M2. The split of ownership is the whole point of this shape: every field
    above `status` is the model's prose, every field below it is a fact code established,
    and `severity` is triage's rather than the model's echo of it. The echo is checked and
    then thrown away, because storing what a model said the severity was creates a second
    answer to a question that already has one.

    `violations` is populated rather than raised. A rejected work order still has to be
    readable: "the model said this and here is why we did not ship it" is the record M7
    needs to move this job down a tier, and an exception leaves nothing behind.
    """

    model_config = ConfigDict(frozen=True)

    incident_key: str
    agent: str
    severity: Severity  # triage's, always. Never the model's echo.

    headline: str = ""
    assessment: str = ""
    actions: tuple[str, ...] = ()
    rules_cited: tuple[str, ...] = ()
    escalate: bool = False
    escalate_reason: str = ""
    unknowns: tuple[str, ...] = ()
    #: The one thing in a work order that could change the ranch rather than describe it, and
    #: it is a **proposal**: `{"tool": ..., "args": {...}}`, naming a write tool in this agent's
    #: slice. Arrives at M6. `None` is the usual answer. A proposal that survived the three
    #: return-path checks pauses in `src/agent/gate.py` for a human; one that did not is
    #: stripped here and its violation recorded, and the prose still ships.
    proposed_write: dict[str, Any] | None = None
    #: M7. The Tier-1 judge's way of saying "I do not know" on purpose. An escalation trigger
    #: (`routing.escalation_reason`): a model allowed to say it says it instead of inventing.
    #: Recorded on a Tier-2 order too, where it is information for the human and escalates nothing.
    insufficient_information: bool = False

    status: WorkOrderStatus = "ok"
    violations: tuple[str, ...] = ()
    severity_echo: str = ""
    #: The gate's receipt for `proposed_write`: the `audit_id` under which it is paused (or was
    #: decided), or empty when nothing was proposed or the proposal was rejected before pausing.
    audit_id: str = ""

    provider: str = ""
    model: str = ""
    finish_reason: str = ""
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    #: M7. Which tier wrote the stored order, and why it was not the cheaper one. `escalation`
    #: is one of `routing.ESCALATION_REASONS`, or empty when the order was written by the tier
    #: that was asked first. A Tier-2 order with `escalation=""` is one the cascade was off for.
    tier: int = 2
    escalation: str = ""
    #: The receipt for the Tier-1 attempt that was rewritten, so a tick line can still sum what
    #: the local model was asked, and so the pair is readable off the stored order alone.
    tier1_finish_reason: str = ""
    tier1_violations: tuple[str, ...] = ()
    tier1_latency_ms: int = 0
    tier1_input_tokens: int = 0
    tier1_output_tokens: int = 0
    #: M10. Whether the investigator ran for this order, how it ended (`answered`, `step_ceiling`,
    #: `deadline`, `no_tool_calls`, `error`), how many tool calls it made, and which tools. Code's
    #: half of the order: a receipt of the loop, never anything the model said about it. An order
    #: with `investigated=True` and `investigation_outcome="answered"` was judged twice at Tier 1,
    #: the second time on the page plus the facts the loop fetched.
    investigated: bool = False
    investigation_outcome: str = ""
    investigation_steps: int = 0
    investigation_tools: tuple[str, ...] = ()

    @property
    def shippable(self) -> bool:
        return self.status == "ok"

    def render(self) -> str:
        """The work order as a person on shift reads it. Also what a human grades."""
        lines = [f"[{self.severity.upper()}] {self.headline}", ""]
        lines.append(self.assessment)
        lines.append("")
        lines.extend(f"  {i}. {action}" for i, action in enumerate(self.actions, start=1))
        if self.unknowns:
            lines.extend(["", "Not known from the sensors:", *(f"  - {u}" for u in self.unknowns)])
        if self.escalate:
            lines.extend(["", f"ESCALATE: {self.escalate_reason or 'no reason given'}"])
        if self.proposed_write:
            args = ", ".join(f"{k}={v!r}" for k, v in sorted(self.proposed_write.get("args", {}).items()))
            where = f" (paused as {self.audit_id[:8]}, waiting for a human)" if self.audit_id else ""
            lines.extend(["", f"PROPOSED WRITE: {self.proposed_write.get('tool')}({args}){where}"])
        lines.extend(["", f"Rules: {', '.join(self.rules_cited) or 'none cited'}"])
        if self.violations:
            lines.append(f"{'REJECTED' if self.status != 'ok' else 'NOTED'} ({self.status}): {', '.join(self.violations)}")
        return "\n".join(lines)


class ShiftReport(BaseModel):
    """One page for the person coming on shift. The supervisor's own output, arriving at M3.

    A `WorkOrder` answers "what is wrong at this sensor." This answers "what is going on at
    this ranch," and the difference is the reason a supervisor exists rather than four
    independent scripts. Four agents each writing a correct page about their own world still
    leaves somebody at 5am reading four pages and doing the fusion themselves.

    Same ownership split as `WorkOrder`, for the same reason: `headline` through `escalations`
    is prose, everything from `source` down is code's record of how the page was produced.

    `source` is the field to read first when this looks wrong. **`code` is not a degraded
    mode.** A tick where one world opened incidents needs no fusion, so the report is
    assembled deterministically and no model is called at all; a tick where the model was
    called and failed lands here too, and `violations` is what tells those apart.
    """

    model_config = ConfigDict(frozen=True)

    headline: str = ""
    #: The fused narrative. Two to five sentences, and the only place a causal claim across
    #: two worlds may be made.
    situation: str = ""
    #: What the shift does, in order. First item is what happens now.
    priorities: tuple[str, ...] = ()
    #: The incident keys this report claims are one event rather than several. The fusion
    #: claim, stated as data so it can be checked: every key here must be one that was
    #: handed to the report, exactly as `rules_cited` must exist in the packet's SOP.
    linked: tuple[str, ...] = ()
    #: What a human above the crew needs to know, one line each.
    escalations: tuple[str, ...] = ()

    source: Literal["model", "code"] = "code"
    #: Which sensing worlds opened incidents this tick. Two or more is the storm front, and
    #: it is both the reason to call a model here and an escalation trigger in its own right
    #: (`src/agent/CLAUDE.md`).
    worlds: tuple[str, ...] = ()
    work_orders: int = 0
    violations: tuple[str, ...] = ()

    provider: str = ""
    model: str = ""
    finish_reason: str = ""
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    #: M10. Which tier wrote the stored report and why it was not the cheaper one, the same shape
    #: as `WorkOrder`. `escalation` is one of `routing.ESCALATION_REASONS` or empty; a Tier-2 report
    #: with `escalation=""` is one the cascade was off for. The `tier1_*` receipt is the local attempt
    #: when Opus rewrote it, so the pair is readable off the stored report alone. On the model only:
    #: the `shift_reports` row did not change (no migration); the tick line carries `report_tier`.
    tier: int = 2
    escalation: str = ""
    tier1_finish_reason: str = ""
    tier1_violations: tuple[str, ...] = ()
    tier1_latency_ms: int = 0
    tier1_input_tokens: int = 0
    tier1_output_tokens: int = 0

    def render(self) -> str:
        """The page as the person coming on shift reads it."""
        lines = [self.headline or "(no headline)", ""]
        if self.situation:
            lines.extend([self.situation, ""])
        lines.append("Priorities:")
        lines.extend(f"  {i}. {p}" for i, p in enumerate(self.priorities, start=1))
        if not self.priorities:
            lines.append("  (none listed)")
        if self.linked:
            lines.extend(["", f"Read together as one event: {', '.join(self.linked)}"])
        if self.escalations:
            lines.extend(["", "Escalate:", *(f"  - {e}" for e in self.escalations)])
        lines.extend(["", f"Worlds: {', '.join(self.worlds) or 'none'} | {self.work_orders} work orders | assembled by {self.source}"])
        if self.violations:
            lines.append(f"VIOLATIONS: {', '.join(self.violations)}")
        return "\n".join(lines)


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
    #: M7A, the herd sweep. `herd_animals` is how many animals it read a record for this tick (the
    #: ones whose state changed: non-active, or named on a pending care task), `herd_errors` is how
    #: many per-subject reads failed, and `herd_error` is why the stage as a whole failed, or `None`.
    #: A failed herd stage never fails the tick; it means no animal answered, so none resolves.
    herd_animals: int = 0
    herd_errors: int = 0
    herd_error: str | None = None
    findings: tuple[Finding, ...] = ()
    #: `opened` is what gets paged and billed. `pending` was flagged this sweep but not yet
    #: confirmed; `dismissed` was pending and read clean, never alarmed.
    opened: tuple[Incident, ...] = ()
    ongoing: tuple[Incident, ...] = ()
    resolved: tuple[Incident, ...] = ()
    pending: tuple[Incident, ...] = ()
    dismissed: tuple[Incident, ...] = ()
    routed: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    #: The first stage that spends money. One per newly-opened incident, across all four
    #: responders as of M3.
    work_orders: tuple[WorkOrder, ...] = ()
    #: The sensing worlds that actually produced work orders this tick, in `RESPONDERS`
    #: order. Two or more is the storm front: it is what makes the shift report a fusion
    #: rather than a concatenation, and it is an escalation trigger in its own right.
    worlds: tuple[str, ...] = ()
    #: The supervisor's own page, assembled last. `None` on a tick that spent nothing,
    #: because a tick with no work orders has no shift to report on.
    shift_report: ShiftReport | None = None
    #: Dollars this tick billed at `routing.PRICE_TABLE`, per model, Tier 1 at zero. Exactly 0.0 on a calm
    #: tick, and the number the M4 loop sums against its ceiling.
    cost_usd: float = 0.0
    #: Incidents whose owner never answered for a reason worth retrying (the agent raised, the
    #: transport failed, or the upstream was in backoff). The loop carries this set into the
    #: next tick, which re-routes them; a rail rejection is NOT in here, because retrying a
    #: rejected answer turns a rail into a sampler.
    held: tuple[str, ...] = ()
    #: The gate, from M6. `writes_proposed` is the audit ids this tick paused for a human;
    #: `writes_duplicate` is proposals suppressed because the same incident already has the same
    #: write waiting; `writes_failed` is proposals the gate could not persist (their incidents
    #: are held); `writes_pending` is everything waiting across the ledger after this tick.
    writes_proposed: tuple[str, ...] = ()
    writes_duplicate: int = 0
    writes_failed: int = 0
    writes_pending: int | None = None
    #: Upstreams whose stage this tick skipped because they were inside a backoff window.
    skipped_upstreams: tuple[str, ...] = ()
    #: Chaos, when armed: events injected this tick, events healed this tick, the overlay events
    #: whose target sensor the sweep read, and injected-this-run events that healed without ever
    #: being read. `chaos_missed` non-empty is a fault that was born and died between sweeps.
    chaos_fired: int = 0
    chaos_healed: int = 0
    chaos_injected: tuple[str, ...] = ()
    chaos_observed: tuple[str, ...] = ()
    chaos_missed: tuple[str, ...] = ()

    # Set BEFORE a stage is attempted, never after. A line reading `failed_stage: null`
    # next to an error says a tick died without saying where, which is the one question
    # the field exists to answer.
    failed_stage: str | None = None
    error: str | None = None
