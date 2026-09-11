"""Sweetwater: the agentic layer.

    python main.py --handshake   prove the deployed ranch is reachable, then exit
    python main.py --once        run exactly one tick, then exit
    python main.py               run the tick loop until interrupted, or until the spend ceiling halts it
    python main.py --no-spend    either of the above with the two paid stages skipped
    python main.py --api         serve the read API only

Exit codes: 0 clean, 1 unrecoverable or forced, 2 config, 3 not built yet, 4 spend ceiling.

The four ranch APIs and the MCP server are deployed and frozen. This process is
the only thing being built here: one orchestrator, five sub-agents, running
continuously against a real ranch that is genuinely trying to break.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys

from src.agent.executor import EXIT_NOT_IMPLEMENTED, EXIT_OK, EXIT_UNRECOVERABLE
from src.tools.allowlists import DEPLOYED_TOOLS
from src.utils.config import get_settings
from src.utils.logger import Stopwatch, bind_tick, configure_logging, get_logger, log_tick

#: The count is the headline, but the NAMES are what the allowlists are built out of, so
#: the handshake checks the set and not the size. Nineteen tools with one renamed passes a
#: count check and silently empties whichever slice named the old spelling, which would
#: surface much later as an agent that mysteriously cannot do its job.
EXPECTED_TOOL_COUNT = len(DEPLOYED_TOOLS)


async def handshake() -> int:
    """Prove the upstream contract before building anything on top of it.

    Asserts the tool count rather than printing it. "19 tools" is a fact this whole
    design rests on, and a silently-17 upstream would present much later as an
    agent that mysteriously cannot do its job.
    """
    from src.tools.mcp_client import McpUnavailableError, list_tool_names, ranch_session, read_ranch_map

    log = get_logger("sweetwater.handshake")
    settings = get_settings()

    missing = settings.missing_upstreams()
    if missing:
        log.error("config_incomplete", missing=missing, hint="copy .env.example to .env and fill it in")
        return 2

    bind_tick(0)
    watch = Stopwatch()
    tools: list[str] = []
    sensors = 0
    locations = 0
    types = 0
    failed_stage: str | None = None
    error: str | None = None

    try:
        # Set BEFORE the connect, not inside it. A connection failure that reports
        # `failed_stage: null` tells you a tick died without telling you where, which
        # is the one question the field exists to answer.
        failed_stage = "connect"
        async with ranch_session() as session:
            failed_stage = "list_tools"
            tools = sorted(await list_tool_names(session))
            log.info("tools_discovered", count=len(tools), tools=tools)

            failed_stage = "read_resource"
            ranch_map = await read_ranch_map(session)
            sensors, locations, types = len(ranch_map.sensors), len(ranch_map.locations), len(ranch_map.types)
            log.info("ranch_map_read", sensors=sensors, locations=locations, types=types, sensor_types=list(ranch_map.types))
            failed_stage = None
    except McpUnavailableError as exc:
        error = str(exc)
        log.error("handshake_failed", stage=failed_stage, error=error)

    # Written even on failure. A handshake that produces no tick line is
    # indistinguishable from a process that never started.
    log_tick(
        stage="handshake",
        duration_ms=watch.ms,
        tools_found=len(tools),
        sensors=sensors,
        locations=locations,
        sensor_types=types,
        error=error,
        failed_stage=failed_stage,
    )

    if error:
        return 1
    drifted = frozenset(tools) ^ DEPLOYED_TOOLS
    if drifted:
        # Named both ways round, because the two directions mean different things. A tool
        # the server no longer has empties a slice; a tool this repo has never heard of is
        # a scoped change over there and a conversation, per the boundary rule.
        log.error(
            "tool_surface_drifted",
            expected=EXPECTED_TOOL_COUNT,
            found=len(tools),
            gone_from_upstream=sorted(DEPLOYED_TOOLS - frozenset(tools)),
            new_upstream=sorted(frozenset(tools) - DEPLOYED_TOOLS),
            hint="update DEPLOYED_TOOLS in src/tools/allowlists.py and re-check every slice that named a changed tool",
        )
        return 1
    if sensors == 0:
        log.error("empty_ranch_map", hint="an empty map and a calm ranch are indistinguishable downstream; treating as failure")
        return 1

    log.info("handshake_ok", tools=len(tools), sensors=sensors, locations=locations, duration_ms=watch.ms)
    return 0


async def once(*, spend: bool = True) -> int:
    """Exactly one tick, then exit. `--no-spend` stops it at the end of the free pass.

    `SW_OPS_TARGET=test` sends the ledger to the local `sw_ops_test` schema instead of
    Supabase, through the same resolver `alembic` uses. Prod is the default: a default
    that quietly writes somewhere harmless is a default that ships.
    """
    from src.agent.executor import run_tick, summarize
    from src.agent.memory import SchemaGuardError, resolve_store

    log = get_logger("sweetwater.once")
    settings = get_settings()

    missing = settings.missing_upstreams()
    if missing:
        log.error("config_incomplete", missing=missing, hint="copy .env.example to .env and fill it in")
        return 2

    try:
        store = resolve_store()
    except SchemaGuardError as exc:
        # A guard refusal is a config error, not a tick failure, and it writes no tick
        # line: nothing was attempted against the ranch.
        log.error("store_unavailable", error=str(exc))
        return 2

    state = await run_tick(tick=1, store=store, spend=spend)
    log.info("once_done", store=store.name, summary=summarize(state))
    return EXIT_UNRECOVERABLE if state.error else EXIT_OK


async def loop(*, spend: bool = True) -> int:
    """The continuous loop. M4. Runs until interrupted, or until the spend ceiling halts it.

    Signals, on the platform this actually runs on. Windows has no `loop.add_signal_handler`
    and never delivers SIGTERM to a console process, so nothing here depends on either. Ctrl+C
    is handled by `asyncio.Runner` (first cancels, second raises) and `run_loop` turns the
    first into a drain. SIGTERM (POSIX) and SIGBREAK (Windows, Ctrl+Break) are wired through
    `signal.signal` to request the same drain, and are simply absent where the platform lacks them.
    """
    from src.agent.executor import EXIT_CONFIG, run_loop
    from src.agent.memory import SchemaGuardError, resolve_store

    log = get_logger("sweetwater.loop")
    settings = get_settings()

    missing = settings.missing_upstreams()
    if missing:
        log.error("config_incomplete", missing=missing, hint="copy .env.example to .env and fill it in")
        return EXIT_CONFIG
    if spend and settings.spend_ceiling_usd <= 0:
        log.error("spend_ceiling_invalid", ceiling_usd=settings.spend_ceiling_usd, hint="SPEND_CEILING_USD must be positive; there is no unlimited setting. Use --no-spend for a free loop")
        return EXIT_CONFIG
    try:
        store = resolve_store()
    except SchemaGuardError as exc:
        log.error("store_unavailable", error=str(exc))
        return EXIT_CONFIG

    stop = asyncio.Event()
    running = asyncio.get_running_loop()

    def _request_stop(signum: int, _frame: object) -> None:
        name = signal.Signals(signum).name
        running.call_soon_threadsafe(stop.set)
        running.call_soon_threadsafe(lambda: log.warning("stop_requested", signal=name))

    for name in ("SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, _request_stop)

    return await run_loop(store=store, spend=spend, stop=stop)


async def not_yet(name: str, milestone: str) -> int:
    get_logger("sweetwater").error("not_implemented", command=name, arrives_in=milestone)
    return EXIT_NOT_IMPLEMENTED


def main() -> int:
    parser = argparse.ArgumentParser(prog="sweetwater", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--handshake", action="store_true", help="verify the deployed MCP server and ranch map, then exit")
    mode.add_argument("--once", action="store_true", help="run exactly one tick, then exit")
    mode.add_argument("--api", action="store_true", help="serve the read API only, no tick loop")
    parser.add_argument("--no-spend", action="store_true", help="stop every tick at the end of the free pass; no evidence call, no model call, no bill")
    args = parser.parse_args()

    run_id = configure_logging()
    mode_name = "handshake" if args.handshake else "once" if args.once else "api" if args.api else "loop"
    get_logger("sweetwater").info("start", run_id=run_id, mode=mode_name, spend=not args.no_spend)

    if args.handshake:
        return asyncio.run(handshake())
    if args.once:
        return asyncio.run(once(spend=not args.no_spend))
    if args.api:
        return asyncio.run(not_yet("--api", "M8"))
    try:
        return asyncio.run(loop(spend=not args.no_spend))
    except KeyboardInterrupt:
        # The second Ctrl+C. The first was turned into a drain inside `run_loop`; this one
        # means the person at the keyboard did not want to wait, and the in-flight tick was
        # cancelled by the Runner on the way out. Not a clean stop, so not exit 0.
        get_logger("sweetwater").error("loop_forced", hint="second interrupt; the in-flight tick was cancelled and wrote no line")
        return EXIT_UNRECOVERABLE


if __name__ == "__main__":
    sys.exit(main())
