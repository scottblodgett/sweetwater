"""Streamable HTTP client against the deployed MCP server's Lambda Function URL.

The upstream is FROZEN. This repo owns no part of it: 19 tools and the
`ranch://sensors/map` resource are what exist, and if one turns out to be missing
that is a scoped change to the other repo and a conversation, not a drive-by.

Two things worth knowing before reading further:

  * The tool names are FLAT. There are no namespaces, so a per-agent allowlist is
    an explicit set of literal names (`src/tools/allowlists.py`), never a prefix
    match.
  * LangChain's adapter surfaces TOOLS ONLY. The ranch map is a resource, so it is
    never in the tool list and has to be read explicitly with `read_resource`.
    An agent that "should have the map" and does not is almost always this.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import TextContent, TextResourceContents

from src.tools.allowlists import Approval, assert_callable
from src.utils.config import get_settings
from src.utils.logger import get_logger

RANCH_MAP_URI = "ranch://sensors/map"

log = get_logger(__name__)


class McpUnavailableError(RuntimeError):
    """The MCP server could not be reached or refused the handshake."""


def flatten_exception(exc: BaseException) -> str:
    """Unwrap ExceptionGroup down to the leaves that actually say what went wrong.

    The MCP transport runs on anyio task groups, so ANY failure underneath surfaces
    as `unhandled errors in a TaskGroup (1 sub-exception)`. That string is the same
    whether the host refused the connection, DNS failed, or the server returned a
    401, which makes it worse than useless in a log: it is a message that looks like
    information and carries none. Flatten to `ConnectionRefusedError: ...` instead.
    """
    leaves: list[str] = []

    def walk(e: BaseException) -> None:
        if isinstance(e, BaseExceptionGroup):
            for sub in e.exceptions:
                walk(sub)
        else:
            text = str(e).strip()
            leaves.append(f"{type(e).__name__}: {text}" if text else type(e).__name__)

    walk(exc)
    # Deduplicated, because 160 concurrent reads failing the same way produce 160
    # identical leaves and a log line nobody will scroll to the end of.
    seen: dict[str, None] = {}
    for leaf in leaves:
        seen.setdefault(leaf, None)
    return "; ".join(seen) or repr(exc)


@asynccontextmanager
async def ranch_session() -> AsyncIterator[ClientSession]:
    """Open an initialized session against the deployed server.

    The Lambda handler is stateless, so there is no session to resume and no
    server-side cost to opening one per tick. Kept as a context manager anyway so
    the HTTP transport is always closed: an orphaned httpx client in a loop that
    runs every five minutes is a slow leak that presents as latency.
    """
    settings = get_settings()
    if not settings.mcp_url:
        raise McpUnavailableError("MCP_URL is not set. Copy .env.example to .env and fill it in.")

    try:
        async with streamablehttp_client(url=settings.mcp_url, timeout=settings.upstream_timeout_s) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    except McpUnavailableError:
        raise
    except BaseException as exc:
        # BaseException, not Exception: an ExceptionGroup raised by the transport's
        # task group does not always inherit from Exception, and catching the
        # narrower type lets the real cause escape past this handler unreported.
        if isinstance(exc, KeyboardInterrupt | SystemExit):
            raise
        raise McpUnavailableError(f"MCP session against {settings.mcp_url} failed: {flatten_exception(exc)}") from exc


async def list_tool_names(session: ClientSession) -> list[str]:
    result = await session.list_tools()
    return [t.name for t in result.tools]


async def call_tool(session: ClientSession, name: str, arguments: dict[str, Any], *, agent: str = "", approval: Approval | None = None) -> Any:
    """Invoke a tool and return its parsed payload.

    The upstream returns structured errors as an `isError` result whose text is a
    JSON envelope carrying `category` and `retriable` (rung II.12). That envelope is
    returned AS DATA rather than raised, because classification and retry policy
    belong to different layers: the server saw the status line, the caller owns the
    budget. Raising here would take that decision away from the only process that
    has a deadline.

    The one exception is `assert_callable`, which RAISES. Every MCP invocation goes
    through this function, so it is the one place a write against the live ranch can
    be refused no matter who wired the call. From M6 a write needs an `Approval` minted by
    `src/agent/gate.py` after a human resumed the pause; a write tool reaching this line
    without one means this repo is wired wrong, and that is not a fact a caller gets to
    weigh against its budget. See `src/tools/allowlists.py`.
    """
    assert_callable(name, agent=agent, approval=approval)
    result = await session.call_tool(name, arguments)
    # isinstance, not `getattr(c, "text", None)`. A content union that also holds image,
    # audio, and resource-link blocks is not narrowed by a duck-typed attribute probe,
    # so the getattr version type-checks by accident and would silently concatenate the
    # empty string if the upstream ever returned a non-text block.
    blob = "\n".join(c.text for c in result.content if isinstance(c, TextContent))
    try:
        parsed = json.loads(blob) if blob else None
    except json.JSONDecodeError:
        parsed = {"error": {"category": "bad_response", "retriable": True, "message": "upstream returned non-JSON text", "body": blob[:500]}}
    if result.isError:
        log.warning("mcp_tool_error", tool=name, payload=parsed)
    return parsed


@dataclass(frozen=True)
class SensorRef:
    """A catalog entry. Topology only: no live value, by the resource's design."""

    sensor_id: str
    sensor_type: str
    location: str
    status: str = "unknown"
    coordinates: dict[str, float] | None = None


@dataclass(frozen=True)
class RanchMap:
    """The whole sensor topology as one read, instead of paging `list_sensors`.

    This is the rung II.11 lesson made structural. An agent that pages a collection
    cannot tell an empty page from the end of the list, and a local model handed a
    pager burns its whole budget navigating and then reports an all-clear. Learning
    the lay of the land once removes the navigation problem rather than prompting
    around it.
    """

    sensors: tuple[SensorRef, ...] = field(default_factory=tuple)

    @property
    def locations(self) -> tuple[str, ...]:
        return tuple(sorted({s.location for s in self.sensors}))

    @property
    def types(self) -> tuple[str, ...]:
        return tuple(sorted({s.sensor_type for s in self.sensors}))

    def of_type(self, *sensor_types: str) -> tuple[SensorRef, ...]:
        wanted = set(sensor_types)
        return tuple(s for s in self.sensors if s.sensor_type in wanted)

    def at_location(self, location: str) -> tuple[SensorRef, ...]:
        return tuple(s for s in self.sensors if s.location == location)

    def get(self, sensor_id: str) -> SensorRef | None:
        return next((s for s in self.sensors if s.sensor_id == sensor_id), None)


def _coerce_sensor(raw: dict[str, Any], fallback_location: str) -> SensorRef:
    coords = raw.get("coordinates")
    return SensorRef(
        sensor_id=str(raw.get("id") or raw.get("sensorId") or ""),
        sensor_type=str(raw.get("type") or raw.get("sensorType") or "unknown"),
        location=str(raw.get("locationName") or raw.get("location") or fallback_location),
        status=str(raw.get("status") or "unknown"),
        coordinates=coords if isinstance(coords, dict) else None,
    )


def parse_ranch_map(payload: Any) -> RanchMap:
    """Tolerant of both shapes the resource could reasonably return.

    The upstream groups by location today. Written to also accept a flat list so a
    harmless upstream reshape degrades into a different code path rather than an
    empty map, which would look exactly like a calm ranch.
    """
    sensors: list[SensorRef] = []

    if isinstance(payload, dict):
        payload = payload.get("data", payload)

    if isinstance(payload, dict):
        groups = payload.get("locations", payload)
        if isinstance(groups, list):  # [{ locationName, sensors: [...] }, ...]
            for group in groups:
                if not isinstance(group, dict):
                    continue
                name = str(group.get("locationName") or group.get("location") or group.get("name") or "unknown")
                for raw in group.get("sensors") or []:
                    if isinstance(raw, dict):
                        sensors.append(_coerce_sensor(raw, name))
        elif isinstance(groups, dict):  # { "Home Place": [...], ... }
            for name, entries in groups.items():
                if isinstance(entries, list):
                    for raw in entries:
                        if isinstance(raw, dict):
                            sensors.append(_coerce_sensor(raw, str(name)))
    elif isinstance(payload, list):
        for raw in payload:
            if isinstance(raw, dict):
                sensors.append(_coerce_sensor(raw, "unknown"))

    return RanchMap(sensors=tuple(s for s in sensors if s.sensor_id))


async def read_ranch_map(session: ClientSession) -> RanchMap:
    from pydantic import AnyUrl

    result = await session.read_resource(AnyUrl(RANCH_MAP_URI))
    blob = "\n".join(c.text for c in result.contents if isinstance(c, TextResourceContents))
    if not blob:
        raise McpUnavailableError(f"{RANCH_MAP_URI} returned no text content")
    return parse_ranch_map(json.loads(blob))
