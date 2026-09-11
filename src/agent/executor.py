"""The continuous loop. One tick, all ten stages of it, and from M4 the loop that runs them.

    catalog -> chaos -> sweep -> triage -> reconcile -> route      free
      -> evidence -> fan_out                                       spends
      -> gate                                                      pauses a proposed write for a human, never the tick (M6)
      -> synthesize                                                decides for itself

Five stages, zero tokens, and they are what narrows 160 sensors down to what is actually
wrong. Only what comes out of `route` reaches a model. `run_tick` returns a `RanchState`
rather than an exit code because the loop needs exactly this object, and `main.py` needs
one integer.

**`synthesize` sits outside the spend guard.** It is the one stage that decides for itself
whether it costs anything: one world reporting has nothing to fuse and is assembled in
code, so a calm tick and the whole `spend=False` pass still end with a shift report and
still pay nothing for it.

Cadence, backoff, the spend ceiling, and graceful shutdown wrap `run_tick` in `run_loop`.
They belong in this module rather than in `main.py`, which owns argument parsing and an
exit code and nothing else.

Rules from `src/agent/CLAUDE.md` enforced here rather than described:

  * **`failed_stage` is set before the stage is attempted, never after.** A line reading
    `failed_stage: null` beside an error says a tick died without saying where.
  * **Exactly one `tick.jsonl` line per tick, including a tick that failed, including a
    tick that skipped every stage because an upstream is in backoff.** A tick that writes no
    line is indistinguishable from a dead loop. The heartbeat does not go quiet while the
    patient is sick.
  * **The spend ceiling halts the loop; it does not skip a tick and carry on.** M4 is the
    first phase where the money runs with nobody watching, and a bug in backoff is a tight
    retry loop that bills. The halt is checked after every tick, so the overshoot is bounded
    at one tick, which is the price of never cancelling a fan-out whose tokens are already paid for.
  * **Backoff is per upstream, never global.** Backing the whole loop off because the Feed
    API is sick means one dead service stops the ranch watch.
  * **A crashed agent resolves nothing, and its incidents are held rather than forgotten.**
    Reconcile runs before any agent and reads triage's findings, so an agent raising cannot
    close an incident. What it can do is leave a `no_answer` order on an incident that is
    `ongoing` next tick and therefore never re-routed. `held` is that set, carried between
    ticks and re-routed until an agent actually answers or the incident resolves.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime

from src.agent.agent import RESPONDERS, owners_for, route, synthesize
from src.agent.gate import WriteProposal, build_gate, pending, propose
from src.agent.memory import (
    StoreTarget,
    checkpointer,
    counts_by_status,
    held_incident_keys,
    reconcile,
    record_held,
    resolve_store,
    store_session,
)
from src.agent.state import RanchState, WorkOrder
from src.agent.workers import fan_out
from src.models.llm_client import cost_usd
from src.tools import chaos
from src.tools.evidence import EvidencePacket, assemble
from src.tools.mcp_client import RanchMap
from src.tools.sensors import fetch_catalog, sweep
from src.tools.triage import triage_sweep
from src.utils.config import get_settings
from src.utils.logger import Stopwatch, bind_tick, get_logger, get_run_id, log_tick

log = get_logger(__name__)

# --------------------------------------------------------------------------- #
# exit codes. `main.py` returns these and nothing else invents one.
# --------------------------------------------------------------------------- #
EXIT_OK = 0  #: clean shutdown: the in-flight tick drained and its line was written
EXIT_UNRECOVERABLE = 1  #: a tick raised outside its own guard, or a second interrupt forced the stop
EXIT_CONFIG = 2  #: refused to start; nothing was attempted against the ranch
EXIT_NOT_IMPLEMENTED = 3  #: an unbuilt mode, naming the milestone that brings it
EXIT_SPEND_CEILING = 4  #: stopped on purpose at the per-run ceiling. Not 0, so a restart policy does not relaunch and spend again; not 1, so nobody goes hunting for an outage

# --------------------------------------------------------------------------- #
# the upstreams a stage depends on, which is the granularity backoff works at
# --------------------------------------------------------------------------- #
#: Farm, Feed, and Care sit behind the one `evidence` stage; telling them apart is
#: `evidence.py`'s business, not the loop's. `mcp` includes the REST catalog fallback, so a
#: `catalog` failure means both sources were down.
UPSTREAM_MCP = "mcp"
UPSTREAM_SENSOR = "sensor"
UPSTREAM_SW_OPS = "sw_ops"
UPSTREAM_EVIDENCE = "evidence"
UPSTREAM_MODEL = "model"
UPSTREAMS = (UPSTREAM_MCP, UPSTREAM_SENSOR, UPSTREAM_SW_OPS, UPSTREAM_EVIDENCE, UPSTREAM_MODEL)

#: Which stage's failure marks which upstream. `synthesize` is absent on purpose: it falls
#: back to the code-assembled page and never fails a tick.
STAGE_UPSTREAM: dict[str, str] = {"catalog": UPSTREAM_MCP, "sweep": UPSTREAM_SENSOR, "reconcile": UPSTREAM_SW_OPS, "evidence": UPSTREAM_EVIDENCE, "fan_out": UPSTREAM_MODEL}

#: The upstreams without which a tick has nothing safe to do. Skipping any of these skips
#: the whole free pass: no sweep means nothing to triage, and no ledger means nothing to
#: reconcile against. The tick still writes its line.
FREE_PASS_UPSTREAMS = frozenset({UPSTREAM_MCP, UPSTREAM_SENSOR, UPSTREAM_SW_OPS})

#: A `no_answer` order with one of these violations is retried next tick by holding its
#: incident. `no_payload` on `max_tokens` is deliberately NOT here: that is a config bug, and
#: retrying it buys the same truncated answer again. A `rejected` order is never held.
RETRIABLE_VIOLATIONS = frozenset({"agent_raised", "worker_raised", "transport_error", "upstream_backoff"})


@dataclass
class Backoff:
    """Per-upstream exponential backoff, deterministic, read by the loop before every tick.

    Deterministic rather than jittered because there is exactly one caller: jitter exists to
    break up a herd, and a single loop retrying one service has no herd to break. `retry_at`
    is a monotonic clock so a wall-clock adjustment mid-run cannot shorten or lengthen a window.
    """

    base: float
    cap: float
    failures: dict[str, int] = field(default_factory=dict)
    retry_at: dict[str, float] = field(default_factory=dict)

    def delay_for(self, consecutive: int) -> float:
        return float(min(self.cap, self.base * (2 ** max(consecutive - 1, 0))))

    def record_failure(self, upstream: str, *, now: float) -> float:
        n = self.failures.get(upstream, 0) + 1
        self.failures[upstream] = n
        delay = self.delay_for(n)
        self.retry_at[upstream] = now + delay
        log.warning("upstream_backoff", upstream=upstream, consecutive_failures=n, retry_in_s=round(delay, 1))
        return delay

    def record_success(self, upstream: str) -> None:
        if upstream in self.failures:
            log.info("upstream_recovered", upstream=upstream, after_failures=self.failures[upstream])
        self.failures.pop(upstream, None)
        self.retry_at.pop(upstream, None)

    def blocked(self, *, now: float) -> dict[str, float]:
        """`{upstream: seconds_remaining}` for every upstream still inside its window."""
        return {u: round(t - now, 1) for u, t in self.retry_at.items() if t > now}

    def observe(self, state: RanchState, *, now: float) -> None:
        """Fold one tick's outcome in. A stage that failed marks its upstream; a stage that
        ran and did not fail clears it. A stage that was skipped says nothing either way."""
        failed = STAGE_UPSTREAM.get(state.failed_stage or "")
        for stage, upstream in STAGE_UPSTREAM.items():
            if upstream in state.skipped_upstreams:
                continue
            if upstream == failed:
                self.record_failure(upstream, now=now)
            elif upstream in self.failures and _stage_ran(state, stage):
                self.record_success(upstream)
        # The model never raises out of `fan_out`; it comes back as transport-error orders. A
        # tick where every order the model was asked for died in transport is the model being
        # down, and one where some answered is not.
        if state.work_orders and UPSTREAM_MODEL not in state.skipped_upstreams:
            if all("transport_error" in o.violations for o in state.work_orders):
                self.record_failure(UPSTREAM_MODEL, now=now)
            elif UPSTREAM_MODEL in self.failures:
                self.record_success(UPSTREAM_MODEL)


_STAGE_ORDER = ("catalog", "sweep", "triage", "reconcile", "route", "evidence", "fan_out", "gate", "synthesize")

#: Why an incident is being held, written to `incidents.held_reason` so the reason survives a
#: restart with the key. The gate's own reason is the one the orders' violations cannot carry.
HELD_UPSTREAM_BACKOFF = "upstream_backoff"
HELD_GATE_UNAVAILABLE = "gate_unavailable"


@dataclass(frozen=True)
class GateOutcome:
    """What the gate stage did with this tick's proposals. `pending` is `None` when the gate
    was never opened, because a count nobody measured must not read as zero."""

    orders: tuple[WorkOrder, ...]
    proposed: tuple[str, ...] = ()
    duplicate: int = 0
    failed: int = 0
    pending: int | None = None
    held: frozenset[str] = frozenset()


async def _gate_step(target: StoreTarget, orders: tuple[WorkOrder, ...], *, routed_keys: frozenset[str], tick: int, run_id: str) -> GateOutcome:
    """Hand every surviving `proposed_write` to the gate. The tick does not wait for an answer.

    The key check lives here and not in `workers.check` because the key is code's: a proposal
    is born inside the work order for one packet and never names an incident, so the only way
    it can carry a foreign key is a bug on this side of the model. Checked anyway, as
    `write_key_unknown`, because "cannot happen" is what a future path that builds a work order
    by hand will say too.

    **The gate never fails the tick.** A checkpointer that cannot be reached drops nothing on
    the floor: each proposal that could not pause is counted in `failed`, its incident is held
    so the next tick re-judges and re-proposes it, and the shift report still gets written.
    """
    candidates = [o for o in orders if o.shippable and o.proposed_write]
    if not candidates:
        return GateOutcome(orders=orders)

    updated = {o.incident_key: o for o in orders}
    to_pause: list[WorkOrder] = []
    for order in candidates:
        if order.incident_key not in routed_keys:
            log.error("write_key_unknown", incident=order.incident_key, agent=order.agent, tool=(order.proposed_write or {}).get("tool"), routed=sorted(routed_keys), hint="a proposal for an incident this tick never routed; dropped before the gate")
            updated[order.incident_key] = order.model_copy(update={"proposed_write": None, "violations": (*order.violations, "write_key_unknown")})
            continue
        to_pause.append(order)

    proposed: list[str] = []
    duplicate = failed = 0
    held: set[str] = set()
    pending_count: int | None = None
    try:
        async with checkpointer(target) as saver:
            gate = build_gate(saver)
            for order in to_pause:
                assert order.proposed_write is not None
                outcome = await propose(gate, WriteProposal(incident_key=order.incident_key, agent=order.agent, tool=str(order.proposed_write["tool"]), args=dict(order.proposed_write["args"]), tick=tick, run_id=run_id))
                if outcome.duplicate_of:
                    duplicate += 1
                    updated[order.incident_key] = order.model_copy(update={"audit_id": outcome.duplicate_of})
                elif outcome.dropped:
                    failed += 1
                    held.add(order.incident_key)
                    updated[order.incident_key] = order.model_copy(update={"audit_id": outcome.audit_id})
                else:
                    proposed.append(outcome.audit_id)
                    updated[order.incident_key] = order.model_copy(update={"audit_id": outcome.audit_id})
            pending_count = len(await pending(gate))
    except Exception as exc:
        unpaused = [o.incident_key for o in to_pause if o.incident_key not in held and not updated[o.incident_key].audit_id]
        failed += len(unpaused)
        held.update(unpaused)
        log.error("gate_unavailable", error=f"{type(exc).__name__}: {exc}", proposals=len(to_pause), held=sorted(held), hint="the checkpointer could not be opened; the incidents are held and re-proposed next tick")

    return GateOutcome(orders=tuple(updated[o.incident_key] for o in orders), proposed=tuple(proposed), duplicate=duplicate, failed=failed, pending=pending_count, held=frozenset(held))


async def _write_held(target: StoreTarget, *, reasons: dict[str, str], released: frozenset[str]) -> None:
    """Persist the held set (migration 0005). Never fails the tick: the in-process set still
    carries the keys for this run, and a restart is the only thing that would notice."""
    if not reasons and not released:
        return
    try:
        async with store_session(url=target.url, schema=target.schema) as session:
            await record_held(session, reasons=reasons, released=released)
    except Exception as exc:
        log.warning("held_not_persisted", error=f"{type(exc).__name__}: {exc}", held=sorted(reasons), hint="the held set is still carried in-process for this run; it will not survive a restart")


async def restore_held(target: StoreTarget) -> frozenset[str]:
    """What the previous run was carrying, read before the loop's first tick."""
    async with store_session(url=target.url, schema=target.schema) as session:
        return await held_incident_keys(session)


def _stage_ran(state: RanchState, stage: str) -> bool:
    """True if the tick got past `stage` without failing in it. A tick that failed earlier
    never attempted it; a tick that spent nothing never attempted `evidence` or `fan_out`."""
    if stage in ("evidence", "fan_out"):
        return bool(state.work_orders)
    if state.failed_stage is None:
        return True
    if state.failed_stage not in _STAGE_ORDER:
        return False
    return _STAGE_ORDER.index(stage) < _STAGE_ORDER.index(state.failed_stage)


async def _chaos_step(session: object, *, state: RanchState, catalog: RanchMap, tick: int, chaos_seen: frozenset[str], chaos_injected: frozenset[str]) -> None:
    """Heal what is due, inject this tick's slice, fire any animal events, and name the misses.

    Chaos never fails the tick it decorates, which is M5's rule and stays: everything in here
    is inside the caller's `try`, but the caller catches this step's own exception separately
    and continues on honest readings. The miss check is the M4 addition: an event this run
    injected that heals without its sensor ever having been read was born and died between
    two sweeps, and a chaos log full of events nobody saw is the silent failure this exists to
    make loud. Animal events are excluded from the check because nothing observes them yet: no
    stage reads the Care API, which is the owed `herd_health` verification and its own item.
    """
    settings = get_settings()
    healed = await chaos.expire(session)
    state.chaos_healed = len(healed)
    missed = tuple(e.event_id for e in healed if e.kind == chaos.KIND_SENSOR and e.event_id in chaos_injected and e.event_id not in chaos_seen)
    for event in healed:
        if event.event_id in missed:
            log.warning("chaos_event_missed", event_id=event.event_id, scenario=event.scenario, target_id=event.target_id, injected_at=event.injected_at.isoformat(), expired_at=event.expires_at.isoformat(), hint="healed without its sensor ever being read: TTL shorter than the sweeps that actually ran, or the sweep was in backoff")
    state.chaos_missed = missed
    planned = chaos.plan(seed=settings.chaos_seed, catalog=catalog.sensors, ticks=tick, cohort=settings.chaos_cohort, ticks_between_events=settings.chaos_ticks_between_events)
    fired = await chaos.inject_for_tick(session, tick=tick, planned=planned, run_id=state.run_id)
    state.chaos_fired = len(fired)
    state.chaos_injected = tuple(e.event_id for e in fired)
    if fired:
        await chaos.fire_animal_events(fired)


async def run_tick(
    *,
    tick: int = 1,
    store: StoreTarget | None = None,
    now: datetime | None = None,
    spend: bool = True,
    held: Iterable[str] = (),
    skip: Iterable[str] = (),
    chaos_seen: Iterable[str] = (),
    chaos_injected: Iterable[str] = (),
) -> RanchState:
    """Run the pass once, fold the result into `sw_ops`, and write the work orders.

    `spend=False` stops the tick at the end of the free pass, before the roster call and
    before any model call. That is how `tests/` exercises the whole pipeline without a
    token: `tests/CLAUDE.md` forbids a test that reaches a model, and a flag read here
    beats five call sites each remembering to pass a fake.

    `store` and `now` are injected so a test can point at `sw_ops_test` and assert on a
    fixed timestamp. Left alone, the target comes from `SW_OPS_TARGET` through the same
    resolver `alembic` uses, because a migration applied to one database and a tick
    written to another is a failure that presents as an empty ledger rather than an error.

    The loop's four inputs, all empty for `--once`: `held` is the incident keys a previous
    tick could not get answered, re-routed here if they are still open; `skip` is the
    upstreams inside a backoff window, whose stages are not attempted; `chaos_seen` and
    `chaos_injected` are what the run has observed and armed so far, for the miss check.
    """
    target = store or resolve_store()
    state = RanchState(run_id=get_run_id(), tick=tick)
    bind_tick(tick)
    watch = Stopwatch()
    held_in = frozenset(held)
    skipping = frozenset(skip)
    seen = frozenset(chaos_seen)
    injected_before = frozenset(chaos_injected)
    held_unread = 0
    ledger: dict[str, int] = {}
    settings = get_settings()

    if skipping & FREE_PASS_UPSTREAMS:
        # Nothing safe to do without a catalog, a sweep, or a ledger. The tick is a heartbeat
        # that names why, and the held set passes through untouched: nothing was attempted, so
        # nothing was answered.
        state.skipped_upstreams = tuple(sorted(skipping))
        state.held = tuple(sorted(held_in))
        log.warning("tick_skipped", skipped=state.skipped_upstreams, held=len(state.held))
        log_tick(duration_ms=watch.ms, store=target.name, skipped_upstreams=list(state.skipped_upstreams), held=len(state.held), cost_usd=0.0, error=None, failed_stage=None)
        return state

    try:
        state.failed_stage = "catalog"
        ranch_map, state.catalog_source = await fetch_catalog()
        if not ranch_map.sensors:
            # An empty catalog downstream is indistinguishable from a calm ranch, so it is
            # a failed tick rather than a quiet one. Nothing is reconciled: resolving every
            # incident on the strength of a catalog we could not read is the worst
            # available outcome.
            raise RuntimeError(f"catalog is empty (source={state.catalog_source}); nothing to sweep")

        # Chaos, when armed, fires here and not first: `plan()` picks targets from the live
        # topology, so it needs the catalog. It heals before it injects so a TTL that ran out
        # produces a `resolved` incident on the very tick it expired. Its own failure never
        # fails the tick: the sweep continues on honest readings and the line says so.
        if settings.chaos_enabled:
            state.failed_stage = "chaos"
            try:
                async with store_session(url=target.url, schema=target.schema) as session:
                    await _chaos_step(session, state=state, catalog=ranch_map, tick=tick, chaos_seen=seen, chaos_injected=injected_before)
            except Exception as exc:
                log.warning("chaos_step_failed", error=f"{type(exc).__name__}: {exc}", hint="the sweep continues on honest readings; chaos never fails the tick it decorates")

        state.failed_stage = "sweep"
        swept = await sweep(ranch_map.sensors)
        state.sensors_read, state.sensors_failed = len(swept.readings), len(swept.errors)
        state.chaos_observed = swept.overlay_observed
        if swept.errors and not swept.readings:
            # One dark sensor is a per-sensor error and the sweep carries on. Every sensor dark
            # is the Sensor API being down, and it has to fail the stage or the loop never learns
            # to back off: it would read 160 connection errors on every tick, at cadence, forever.
            # Found by asking what a dead upstream looks like from here before killing one live.
            raise RuntimeError(f"sweep read nothing: all {len(swept.errors)} reads failed ({', '.join(sorted({e.category for e in swept.errors}))})")

        state.failed_stage = "triage"
        state.findings = tuple(triage_sweep(swept.readings))

        state.failed_stage = "reconcile"
        owners = owners_for(state.findings)
        async with store_session(url=target.url, schema=target.schema) as session:
            result = await reconcile(
                session,
                state.findings,
                tick=tick,
                run_id=state.run_id,
                # The sensors that actually answered. A sensor that did not answer resolves
                # nothing: "no finding" and "no reading" are different facts, and conflating
                # them lets one upstream outage close every incident and report an all-clear.
                read_sensor_ids={r.sensor_id for r in swept.readings},
                now=now,
                owners=owners,
            )
            ledger = {str(k): v for k, v in (await counts_by_status(session)).items()}
        state.opened, state.ongoing, state.resolved = result.opened, result.ongoing, result.resolved
        state.pending, state.dismissed = result.pending, result.dismissed
        held_unread = len(result.skipped_unread)

        state.failed_stage = "route"
        # Newly-opened only, plus whatever a previous tick was carrying unanswered. An
        # `ongoing` incident has already been worked, and re-waking its owner every five
        # minutes is how a service teaches its client to ignore it. A held one was never
        # worked: its agent raised, or the model never answered, and it is `ongoing` in the
        # ledger only because the ledger does not know the difference. A held incident that
        # resolved this tick falls out here on its own.
        retry = tuple(inc for inc in result.ongoing if inc.key in held_in)
        to_work = result.opened + retry
        state.routed = route(to_work)
        if retry:
            log.info("held_rerouted", count=len(retry), keys=[inc.key for inc in retry])

        # --- from here on the tick costs money ---------------------------------
        # Skipped entirely, all three stages, when nothing new opened. A calm tick must not
        # pay for a roster call it has no packet to put in.
        #
        # Every routed incident, not just water_feed's. M2 filtered to one agent here; M3's
        # whole point is that the four of them are worked in the same pass, because an
        # incident nobody was handed is an incident nobody is paged about.
        routed_keys = {key: agent for agent, keys in state.routed.items() for key in keys}
        newly_opened = tuple(inc for inc in to_work if inc.key in routed_keys)
        held_out: dict[str, str] = {}
        spend_blocked = skipping & {UPSTREAM_EVIDENCE, UPSTREAM_MODEL}
        if newly_opened and spend and spend_blocked:
            # The ranch is readable but the expensive half is not. Hold every routed incident
            # and try again when the window closes, rather than paying for packets nobody can
            # judge or asking a model that is not answering.
            state.skipped_upstreams = tuple(sorted(spend_blocked))
            held_out.update({inc.key: HELD_UPSTREAM_BACKOFF for inc in newly_opened})
            log.warning("spend_skipped", skipped=state.skipped_upstreams, held=len(held_out))
        elif newly_opened and spend:
            state.failed_stage = "evidence"
            # One assemble call for all four agents. The roster is fetched once per tick, so
            # four agents' packets cost the same HTTP as one agent's did.
            packets = await assemble(newly_opened, readings=swept.readings, ranch_map=ranch_map)

            state.failed_stage = "fan_out"
            by_agent: dict[str, list[EvidencePacket]] = {agent: [] for agent in RESPONDERS}
            for packet in packets:
                by_agent[routed_keys[packet.incident.key]].append(packet)
            orders = await fan_out({agent: tuple(group) for agent, group in by_agent.items()})
            state.work_orders = tuple(o for agent in RESPONDERS for o in orders[agent])
            state.worlds = tuple(agent for agent in RESPONDERS if orders[agent])
            for order in state.work_orders:
                retriable = sorted(set(order.violations) & RETRIABLE_VIOLATIONS)
                if order.status == "no_answer" and retriable:
                    held_out[order.incident_key] = retriable[0]

            # The gate. Every proposal that survived the three checks pauses for a human here,
            # and the tick moves on: a pending write does not block the watch. A duplicate of a
            # write already waiting is suppressed, not asked twice.
            state.failed_stage = "gate"
            gated = await _gate_step(target, state.work_orders, routed_keys=frozenset(routed_keys), tick=tick, run_id=state.run_id)
            state.work_orders = gated.orders
            state.writes_proposed, state.writes_duplicate, state.writes_failed, state.writes_pending = gated.proposed, gated.duplicate, gated.failed, gated.pending
            held_out.update({key: HELD_GATE_UNAVAILABLE for key in gated.held})
        state.held = tuple(sorted(held_out))
        if state.held:
            log.warning("incidents_held", count=len(state.held), keys=list(state.held), hint="no agent answered for a retriable reason; re-routed next tick")
        # Durable from M6 (migration 0005): the reasons on what is held, null on what was held
        # and no longer is, so a restart carries the same set this process does.
        await _write_held(target, reasons=held_out, released=held_in)

        # Outside the spend block, and on every tick, because it is free unless it has
        # something to fuse. `synthesize` calls a model only when two or more worlds opened
        # incidents; a calm tick and the whole free pass get the code-assembled page for
        # nothing. A tick that ends with no page at all is a shift nobody was briefed on.
        state.failed_stage = "synthesize"
        state.shift_report = await synthesize(state, spend=spend)

        state.failed_stage = None
    # Broad on purpose: a tick reports its own failure and never propagates one, because
    # an exception escaping here takes the loop down over one bad stage.
    except Exception as exc:
        state.error = f"{type(exc).__name__}: {exc}"
        # A tick that failed before it could answer for what it was carrying keeps carrying it.
        state.held = tuple(sorted(held_in))
        log.error("tick_failed", stage=state.failed_stage, error=state.error)

    input_tokens = sum(o.input_tokens for o in state.work_orders) + (state.shift_report.input_tokens if state.shift_report else 0)
    output_tokens = sum(o.output_tokens for o in state.work_orders) + (state.shift_report.output_tokens if state.shift_report else 0)
    state.cost_usd = cost_usd(input_tokens, output_tokens)

    log_tick(
        duration_ms=watch.ms,
        store=target.name,
        catalog_source=state.catalog_source,
        sensors_read=state.sensors_read,
        sensors_failed=state.sensors_failed,
        findings=len(state.findings),
        critical=sum(1 for f in state.findings if f.severity == "critical"),
        opened=len(state.opened),
        ongoing=len(state.ongoing),
        resolved=len(state.resolved),
        # The debounce. `pending` was flagged this sweep and not yet confirmed; `dismissed`
        # was pending and read clean. A high `dismissed` beside a low `opened` is the
        # simulator's dice being filtered out, which is the number that used to be the bill.
        pending=len(state.pending),
        dismissed=len(state.dismissed),
        held_unread=held_unread,
        agents_routed=sorted(state.routed),
        work_orders=len(state.work_orders),
        work_orders_shipped=sum(1 for o in state.work_orders if o.shippable),
        work_orders_rejected=sum(1 for o in state.work_orders if not o.shippable),
        escalated=sum(1 for o in state.work_orders if o.escalate),
        # The M2 verification lives on this pair: token cost stays flat across sweeps while
        # incident count moves, because only newly-opened incidents reach a model.
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        # Dollars at the assumed rate. This is the number the loop sums against its ceiling,
        # so it is on the line rather than derived from it later.
        cost_usd=state.cost_usd,
        # Which worlds reported, and whether the supervisor paid to fuse them. `worlds` is the
        # storm-front count and `shift_report` is what it cost, so the two together are the
        # only way to read a tick's synthesis spend off one line.
        worlds=list(state.worlds),
        shift_report=state.shift_report.source if state.shift_report else None,
        shift_report_violations=list(state.shift_report.violations) if state.shift_report else [],
        ledger=ledger,
        # The loop's fields. `held` is incidents carried to the next tick unanswered;
        # `skipped_upstreams` is who was in backoff; the chaos trio is what was armed, healed,
        # and, worst, healed unseen.
        held=len(state.held),
        skipped_upstreams=list(state.skipped_upstreams),
        # The gate's fields, M6. `writes_pending` is everything waiting for a human across the
        # ledger, or null when this tick had nothing to propose and did not open the gate.
        writes_proposed=len(state.writes_proposed),
        writes_duplicate=state.writes_duplicate,
        writes_failed=state.writes_failed,
        writes_pending=state.writes_pending,
        chaos_fired=state.chaos_fired,
        chaos_healed=state.chaos_healed,
        chaos_missed=list(state.chaos_missed),
        error=state.error,
        failed_stage=state.failed_stage,
    )
    return state


# --------------------------------------------------------------------------- #
# the loop
# --------------------------------------------------------------------------- #
TickFn = Callable[..., Awaitable[RanchState]]


async def run_loop(
    *,
    store: StoreTarget | None = None,
    interval_s: float | None = None,
    spend: bool = True,
    ceiling_usd: float | None = None,
    stop: asyncio.Event | None = None,
    max_ticks: int | None = None,
    tick_fn: TickFn = run_tick,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    restore: Callable[[StoreTarget], Awaitable[frozenset[str]]] | None = None,
) -> int:
    """Run ticks at a fixed cadence until stopped, halted, or broken. Returns an exit code.

    Cadence is **start to start**: the next tick is scheduled from when this one began, so
    a 90-second storm tick does not push every later tick 90 seconds late. A tick that
    overruns the interval starts the next one immediately and says so.

    Shutdown on Windows 11, which is where this runs. `loop.add_signal_handler` raises
    `NotImplementedError` there, so nothing here uses it. Python 3.11's `asyncio.Runner`
    already does the right thing with Ctrl+C: the first one cancels the main task, the
    second raises `KeyboardInterrupt`. So the in-flight tick runs as its own task behind
    `asyncio.shield`, the cancel lands here and not in the tick, the tick is **drained**
    rather than cancelled (its tokens are already paid for), the line is written, and the
    loop returns 0. A second Ctrl+C escapes as `KeyboardInterrupt` and `main.py` returns 1.
    SIGTERM and SIGBREAK set `stop` through `signal.signal` in `main.py`, for the same drain.

    `tick_fn`, `clock`, and `sleep` are injected so a rail can run thirty ticks in a
    millisecond with a fake tick and assert on the bookkeeping. `max_ticks` exists for the
    same reason and for the paid verification run; production leaves it `None`.
    """
    settings = get_settings()
    interval = float(settings.tick_interval_seconds if interval_s is None else interval_s)
    ceiling = float(settings.spend_ceiling_usd if ceiling_usd is None else ceiling_usd)
    if spend and ceiling <= 0:
        raise ValueError(f"SPEND_CEILING_USD must be positive to run a spending loop (got {ceiling}); there is no value that means unlimited")
    stop = stop or asyncio.Event()
    do_sleep = sleep or asyncio.sleep
    backoff = Backoff(base=settings.backoff_base_seconds, cap=settings.backoff_cap_seconds)
    target = store or resolve_store()

    spent = 0.0
    tick = 0
    chaos_seen: set[str] = set()
    chaos_injected: set[str] = set()
    # M6: what the previous run was carrying. Read once, and a ledger that cannot be read here
    # starts the loop with an empty set rather than refusing to start: the first tick will hit
    # the same ledger and back off properly if it is really down.
    held: frozenset[str] = frozenset()
    try:
        held = await (restore or restore_held)(target)
        if held:
            log.info("held_restored", count=len(held), keys=sorted(held), hint="carried from a previous run; re-routed on the first tick they are still open")
    except Exception as exc:
        log.warning("held_restore_failed", error=f"{type(exc).__name__}: {exc}", hint="starting with an empty held set")
    log.info("loop_start", store=target.name, interval_s=interval, spend=spend, ceiling_usd=ceiling if spend else None, max_ticks=max_ticks, held=len(held))

    next_at = clock()
    while not stop.is_set():
        tick += 1
        # Bound here as well as inside the tick: a task copies its context, so the tick's own
        # `bind_tick` never reaches this coroutine, and the loop's lines about tick 12 would
        # otherwise say `tick=0`. Found on the first live run.
        bind_tick(tick)
        started = clock()
        blocked = backoff.blocked(now=started)
        task = asyncio.ensure_future(tick_fn(tick=tick, store=target, spend=spend, held=held, skip=tuple(sorted(blocked)), chaos_seen=tuple(chaos_seen), chaos_injected=tuple(chaos_injected)))
        try:
            state = await asyncio.shield(task)
        except asyncio.CancelledError:
            # First interrupt. The tick keeps running; this coroutine waits for it. A second
            # interrupt during the wait arrives as KeyboardInterrupt and is the forced stop.
            _uncancel()
            stop.set()
            log.warning("loop_draining", tick=tick, hint="finishing the in-flight tick; interrupt again to force")
            state = await task
        except Exception as exc:
            # `run_tick` catches everything a stage can raise, so reaching here is a bug in the
            # tick's own scaffolding. It still gets a line, because the alternative is a dead
            # loop that looks exactly like a loop that never started, and then it stops: a
            # loop retrying a bug is the tight billing loop this module exists to prevent.
            detail = f"{type(exc).__name__}: {exc}"
            log_tick(duration_ms=int((clock() - started) * 1000), store=target.name, cost_usd=0.0, error=detail, failed_stage="tick")
            log.error("loop_unrecoverable", tick=tick, error=detail)
            return EXIT_UNRECOVERABLE

        spent = round(spent + state.cost_usd, 6)
        held = frozenset(state.held)
        chaos_seen.update(state.chaos_observed)
        chaos_injected.update(state.chaos_injected)
        backoff.observe(state, now=clock())

        if spend and spent >= ceiling:
            log.error("loop_halted", reason="spend_ceiling", spent_usd=spent, ceiling_usd=ceiling, ticks=tick, run_id=state.run_id, hint="raise SPEND_CEILING_USD and restart deliberately; the loop does not resume on its own")
            return EXIT_SPEND_CEILING
        if stop.is_set() or (max_ticks is not None and tick >= max_ticks):
            break

        next_at += interval
        delay = next_at - clock()
        if delay < 0:
            log.warning("tick_overran", tick=tick, by_s=round(-delay, 1), interval_s=interval)
            next_at = clock()
            continue
        try:
            await _wait_or_sleep(stop, delay, do_sleep)
        except asyncio.CancelledError:
            _uncancel()
            stop.set()

    log.info("loop_stopped", ticks=tick, spent_usd=spent, held=len(held), run_id=get_run_id())
    return EXIT_OK


async def _wait_or_sleep(stop: asyncio.Event, delay: float, do_sleep: Callable[[float], Awaitable[None]]) -> None:
    """Sleep out the cadence, but wake early if a stop is requested from a signal handler."""
    if do_sleep is asyncio.sleep:
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except TimeoutError:
            pass
        return
    await do_sleep(delay)


def _uncancel() -> None:
    """Swallowing a `CancelledError` on purpose has to tell the task so. In 3.11 the Runner
    counts cancellations, and an unbalanced count turns the next timeout into a cancel."""
    task = asyncio.current_task()
    if task is not None:
        task.uncancel()


def summarize(state: RanchState) -> str:
    """One human line for the console, because `--once` is run by a person watching."""
    if state.error:
        return f"tick {state.tick} FAILED at {state.failed_stage}: {state.error}"
    if state.skipped_upstreams and not state.catalog_source:
        return f"tick {state.tick} skipped - {', '.join(state.skipped_upstreams)} in backoff, {len(state.held)} held"
    fan = ", ".join(f"{agent}:{len(keys)}" for agent, keys in state.routed.items()) or "nobody"
    spend = ""
    if state.work_orders:
        tokens = sum(o.input_tokens + o.output_tokens for o in state.work_orders)
        spend = f", {sum(1 for o in state.work_orders if o.shippable)}/{len(state.work_orders)} work orders shipped on {tokens} tokens (${state.cost_usd:.2f})"
    report = f", shift report {state.shift_report.source}" if state.shift_report else ""
    held = f", {len(state.held)} held" if state.held else ""
    debounce = f" (pending {len(state.pending)}, dismissed {len(state.dismissed)})" if state.pending or state.dismissed else ""
    return (
        f"tick {state.tick} ok - {state.sensors_read} read ({state.sensors_failed} failed) via {state.catalog_source}, "
        f"{len(state.findings)} findings, opened {len(state.opened)} / ongoing {len(state.ongoing)} / resolved {len(state.resolved)}{debounce}, routed to {fan}{spend}{report}{held}"
    )

