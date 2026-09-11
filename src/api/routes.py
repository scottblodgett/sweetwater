"""The read API: the app factory, the envelope, the errors, the request id, CORS, and the routes. M8.

`/health`, `/ops/incidents`, `/ops/report`, `/ops/stream` (SSE), `/ops/gate` (list, and approve
or reject). Read-only apart from the gate decision, and even that resumes a paused graph rather
than touching the ranch from here: `gate.decide()` does the work, exactly as the CLI does.

`create_app` is a factory, not a module-level app, so a rail can hand it the test ledger and a
fake performer and get a whole API that never opens a ranch connection. `main.py --api` calls it
with what `resolve_store()` and `Settings` say. One module rather than an `app.py` beside this,
because `docs/plan.md` names `routes.py` and `schemas.py` and the tree matches the plan.

Two rules from `src/api/CLAUDE.md` live in the factory rather than in the routes:

  * **Every request carries `X-Request-ID`**, accepted if given and generated if not, and it is
    echoed on every response including the ones the error handlers write. Done as a pure ASGI
    middleware rather than `BaseHTTPMiddleware`, because the latter buffers a streaming body and
    `/ops/stream` is a streaming body.
  * **Every error is the same shape.** FastAPI's own validation error, Starlette's 404 for an
    unknown route, an `ApiError` a route raised on purpose, and an exception nobody expected all
    come out as `{error: {code, message, details}}`. The status split is the ranch's: 400
    unparsable, 404 not found, 409 conflict, 422 validation, 500 unexpected.

This module never imports the ranch clients or a model client. It reads `sw_ops` through
`src/agent/memory.py`, and that is the whole architecture: a browser refresh cannot spend a
token or fire a sweep.
"""

from __future__ import annotations

import asyncio
import secrets as secretlib
import uuid
from collections.abc import AsyncIterator, MutableMapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

from src.agent.gate import GateAlreadyDecided, GateInvalidDecision, GateNotFound, Performer, build_gate, decide, pending
from src.agent.memory import (
    CheckpointerNotMigratedError,
    StoreTarget,
    build_engine,
    checkpointer,
    latest_shift_report,
    latest_tick,
    list_incidents,
    session_factory,
    ticks_after,
)
from src.agent.state import IncidentStatus
from src.api.schemas import AgentName, GateDecisionIn, GateDecisionOut, HealthOut, IncidentOut, PendingWriteOut, ShiftReportOut, TickOut
from src.utils.config import Settings, get_settings
from src.utils.helpers import utc_now_iso
from src.utils.logger import get_logger

log = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


class ApiError(Exception):
    """An error a route raises on purpose, already in the ranch's shape."""

    def __init__(self, status: int, code: str, message: str, details: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}
        self.headers = headers or {}


def error_response(status: int, code: str, message: str, details: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "details": details or {}}}, headers=headers)


class RequestIdMiddleware:
    """Accept `X-Request-ID` or mint one, stash it on the scope, echo it on every response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        given = next((v.decode("latin-1") for k, v in scope.get("headers", []) if k.decode("latin-1").lower() == REQUEST_ID_HEADER.lower()), "").strip()
        request_id = given[:128] or uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_id(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != REQUEST_ID_HEADER.lower().encode("latin-1")]
                headers.append((REQUEST_ID_HEADER.lower().encode("latin-1"), request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_id)


def _validation_details(exc: RequestValidationError) -> tuple[bool, dict[str, Any]]:
    """`(unparsable, details)`. Unparsable means the body was not JSON at all, which is the
    ranch's 400; everything else parsed and failed a rule, which is its 422 naming the field."""
    errors = list(exc.errors())
    first = errors[0] if errors else {}
    loc = [str(p) for p in first.get("loc", ()) if p not in ("body", "query", "path", "header")]
    # `json_invalid` carries the byte offset in `loc` (`("body", 1)`), a field error carries the name.
    unparsable = str(first.get("type", "")) in {"json_invalid", "json_type"} and str(first.get("loc", ("",))[0]) == "body"
    return unparsable, {"field": "body" if unparsable else ".".join(loc) or "body", "reason": str(first.get("msg", "invalid"))}


router = APIRouter()

#: `/ops/incidents` page size. The window paginates; nothing needs the whole ledger in one body.
LIMIT_DEFAULT = 100
LIMIT_MAX = 500


def _engine(request: Request) -> AsyncEngine:
    engine: AsyncEngine = request.app.state.engine
    return engine


def _sessions(request: Request) -> async_sessionmaker[AsyncSession]:
    return session_factory(_engine(request))


def _target(request: Request) -> StoreTarget:
    target: StoreTarget = request.app.state.target
    return target


def _envelope(data: Any, *, limit: int | None = None, offset: int | None = None) -> dict[str, Any]:
    if isinstance(data, list):
        return {"data": data, "meta": {"count": len(data), "limit": limit, "offset": offset}}
    return {"data": data}


# --------------------------------------------------------------------------- #
# auth: who may approve a write over HTTP
# --------------------------------------------------------------------------- #
async def approver(request: Request, authorization: Annotated[str | None, Header()] = None) -> str:
    """The name behind the bearer token, or 401. `decided_by` comes from here and nowhere else."""
    tokens: dict[str, str] = request.app.state.tokens
    scheme, _, presented = (authorization or "").strip().partition(" ")
    presented = presented.strip()
    if scheme.lower() == "bearer" and presented:
        # Compare against every secret rather than looking the token up: a dict probe leaks
        # which prefix matched through timing, and `compare_digest` is the whole point.
        for secret, name in tokens.items():
            if secretlib.compare_digest(secret.encode(), presented.encode()):
                return name
    raise ApiError(401, "UNAUTHORIZED", "POST /ops/gate needs a bearer token from OPS_API_TOKEN", headers={"WWW-Authenticate": "Bearer"})


# --------------------------------------------------------------------------- #
# routes
# --------------------------------------------------------------------------- #
@router.get("/health")
async def health() -> dict[str, Any]:
    # Never touches the database, on purpose, and do not add a SELECT 1 here to make it "more
    # useful": a liveness check that can fail for a database reason cannot distinguish "down"
    # from "degraded", and a restart policy reading it would bounce a healthy process because
    # Supabase hiccuped. Database health is visible where it belongs, on the tick line.
    return _envelope(HealthOut(time=utc_now_iso()).model_dump())


@router.get("/ops/incidents")
async def incidents(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=LIMIT_MAX)] = LIMIT_DEFAULT,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: Annotated[IncidentStatus | None, Query()] = None,
    owner: Annotated[AgentName | None, Query()] = None,
    subject_type: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
) -> dict[str, Any]:
    """Newest first by `last_seen_at`. `meta.count` is what came back, not what matched."""
    async with _sessions(request)() as session:
        rows = await list_incidents(session, status=status, owner=owner, subject_type=subject_type, limit=limit, offset=offset)
    return _envelope([IncidentOut.from_incident(i).model_dump(mode="json") for i in rows], limit=limit, offset=offset)


@router.get("/ops/report")
async def report(request: Request) -> dict[str, Any]:
    async with _sessions(request)() as session:
        row = await latest_shift_report(session)
    if row is None:
        raise ApiError(404, "REPORT_NOT_FOUND", "no shift report has been written to this ledger yet; one lands at the end of every tick")
    return _envelope(ShiftReportOut(**row).model_dump(mode="json"))


#: A comment frame this often while nothing lands, so a proxy between the window and this box
#: does not decide a five-minute silence is a dead connection.
PING_SECONDS = 15.0
#: The SSE line separator. Named because the wire format is line-oriented and a literal in an
#: f-string is one shell escape away from becoming a real newline (which is how this was first written).
LF = chr(10)


def _frame(row: dict[str, Any]) -> bytes:
    """One SSE frame. `id` is the row id so `Last-Event-ID` can resume from it."""
    return "".join(("event: tick", LF, "id: ", str(row["id"]), LF, "data: ", TickOut(**row).model_dump_json(), LF, LF)).encode()


async def tick_events(sessions: async_sessionmaker[AsyncSession], *, after_id: int | None, poll_s: float, limit: int | None, ping_s: float = PING_SECONDS) -> AsyncIterator[bytes]:
    """The stream. Polls `sw_ops.ticks` for rows past the cursor and yields each as one frame.

    With no `Last-Event-ID` the latest row is sent first, so a window that just opened has a
    tick to show rather than a blank pane until the next five-minute mark. With one, the stream
    resumes after it and replays whatever landed in between. `limit` closes the stream after that
    many events, which is what makes it curl-able and testable; the window omits it.

    Hand-rolled on `StreamingResponse` rather than `sse-starlette`: that library hooks uvicorn's
    exit handler at import and keeps one process-global exit event bound to the first event loop
    it saw, which cancelled this generator mid-query on the second loop of the suite. Twenty lines
    of the wire format beat a dependency with global state. Starlette cancels this generator when
    the client goes away, so there is no disconnect check here and no second `receive()`.
    """
    sent = 0
    cursor = after_id or 0
    if after_id is None:
        async with sessions() as session:
            latest = await latest_tick(session)
        if latest is not None:
            yield _frame(latest)
            cursor = int(latest["id"])
            sent += 1
    idle = 0.0
    while limit is None or sent < limit:
        async with sessions() as session:
            rows = await ticks_after(session, after_id=cursor, limit=(limit - sent) if limit is not None else 100)
        for row in rows:
            yield _frame(row)
            cursor = int(row["id"])
            sent += 1
        if rows:
            idle = 0.0
            continue
        await asyncio.sleep(poll_s)
        idle += poll_s
        if idle >= ping_s:
            yield (": ping" + LF + LF).encode()
            idle = 0.0


@router.get("/ops/stream")
async def stream(request: Request, limit: Annotated[int | None, Query(ge=1, le=10_000)] = None, last_event_id: Annotated[str | None, Header()] = None) -> StreamingResponse:
    after_id: int | None = None
    if last_event_id is not None and last_event_id.strip():
        if not last_event_id.strip().isdigit():
            raise ApiError(422, "VALIDATION_ERROR", "Last-Event-ID: must be a tick row id", {"field": "Last-Event-ID", "reason": "must be a tick row id"})
        after_id = int(last_event_id.strip())
    poll_s: float = request.app.state.poll_s
    headers = {"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}
    return StreamingResponse(tick_events(_sessions(request), after_id=after_id, poll_s=poll_s, limit=limit), media_type="text/event-stream", headers=headers)


@router.get("/ops/gate")
async def gate_list(request: Request) -> dict[str, Any]:
    """The CLI's `list` in an envelope: every proposal still paused, oldest first."""
    now = datetime.now(UTC)
    try:
        async with checkpointer(_target(request)) as saver:
            items = await pending(build_gate(saver, perform=request.app.state.perform))
    except CheckpointerNotMigratedError as exc:
        raise ApiError(500, "CHECKPOINTER_NOT_MIGRATED", str(exc)) from exc
    # Bounded like every other collection, and the bound is never reached: a ledger with five
    # hundred unanswered writes has a bigger problem than a page size.
    return _envelope([PendingWriteOut.from_pending(p, now=now).model_dump() for p in items[:LIMIT_MAX]], limit=LIMIT_MAX, offset=0)


@router.post("/ops/gate")
async def gate_decide(request: Request, body: GateDecisionIn, decided_by: Annotated[str, Depends(approver)]) -> dict[str, Any]:
    """The CLI's `approve` and `reject` in an envelope. `decided_by` is the token's name."""
    try:
        async with checkpointer(_target(request)) as saver:
            final = await decide(build_gate(saver, perform=request.app.state.perform), audit_id=body.audit_id, decision=body.decision, decided_by=decided_by, reason=body.reason)
    except GateNotFound as exc:
        raise ApiError(404, "GATE_NOT_FOUND", str(exc), {"audit_id": body.audit_id}) from exc
    except GateAlreadyDecided as exc:
        raise ApiError(409, "GATE_ALREADY_DECIDED", str(exc), {"audit_id": body.audit_id}) from exc
    except GateInvalidDecision as exc:
        raise ApiError(422, "VALIDATION_ERROR", str(exc), {"field": "reason", "reason": str(exc)}) from exc
    except CheckpointerNotMigratedError as exc:
        raise ApiError(500, "CHECKPOINTER_NOT_MIGRATED", str(exc)) from exc
    log.info("api_gate_decided", audit_id=body.audit_id, decision=body.decision, decided_by=decided_by, result=final.get("result"), request_id=request.scope.get("state", {}).get("request_id"))
    return _envelope(GateDecisionOut.from_state(final).model_dump())


# --------------------------------------------------------------------------- #
# the app
# --------------------------------------------------------------------------- #
def create_app(*, target: StoreTarget, settings: Settings | None = None, perform: Performer | None = None, tokens: dict[str, str] | None = None) -> FastAPI:
    """Build the API against one ledger.

    `tokens` is `{secret: name}`; `None` means read them from `settings.ops_tokens()`, which
    raises on an empty or malformed `OPS_API_TOKEN` so the caller (`main.py`) can refuse to
    start. `perform` is what an approved write calls; production leaves it `None` for
    `gate.perform_write`, a rail injects a fake so no ranch is on the wire.
    """
    cfg = settings or get_settings()
    secrets = tokens if tokens is not None else cfg.ops_tokens()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # `create_async_engine` opens nothing until the first query, so `/health` on a dead
        # database URL still answers: the engine is built here and disposed on shutdown.
        app.state.engine = build_engine(target.url, schema=target.schema)
        log.info("api_started", store=target.name, approvers=sorted(set(secrets.values())), cors_origins=list(cfg.cors_origins), poll_s=cfg.api_stream_poll_seconds)
        try:
            yield
        finally:
            await app.state.engine.dispose()

    app = FastAPI(title="Sweetwater ops", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.target = target
    app.state.perform = perform
    app.state.tokens = secrets
    app.state.poll_s = cfg.api_stream_poll_seconds
    app.include_router(router)

    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message, exc.details, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        unparsable, details = _validation_details(exc)
        if unparsable:
            return error_response(400, "MALFORMED_BODY", "the request body is not valid JSON", details)
        return error_response(422, "VALIDATION_ERROR", f"{details['field']}: {details['reason']}", details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return error_response(exc.status_code, code, str(exc.detail), headers=dict(exc.headers or {}))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        request_id = str(request.scope.get("state", {}).get("request_id", ""))
        log.error("api_unexpected", path=request.url.path, error=f"{type(exc).__name__}: {exc}"[:200], request_id=request_id)
        # Starlette answers a 500 from its outermost layer, above every middleware including the
        # request id one, so this response sets the header itself or the one error a person most
        # needs to correlate would be the one without an id.
        return error_response(500, "INTERNAL_ERROR", "unexpected error; the request id is in the X-Request-ID header and in the API's log", headers={REQUEST_ID_HEADER: request_id} if request_id else None)

    # Outermost last: Starlette wraps in reverse order of `add_middleware`, so the request id is
    # added after CORS and therefore runs before it, which is what puts the header on a preflight.
    if cfg.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(cfg.cors_origins), allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER, "Last-Event-ID"], expose_headers=[REQUEST_ID_HEADER])
    app.add_middleware(RequestIdMiddleware)
    return app
