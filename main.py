"""Sweetwater: the agentic layer.

    python main.py --handshake   prove the deployed ranch is reachable, then exit
    python main.py --once        run exactly one tick, then exit
    python main.py               run the tick loop until interrupted
    python main.py --api         serve the read API only

The four ranch APIs and the MCP server are deployed and frozen. This process is
the only thing being built here: one orchestrator, five sub-agents, running
continuously against a real ranch that is genuinely trying to break.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from src.utils.config import get_settings
from src.utils.logger import Stopwatch, bind_tick, configure_logging, get_logger, log_tick

EXPECTED_TOOL_COUNT = 19


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
    if len(tools) != EXPECTED_TOOL_COUNT:
        log.error("unexpected_tool_count", expected=EXPECTED_TOOL_COUNT, found=len(tools), tools=tools)
        return 1
    if sensors == 0:
        log.error("empty_ranch_map", hint="an empty map and a calm ranch are indistinguishable downstream; treating as failure")
        return 1

    log.info("handshake_ok", tools=len(tools), sensors=sensors, locations=locations, duration_ms=watch.ms)
    return 0


async def once() -> int:
    """Exactly one tick, then exit. The free pass end to end, no model involved.

    `SW_OPS_TARGET=test` sends the ledger to the local `sw_ops_test` schema instead of
    Supabase, through the same resolver `alembic` uses. Prod is the default: a default
    that quietly writes somewhere harmless is a default that ships.
    """
    from src.agent.memory import SchemaGuardError, resolve_store
    from src.agent.tick import run_tick, summarize

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

    state = await run_tick(tick=1, store=store)
    log.info("once_done", store=store.name, summary=summarize(state))
    return 1 if state.error else 0


async def not_yet(name: str, milestone: str) -> int:
    get_logger("sweetwater").error("not_implemented", command=name, arrives_in=milestone)
    return 3


def main() -> int:
    parser = argparse.ArgumentParser(prog="sweetwater", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--handshake", action="store_true", help="verify the deployed MCP server and ranch map, then exit")
    mode.add_argument("--once", action="store_true", help="run exactly one tick, then exit")
    mode.add_argument("--api", action="store_true", help="serve the read API only, no tick loop")
    args = parser.parse_args()

    run_id = configure_logging()
    get_logger("sweetwater").info("start", run_id=run_id, mode="handshake" if args.handshake else "once" if args.once else "api" if args.api else "loop")

    if args.handshake:
        return asyncio.run(handshake())
    if args.once:
        return asyncio.run(once())
    if args.api:
        return asyncio.run(not_yet("--api", "M8"))
    return asyncio.run(not_yet("the tick loop", "M4"))


if __name__ == "__main__":
    sys.exit(main())
