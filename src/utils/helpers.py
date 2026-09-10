"""Small shared primitives. Nothing here knows a domain."""

from __future__ import annotations

import asyncio
import random
import re
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TypeVar

import httpx

from src.utils.config import get_settings

T = TypeVar("T")


def utc_now_iso() -> str:
    """UTC ISO 8601 with milliseconds, matching the four upstream services exactly.

    Lexicographic ordering is relied upon, so the precision has to match theirs or
    sorting a mixed set silently misorders.
    """
    now = datetime.now(UTC)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def chunked(items: Sequence[T], size: int) -> Iterable[Sequence[T]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def as_strings(raw: object) -> tuple[str, ...]:
    """A tuple of non-empty stripped strings, tolerantly. A forced tool call is not a guarantee.

    Shared between the work-order rails and the shift-report rails, which parse two different
    schemas with the same three list fields in them.
    """
    if isinstance(raw, str):
        return (raw.strip(),) if raw.strip() else ()
    if isinstance(raw, list):
        return tuple(str(item).strip() for item in raw if str(item).strip())
    return ()


#: An instruction that instructs nobody to do anything. Anchored at the start, so "check the
#: float and monitor the level after the haul" survives: that one has a person doing something
#: first, and a rail that punishes accurate writing gets switched off inside a week.
#:
#: Lives here rather than in `workers.py` because two rails need exactly this test - a work
#: order's `actions` and a shift report's `priorities` - and they are in modules that cannot
#: import each other. Two copies of one regex is two regexes by the second time somebody
#: widens one of them.
NO_OP_INSTRUCTION = re.compile(r"^\W*(no action|none|nothing|no further|monitor|continue to monitor|keep monitoring|observe|await|wait and see|take no)\b", re.IGNORECASE)


def has_no_real_instruction(items: Sequence[str]) -> bool:
    """True when a list of instructions tells nobody to do anything. The all-clear rail's core."""
    return not items or all(NO_OP_INSTRUCTION.match(item) for item in items)


@asynccontextmanager
async def upstream_client(base_url: str = "") -> AsyncIterator[httpx.AsyncClient]:
    """An httpx client carrying the deadline every upstream call must respect.

    A deadline protects the waiter, never the callee. The tick loop has a budget;
    an upstream that hangs must not be allowed to spend it.
    """
    settings = get_settings()
    limits = httpx.Limits(max_connections=settings.sweep_concurrency * 2, max_keepalive_connections=settings.sweep_concurrency)
    async with httpx.AsyncClient(base_url=base_url, timeout=settings.upstream_timeout_s, limits=limits, headers={"Accept": "application/json"}) as client:
        yield client


def backoff_delay(attempt: int, *, base: float = 1.0, cap: float = 60.0, rng: random.Random | None = None) -> float:
    """Exponential backoff with full jitter.

    Jittered because the sweep fires 160 requests at once: on a shared outage every
    one of them would otherwise retry in lockstep and rebuild the exact thundering
    herd the backoff was added to avoid.
    """
    ceiling = min(cap, base * (2**attempt))
    return (rng or random).uniform(0, ceiling)


async def gather_bounded(coros: Sequence[object], *, limit: int, sem: asyncio.Semaphore | None = None) -> list[object]:
    """asyncio.gather with a concurrency ceiling.

    160 unbounded requests against API Gateway is a wall of Lambda cold starts and
    reads to the other side as a load test, not a monitoring sweep.

    **Pass `sem` when the ceiling has to hold across several calls to this function.**
    A private semaphore per call bounds one caller and nothing else, which is correct for
    the sweep (one call, 160 reads) and wrong for M3's fan-out: four agents each bounding
    themselves at four is sixteen concurrent Opus calls, and the limit that was written down
    is four. `limit` is ignored when `sem` is given, because the shared object already
    carries the number and two sources for one ceiling is how they disagree.
    """
    semaphore = sem or asyncio.Semaphore(limit)

    async def _run(coro: object) -> object:
        async with semaphore:
            return await coro  # type: ignore[misc]

    return list(await asyncio.gather(*(_run(c) for c in coros), return_exceptions=True))
