"""The five tool slices, enforced in code. Arrives at M3.

The deployed MCP server exposes 19 flat tool names with no namespaces, so each slice is an
explicit name set. Any future `create_react_agent` receives a **filtered** list: isolation
that lives in a prompt is a request, and a request is not a boundary.

The line worth defending: `herd_health` cannot read a sensor, and nothing but `water_feed`
can touch feed. That is what makes the supervisor real rather than decorative.

A test counts each set rather than asserting its contents, so widening a slice to fix a
symptom fails loudly. See `tests/CLAUDE.md` for what that rail proves.

## Declared, proposable, and performable are three different things

Eight of the 19 tools mutate the ranch. Four of them sit in a slice. This module keeps three
answers to three different questions, and M6 is what made the third one real:

  * `tools_for(agent)` is the **declaration**. It includes the writes, because the counts
    the tests assert have to be the real counts. A slice that quietly omits its write tools
    is a slice whose test proves nothing about the shape that eventually ships.
  * `bound_tools_for(agent)` and `proposable_tools_for(agent)` are **what a model may be
    handed, and what it may propose**. Both are empty of writes while `GATE_LANDED` is False.
    From M6 a model never calls a write; it may *propose* one in its work order
    (`WorkOrder.proposed_write`), and the proposal pauses in `src/agent/gate.py` for a human.
  * `assert_callable(tool, approval=...)` is **who may perform**. A write needs an `Approval`,
    minted only by the gate after a human resumed the pause with `approve`.

`GATE_LANDED` is one name in one place. M6 flipped it, after the pause and the audit stream
were proven, and it is not a knob to reach for when something else is failing.

## The four tools nobody gets, and why they are named here anyway

`assign_to_pasture`, `assign_to_shelter`, `remove_from_pasture`, and `remove_from_shelter`
are deployed and appear in **no** slice. No agent has a reason to move an animal between
places: that is a crew decision about the ranch's actual operation, not an inference from a
sensor. They are listed in `DEPLOYED_TOOLS` and in `WRITE_TOOLS` regardless, which is the
whole point. `docs/Plan.md` marks four write tools with a `*`; the deployed surface
has eight, and the four extra ones are exactly the kind that get added to a slice later by
somebody who needed one read out of the same API. Naming them means that edit trips the
runtime guard instead of PATCHing the Care API on a Tuesday.

## No import from `src.agent`, on purpose

Agent names are literals here rather than imported from `src/agent/agent.py`, because
`mcp_client` imports this module for its write guard and the chain back is real:
`mcp_client -> allowlists -> agent -> triage -> sensors -> mcp_client`. A test asserts
`SLICES` covers exactly `agent.AGENTS`, so the two stay in step without the cycle. The rail
is the sync mechanism; the import would only have been a shorter way to write it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.utils.logger import get_logger

log = get_logger(__name__)

#: Every tool the frozen upstream exposes, verified over the wire by `--handshake` on
#: 2026-09-10 (19 tools, protocol `2025-11-25`). Written down here because it was written
#: down nowhere in this repo: the doc set names only the 15 that appear in a slice, so
#: "no agent names a tool outside its set" had no universe to be checked against. Read off
#: the wire, not copied out of the upstream's source, per the boundary rule in root
#: `CLAUDE.md`. `main.py --handshake` compares the deployed list against this set on every
#: run and fails on drift in either direction, which is the right home for the check: the
#: handshake is already the thing that talks to the live ranch, and `pytest` has to pass on
#: a plane.
DEPLOYED_TOOLS: frozenset[str] = frozenset(
    {
        "assign_to_pasture",
        "assign_to_shelter",
        "consume_feed",
        "create_observation",
        "get_animal",
        "get_sensor_readings",
        "list_animals",
        "list_care_tasks",
        "list_feed_products",
        "list_inventory",
        "list_observations",
        "list_pastures",
        "list_sensors",
        "list_shelters",
        "read_sensor",
        "remove_from_pasture",
        "remove_from_shelter",
        "restock_feed",
        "update_care_task",
    }
)

#: Everything that changes ranch state. All eight, not just the four a slice names, because
#: the guard below is only worth having if it covers the tools a future edit might reach for.
#: `consume_feed` and `restock_feed` move inventory; `create_observation` and
#: `update_care_task` write the care record; the four placement tools move animals.
WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "assign_to_pasture",
        "assign_to_shelter",
        "consume_feed",
        "create_observation",
        "remove_from_pasture",
        "remove_from_shelter",
        "restock_feed",
        "update_care_task",
    }
)

#: **M6 flips this, and M6 is the only thing that may.** Until then no write tool is handed
#: to a model and `call_tool` refuses one outright. The gate is `interrupt()` plus the
#: LangGraph Postgres checkpointer, so a pause outlives the process; see
#: `src/agent/CLAUDE.md`. A boolean rather than a config value on purpose - an env var is a
#: thing somebody can set on a laptop at 11pm to make a demo work.
#:
#: Flipping it does NOT make a write callable. It makes a write **proposable**: the tool
#: appears in `bound_tools_for`, a work order may carry a `proposed_write`, and the proposal
#: pauses in `src/agent/gate.py` until a human answers. `assert_callable` still refuses a write
#: that arrives without an `Approval`, so the runtime belt covers us after the flip exactly as
#: it did before it.
GATE_LANDED = True  # flipped 2026-09-11, M6, after the pause and the audit stream were proven on sw_ops_test


@dataclass(frozen=True)
class Approval:
    """A human's yes, carried to the one call that performs the write.

    Minted only by `src/agent/gate.py` after a resumed `interrupt()` came back `approve`. It
    is the thing `assert_callable` asks for on a write once `GATE_LANDED` is True, so the
    path that performs a mutation cannot be reached by a helper that merely knows the tool's
    name. Not a token to be checked against a store: the checkpointer already holds the
    decision, and this object exists to make the call site say who decided.
    """

    audit_id: str
    decided_by: str


#: How a write tool's arguments are validated before a proposal may pause for a human, and
#: how each is rendered into a brief. **Read off the wire on 2026-09-11** from `tools/list`
#: on the deployed MCP server, the same way `DEPLOYED_TOOLS` was; never from the upstream's
#: source. The kinds decide which return-path check applies to a value the model wrote:
#:
#:   * `id`        must appear on the evidence page the model was handed (grounding)
#:   * `number`    must appear on the page as a number token (grounding)
#:   * `enum`      must be one of the listed values (shape)
#:   * `timestamp` must parse as ISO 8601 (shape)
#:   * `text`      free prose a human reads; not graded
#:
#: A `reason` or a `note` cannot be on the page verbatim, so grading it as an id would reject
#: every honest proposal. An id or a quantity that is NOT on the page is the invented number
#: the whole design exists to keep out of the ranch's records.
WriteArgKind = Literal["id", "number", "enum", "timestamp", "text"]


@dataclass(frozen=True)
class WriteArg:
    name: str
    kind: WriteArgKind
    required: bool = True
    choices: tuple[str, ...] = ()


WRITE_TOOL_ARGS: dict[str, tuple[WriteArg, ...]] = {
    "consume_feed": (WriteArg("sku", "id"), WriteArg("quantity", "number"), WriteArg("reason", "text", required=False)),
    "restock_feed": (WriteArg("sku", "id"), WriteArg("quantity", "number"), WriteArg("reason", "text", required=False)),
    "create_observation": (
        WriteArg("animalId", "id"),
        WriteArg("type", "enum", choices=("behavior", "appetite", "mobility", "appearance", "injury", "general")),
        WriteArg("severity", "enum", choices=("low", "medium", "high")),
        WriteArg("note", "text"),
        WriteArg("observedAt", "timestamp"),
    ),
    "update_care_task": (WriteArg("taskId", "id"), WriteArg("status", "enum", required=False, choices=("pending", "completed", "cancelled")), WriteArg("notes", "text", required=False)),
    "assign_to_pasture": (WriteArg("pastureId", "id"), WriteArg("animalId", "id")),
    "remove_from_pasture": (WriteArg("pastureId", "id"), WriteArg("animalId", "id")),
    "assign_to_shelter": (WriteArg("shelterId", "id"), WriteArg("animalId", "id")),
    "remove_from_shelter": (WriteArg("shelterId", "id"), WriteArg("animalId", "id")),
}


def proposable_tools_for(agent: str) -> frozenset[str]:
    """The writes this agent may propose: its declared slice, restricted to the write set.

    Empty for every agent while `GATE_LANDED` is False, because a proposal with nowhere to
    pause is a write with no gate. The brief renders this set, and `workers.check` rejects a
    proposal naming anything outside it as `write_tool_not_allowed`.
    """
    if not GATE_LANDED:
        return frozenset()
    return tools_for(agent) & WRITE_TOOLS

#: Three agents legitimately share the sensor read tools, and that is not carved up here.
#: The isolation that matters is the brief, the SOP set, and which sensor types each agent
#: is pointed at, not tool-name exclusivity; carving it further would mean editing a frozen
#: server. `docs/Plan.md` is honest about this seam and so is this comment.
_SENSOR_READS = frozenset({"list_sensors", "read_sensor", "get_sensor_readings"})

#: The counts are the spec: 7 / 6 / 5 / 5 / 0. A test asserts each one exactly, so a slice
#: cannot grow by one tool without a deliberate edit to a number a human reads.
SLICES: dict[str, frozenset[str]] = {
    # Nobody dies of thirst or hunger. Feed is the only slice that reaches the Feed API at
    # all, which is what makes "nothing but water_feed can touch feed" a fact about the
    # code rather than a sentence in a brief.
    "water_feed": _SENSOR_READS | {"list_feed_products", "list_inventory", "consume_feed", "restock_feed"},
    # No sensor reads, by design and not by omission: `herd_health` cannot see a sensor, so
    # a sensor incident can never route to it (`src/agent/agent.py`). It stays idle until
    # chaos writes real animal events at M5. Its whole surface is the Care API.
    "herd_health": frozenset({"list_animals", "get_animal", "list_observations", "create_observation", "list_care_tasks", "update_care_task"}),
    # Containment, power, fuel, and the physical plant. Pastures and shelters because a
    # fence down or a gate open is a question about what is behind it.
    "infrastructure": _SENSOR_READS | {"list_pastures", "list_shelters"},
    # AUM stocking and habitat: keep the payments, prove the stewardship. Animals read-only,
    # because the count on the ground is the numerator of every stocking-rate question.
    "compliance": _SENSOR_READS | {"list_pastures", "list_animals"},
    # Chaos gets none of the 19. Its hands are its own, in `src/tools/chaos.py`, and its
    # animal writes go over REST behind `CHAOS_ALLOW_WRITES` and `CHAOS_ANIMAL_COHORT`
    # rather than through a tool a model could reach. See `src/tools/CLAUDE.md`.
    "chaos": frozenset(),
}

#: Deployed, in no slice, and that is the intended state. Read the module docstring before
#: moving one of these into a slice.
UNASSIGNED_TOOLS: frozenset[str] = DEPLOYED_TOOLS - frozenset().union(*SLICES.values())


class WriteGateError(RuntimeError):
    """A write tool was invoked before M6 landed the human gate.

    Raised rather than returned as data, unlike every upstream error in this layer. The
    distinction is deliberate: an upstream error is a fact about the ranch that a caller
    decides what to do about, and this is a fact about **this repo** being wired wrong. There
    is no retry policy, no budget, and no caller judgment that makes it acceptable.
    """


def tools_for(agent: str) -> frozenset[str]:
    """The declared slice, writes included. The count tests assert against this.

    An unknown agent is loud and empty rather than permissive. A default that returns
    everything is how a typo in an agent name becomes a model holding all 19 tools.
    """
    slice_ = SLICES.get(agent)
    if slice_ is None:
        log.error("no_slice_for_agent", agent=agent, known=sorted(SLICES), hint="add the slice in src/tools/allowlists.py; an unknown agent gets nothing")
        return frozenset()
    return slice_


def bound_tools_for(agent: str) -> frozenset[str]:
    """What a model may actually be handed right now: the slice, minus the ungated writes.

    The only function that should ever build a tool list for `create_react_agent`. Nothing
    at M3 binds tools at all - the workers judge an assembled packet in one call and have
    nothing to navigate (`src/agent/workers.py`) - so today this is the seam rather than a
    live filter. It exists now so that M4 or M6 wiring a tool loop has one obvious place to
    get its list from, instead of reaching for `tools_for` because it was shorter.
    """
    declared = tools_for(agent)
    if GATE_LANDED:
        return declared
    withheld = declared & WRITE_TOOLS
    if withheld:
        log.info("write_tools_withheld", agent=agent, withheld=sorted(withheld), reason="GATE_LANDED is False: no interrupt() gate to pause a proposal in")
    return declared - WRITE_TOOLS


def is_allowed(agent: str, tool: str) -> bool:
    """Whether this agent may name this tool at all. Declaration, not gating."""
    return tool in tools_for(agent)


def assert_callable(tool: str, *, agent: str = "", approval: Approval | None = None) -> None:
    """The runtime belt behind the withheld list. Raises on an ungated or unapproved write.

    `bound_tools_for` is the boundary for the model; this is the boundary for us. The two
    are not redundant: the filtered list only protects the paths that remember to use it,
    and a test fixture, a debugging script, or an M4 edit in a hurry are all paths that
    might not. Called from `mcp_client.call_tool`, which every MCP invocation goes through.

    From M6 a write needs an `Approval`, which only `src/agent/gate.py` mints and only after a
    human resumed the pause with `approve`. The flip of `GATE_LANDED` changed what a model may
    propose; it did not change who may perform.
    """
    if tool in WRITE_TOOLS and not GATE_LANDED:
        raise WriteGateError(f"{tool} writes to the live ranch and GATE_LANDED is False. Set allowlists.GATE_LANDED only when interrupt() and the checkpointer are actually in place (M6).")
    if tool in WRITE_TOOLS and approval is None:
        raise WriteGateError(f"{tool} writes to the live ranch and needs a human's Approval. Propose it through the gate (src/agent/gate.py) and perform it from an approved decision; nothing else may call a write tool directly.")
    if agent and tool not in tools_for(agent):
        raise WriteGateError(f"{agent} has no {tool} in its slice. Widen the slice deliberately in src/tools/allowlists.py, or call the tool as the agent that owns it.")


__all__ = [
    "DEPLOYED_TOOLS",
    "GATE_LANDED",
    "SLICES",
    "UNASSIGNED_TOOLS",
    "WRITE_TOOLS",
    "WRITE_TOOL_ARGS",
    "Approval",
    "WriteArg",
    "WriteGateError",
    "assert_callable",
    "bound_tools_for",
    "is_allowed",
    "proposable_tools_for",
    "tools_for",
]
