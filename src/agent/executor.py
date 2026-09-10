"""The continuous loop. In M1 that is one tick, and the tick is the free pass.

    catalog -> sweep -> triage -> reconcile -> route

Five stages, zero tokens. The fan-out and the synthesis that cost money arrive at M2 and
hang off `state.routed`, which is why this returns a `RanchState` rather than an exit
code: the graph at M4 needs exactly this object, and `main.py` needs one integer.

Cadence, backoff, and graceful shutdown wrap `run_tick` here at M4. They belong in this
module rather than in `main.py`, which owns argument parsing and an exit code and nothing
else.

Two rules from `src/agent/CLAUDE.md` are enforced here rather than described:

  * **`failed_stage` is set before the stage is attempted, never after.** A line reading
    `failed_stage: null` beside an error says a tick died without saying where.
  * **Exactly one `tick.jsonl` line per tick, including a tick that failed.** A tick that
    writes no line is indistinguishable from a dead loop, and telling those apart at 2am
    is the entire product.
"""

from __future__ import annotations

from datetime import datetime

from src.agent.agent import WATER_FEED, owners_for, route
from src.agent.memory import StoreTarget, counts_by_status, reconcile, resolve_store, store_session
from src.agent.state import RanchState
from src.agent.workers import run_water_feed
from src.tools.evidence import assemble
from src.tools.sensors import fetch_catalog, sweep
from src.tools.triage import triage_sweep
from src.utils.logger import Stopwatch, bind_tick, get_logger, get_run_id, log_tick

log = get_logger(__name__)


async def run_tick(*, tick: int = 1, store: StoreTarget | None = None, now: datetime | None = None, spend: bool = True) -> RanchState:
    """Run the pass once, fold the result into `sw_ops`, and write the work orders.

    `spend=False` stops the tick at the end of the free pass, before the roster call and
    before any model call. That is how `tests/` exercises the whole pipeline without a
    token: `tests/CLAUDE.md` forbids a test that reaches a model, and a flag read here
    beats five call sites each remembering to pass a fake.

    `store` and `now` are injected so a test can point at `sw_ops_test` and assert on a
    fixed timestamp. Left alone, the target comes from `SW_OPS_TARGET` through the same
    resolver `alembic` uses, because a migration applied to one database and a tick
    written to another is a failure that presents as an empty ledger rather than an error.
    """
    target = store or resolve_store()
    state = RanchState(run_id=get_run_id(), tick=tick)
    bind_tick(tick)
    watch = Stopwatch()
    held_unread = 0
    ledger: dict[str, int] = {}

    try:
        state.failed_stage = "catalog"
        ranch_map, state.catalog_source = await fetch_catalog()
        if not ranch_map.sensors:
            # An empty catalog downstream is indistinguishable from a calm ranch, so it is
            # a failed tick rather than a quiet one. Nothing is reconciled: resolving every
            # incident on the strength of a catalog we could not read is the worst
            # available outcome.
            raise RuntimeError(f"catalog is empty (source={state.catalog_source}); nothing to sweep")

        state.failed_stage = "sweep"
        swept = await sweep(ranch_map.sensors)
        state.sensors_read, state.sensors_failed = len(swept.readings), len(swept.errors)

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
        held_unread = len(result.skipped_unread)

        state.failed_stage = "route"
        # Newly-opened only. An `ongoing` incident has already been worked, and re-waking
        # its owner every five minutes is how a service teaches its client to ignore it.
        state.routed = route(result.opened)

        # --- from here on the tick costs money ---------------------------------
        # Skipped entirely, both stages, when nothing new opened. A calm tick must not pay
        # for a roster call it has no packet to put in.
        water_feed_keys = frozenset(state.routed.get(WATER_FEED, ()))
        newly_opened = tuple(inc for inc in result.opened if inc.key in water_feed_keys)
        if newly_opened and spend:
            state.failed_stage = "evidence"
            packets = await assemble(newly_opened, readings=swept.readings, ranch_map=ranch_map)

            state.failed_stage = "water_feed"
            state.work_orders = await run_water_feed(packets)

        state.failed_stage = None
    # Broad on purpose: a tick reports its own failure and never propagates one, because
    # an exception escaping here takes the M4 loop down over one bad stage.
    except Exception as exc:
        state.error = f"{type(exc).__name__}: {exc}"
        log.error("tick_failed", stage=state.failed_stage, error=state.error)

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
        held_unread=held_unread,
        agents_routed=sorted(state.routed),
        work_orders=len(state.work_orders),
        work_orders_shipped=sum(1 for o in state.work_orders if o.shippable),
        work_orders_rejected=sum(1 for o in state.work_orders if not o.shippable),
        escalated=sum(1 for o in state.work_orders if o.escalate),
        # The M2 verification lives on this pair: token cost stays flat across sweeps while
        # incident count moves, because only newly-opened incidents reach a model.
        input_tokens=sum(o.input_tokens for o in state.work_orders),
        output_tokens=sum(o.output_tokens for o in state.work_orders),
        ledger=ledger,
        error=state.error,
        failed_stage=state.failed_stage,
    )
    return state


def summarize(state: RanchState) -> str:
    """One human line for the console, because `--once` is run by a person watching."""
    if state.error:
        return f"tick {state.tick} FAILED at {state.failed_stage}: {state.error}"
    fan = ", ".join(f"{agent}:{len(keys)}" for agent, keys in state.routed.items()) or "nobody"
    spend = ""
    if state.work_orders:
        tokens = sum(o.input_tokens + o.output_tokens for o in state.work_orders)
        spend = f", {sum(1 for o in state.work_orders if o.shippable)}/{len(state.work_orders)} work orders shipped on {tokens} tokens"
    return (
        f"tick {state.tick} ok - {state.sensors_read} read ({state.sensors_failed} failed) via {state.catalog_source}, "
        f"{len(state.findings)} findings, opened {len(state.opened)} / ongoing {len(state.ongoing)} / resolved {len(state.resolved)}, routed to {fan}{spend}"
    )
