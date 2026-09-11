"""The API rails. M8, with `src/api/`.

httpx against the ASGI app: no server, no port, and the app's lifespan run by hand so the engine
it builds is the one the routes read. What is proven, per `tests/CLAUDE.md`:

  * the `{data, meta}` envelope and the error shape on every route, including the errors, because
    a window that has to special-case one of five services grows a translation layer
  * the status split is the ranch's: a bad `limit` is 422 naming the field, a body that is not
    JSON is 400, an unknown route is 404 in the envelope, an unexpected exception is 500 in it
  * `X-Request-ID` is echoed when given and minted when not, on successes and on errors
  * `/health` answers with the database URL pointed at nothing, because it never touches one
  * the gate over HTTP: approve and reject a real paused write planted on `sw_ops_test` (M6's
    method, no paid tick), every `audit_id` then appears exactly twice in the log and exactly
    twice in `sw_ops.audit_receipts`, a second decision is 409, a POST without the token is 401
    and changes nothing, and `decided_by` is the token's name whatever the body says
  * SSE: at least one event lands from a row inserted after the connection opened, and
    `Last-Event-ID` resumes after the cursor rather than replaying the latest

Every store-backed rail here runs on `sw_ops_test` through `migrated_store`, never Supabase, and
the fake performer means no ranch is ever on the wire.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine
from structlog.testing import capture_logs

import main as entrypoint
from src.agent.gate import WriteProposal, build_gate, pending, propose, unpaired_audit_ids
from src.agent.memory import (
    SCHEMA_TEST,
    StoreTarget,
    audit_receipts,
    build_engine,
    checkpointer,
    incidents,
    insert_shift_report,
    insert_tick,
    session_factory,
)
from src.api.routes import REQUEST_ID_HEADER, create_app
from src.tools.allowlists import Approval
from src.utils.config import Settings

SECRET = "correct-horse-battery-staple-01"
TOKENS = {SECRET: "scooter", "second-approver-secret-0002": "renee"}
T0 = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
async def api_target(migrated_store: str) -> AsyncIterator[StoreTarget]:
    """`sw_ops_test` with every table the API reads emptied, so one rail's rows are not the next rail's data."""
    engine = build_engine(migrated_store, schema=SCHEMA_TEST)
    try:
        async with engine.begin() as conn:
            for table in ("incidents", "ticks", "shift_reports", "audit_receipts", "checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                await conn.execute(text(f"truncate table {table} restart identity" if table in ("incidents", "ticks", "shift_reports") else f"truncate table {table}"))
    finally:
        await engine.dispose()
    yield StoreTarget(name="test", url=migrated_store, schema=SCHEMA_TEST)


@pytest.fixture
def performed() -> list[tuple[str, dict[str, object], Approval]]:
    return []


@pytest.fixture
async def app(api_target: StoreTarget, settings: Settings, performed: list[tuple[str, dict[str, object], Approval]]) -> AsyncIterator[FastAPI]:
    """The whole API on the test ledger, its lifespan run so `app.state.engine` exists, and a
    performer that records instead of reaching the ranch."""

    async def _perform(tool: str, args: dict[str, object], approval: Approval) -> tuple[str, str]:
        performed.append((tool, args, approval))
        return "written", '{"ok": true}'

    settings.api_stream_poll_seconds = 0.05
    application = create_app(target=api_target, settings=settings, perform=_perform, tokens=TOKENS)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://ops.test") as c:
        yield c


def _engine(app: FastAPI) -> AsyncEngine:
    engine: AsyncEngine = app.state.engine
    return engine


async def _plant_incident(app: FastAPI, *, key: str, subject_type: str = "water_level", status: str = "opened", owner: str | None = "water_feed", seen: datetime = T0) -> None:
    subject, category = key.split(":")
    async with session_factory(_engine(app))() as session:
        await session.execute(
            incidents.insert().values(
                incident_key=key, subject_id=subject, subject_type=subject_type, location="Alkali Flat", category=category, severity="critical", status=status, summary=f"{subject} {category}",
                last_value="1.2", unit="ft", threshold=2.0, occurrences=2, first_seen_at=seen - timedelta(minutes=5), last_seen_at=seen, resolved_at=None, tick_opened=2, tick_last_seen=3, run_id="run-1", owner=owner,
            )
        )
        await session.commit()


def _proposal(key: str = "alkali-flat-water:water_low") -> WriteProposal:
    return WriteProposal(incident_key=key, agent="water_feed", tool="restock_feed", args={"sku": "alkali-flat-water-2", "quantity": 16.7}, tick=1, run_id="run-1")


async def _plant_pauses(target: StoreTarget, *keys: str) -> list[str]:
    """M6's method: a real pause on the test ledger's checkpointer, no paid tick needed."""
    async with checkpointer(target) as saver:
        gate = build_gate(saver)
        return [(await propose(gate, _proposal(k))).audit_id for k in keys]


async def _receipt_counts(app: FastAPI) -> dict[str, int]:
    async with session_factory(_engine(app))() as session:
        rows = (await session.execute(select(audit_receipts.c.audit_id, audit_receipts.c.phase))).all()
    counts: dict[str, int] = {}
    for audit_id, _phase in rows:
        counts[str(audit_id)] = counts.get(str(audit_id), 0) + 1
    return counts


def _auth(secret: str = SECRET) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


# =========================================================================== #
# 1. the envelope, the errors, and the request id
# =========================================================================== #
async def test_health_answers_with_the_database_pointed_at_nothing(settings: Settings) -> None:
    """`/health` never touches the database, so a URL nobody answers is not its problem. The
    engine is built lazily by SQLAlchemy and this proves nothing here forces a connection."""
    nowhere = StoreTarget(name="test", url="postgresql+asyncpg://nobody:nothing@127.0.0.1:1/none", schema=SCHEMA_TEST)
    application = create_app(target=nowhere, settings=settings, tokens=TOKENS)
    async with application.router.lifespan_context(application), httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://ops.test") as c:
        r = await c.get("/health")
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "ok" and set(r.json()) == {"data"}, "a single resource rides in {data} alone"
    assert r.json()["data"]["time"].endswith("Z") and len(r.json()["data"]["time"]) == 24, "UTC ISO 8601 with milliseconds, like the ranch"
    assert len(r.headers[REQUEST_ID_HEADER]) == 32, "a request id is minted when none is given"


async def test_the_request_id_is_echoed_on_success_and_on_error(client: httpx.AsyncClient) -> None:
    ok = await client.get("/health", headers={REQUEST_ID_HEADER: "window-4711"})
    assert ok.headers[REQUEST_ID_HEADER] == "window-4711"
    missing = await client.get("/ops/nope", headers={REQUEST_ID_HEADER: "window-4712"})
    assert missing.status_code == 404 and missing.headers[REQUEST_ID_HEADER] == "window-4712"
    assert missing.json() == {"error": {"code": "NOT_FOUND", "message": "Not Found", "details": {}}}, "an unknown route is the same error shape as everything else"


async def test_a_bad_limit_is_422_naming_the_field_not_400(client: httpx.AsyncClient) -> None:
    """The ranch's split, copied deliberately: 400 is unparsable, 422 is parsed and refused."""
    for bad in ("0", "501", "lots"):
        r = await client.get("/ops/incidents", params={"limit": bad})
        assert r.status_code == 422, bad
        body = r.json()["error"]
        assert body["code"] == "VALIDATION_ERROR" and body["details"]["field"] == "limit" and body["details"]["reason"], bad
    r = await client.get("/ops/incidents", params={"offset": "-1"})
    assert r.status_code == 422 and r.json()["error"]["details"]["field"] == "offset"


async def test_a_body_that_is_not_json_is_400_and_a_field_that_fails_is_422(client: httpx.AsyncClient) -> None:
    r = await client.post("/ops/gate", content=b"{not json", headers={**_auth(), "Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "MALFORMED_BODY" and r.json()["error"]["details"]["field"] == "body", "the byte offset FastAPI puts in loc is not a field name"
    r = await client.post("/ops/gate", json={"audit_id": "x", "decision": "maybe"}, headers=_auth())
    assert r.status_code == 422 and r.json()["error"]["details"]["field"] == "decision"
    r = await client.post("/ops/gate", json={"audit_id": "x", "decision": "approve", "decided_by": "mallory"}, headers=_auth())
    assert r.status_code == 422 and r.json()["error"]["details"]["field"] == "decided_by", "the body may not name who decided; the token does"


async def test_an_unexpected_exception_is_500_in_the_envelope(app: FastAPI, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(*_a: object, **_k: object) -> object:
        raise RuntimeError("the pooler hiccuped")

    monkeypatch.setattr("src.api.routes.latest_shift_report", _boom)
    r = await client.get("/ops/report", headers={REQUEST_ID_HEADER: "window-500"})
    assert r.status_code == 500 and r.json()["error"]["code"] == "INTERNAL_ERROR" and r.headers[REQUEST_ID_HEADER] == "window-500"
    assert "hiccuped" not in r.text, "the exception text stays in the log; the body carries the request id to find it by"


# =========================================================================== #
# 2. the reads: incidents and the report, from the ledger and nothing else
# =========================================================================== #
async def test_incidents_are_newest_first_with_the_collection_envelope(app: FastAPI, client: httpx.AsyncClient) -> None:
    await _plant_incident(app, key="alkali-flat-water:water_low", seen=T0)
    await _plant_incident(app, key="east-allotment-fence:fence_open", subject_type="fence", owner="infrastructure", seen=T0 + timedelta(minutes=5))
    await _plant_incident(app, key="cow-0903:deceased", subject_type="animal", owner="herd_health", status="ongoing", seen=T0 + timedelta(minutes=10))
    r = await client.get("/ops/incidents")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"data", "meta"} and body["meta"] == {"count": 3, "limit": 100, "offset": 0}
    assert [i["incident_key"] for i in body["data"]] == ["cow-0903:deceased", "east-allotment-fence:fence_open", "alkali-flat-water:water_low"]
    row = body["data"][2]
    assert row["subject_id"] == "alkali-flat-water" and row["subject_type"] == "water_level" and row["severity"] == "critical" and row["status"] == "opened" and row["owner"] == "water_feed"
    assert row["last_seen_at"].startswith("2026-09-10T14:00:00") and isinstance(row["id"], int)


async def test_incident_filters_and_meta_count_is_what_came_back(app: FastAPI, client: httpx.AsyncClient) -> None:
    await _plant_incident(app, key="alkali-flat-water:water_low", seen=T0)
    await _plant_incident(app, key="east-allotment-fence:fence_open", subject_type="fence", owner="infrastructure", seen=T0 + timedelta(minutes=5))
    await _plant_incident(app, key="cow-0903:deceased", subject_type="animal", owner="herd_health", status="ongoing", seen=T0 + timedelta(minutes=10))

    animals = (await client.get("/ops/incidents", params={"subject_type": "animal"})).json()
    assert [i["incident_key"] for i in animals["data"]] == ["cow-0903:deceased"] and animals["meta"]["count"] == 1
    sensors = (await client.get("/ops/incidents", params={"status": "opened"})).json()
    assert {i["incident_key"] for i in sensors["data"]} == {"alkali-flat-water:water_low", "east-allotment-fence:fence_open"}
    owned = (await client.get("/ops/incidents", params={"owner": "infrastructure", "status": "opened"})).json()
    assert [i["incident_key"] for i in owned["data"]] == ["east-allotment-fence:fence_open"]
    page = (await client.get("/ops/incidents", params={"limit": 2, "offset": 2})).json()
    assert page["meta"] == {"count": 1, "limit": 2, "offset": 2}, "count is what this page returned, not what matched"

    for field, value in (("status", "open"), ("owner", "nobody")):
        r = await client.get("/ops/incidents", params={field: value})
        assert r.status_code == 422 and r.json()["error"]["details"]["field"] == field, (field, value)


async def test_the_report_is_the_latest_row_or_a_named_404(app: FastAPI, client: httpx.AsyncClient) -> None:
    empty = await client.get("/ops/report")
    assert empty.status_code == 404 and empty.json()["error"]["code"] == "REPORT_NOT_FOUND"

    async with session_factory(_engine(app))() as session:
        await insert_shift_report(session, run_id="run-1", tick=1, at=T0, report={"source": "code", "headline": "older", "priorities": ["a"]}, incident_keys=["k1"])
        await insert_shift_report(session, run_id="run-1", tick=2, at=T0 + timedelta(minutes=5), report={"source": "model", "headline": "One event across two worlds", "situation": "s", "priorities": ["first", "second"], "linked": ["k1", "k2"], "worlds": ["water_feed", "infrastructure"], "work_orders": 2, "model": "claude-opus-5", "input_tokens": 100, "output_tokens": 20}, incident_keys=["k2", "k1"])
    r = await client.get("/ops/report")
    assert r.status_code == 200 and set(r.json()) == {"data"}
    data = r.json()["data"]
    assert data["headline"] == "One event across two worlds" and data["tick"] == 2 and data["source"] == "model"
    assert data["linked"] == ["k1", "k2"] and data["incident_keys"] == ["k1", "k2"] and data["priorities"] == ["first", "second"] and data["model"] == "claude-opus-5"


# =========================================================================== #
# 3. the gate over HTTP: the CLI with an envelope around it
# =========================================================================== #
async def test_gate_list_shows_a_planted_pause(api_target: StoreTarget, client: httpx.AsyncClient) -> None:
    (audit_id,) = await _plant_pauses(api_target, "alkali-flat-water:water_low")
    r = await client.get("/ops/gate")
    assert r.status_code == 200 and r.json()["meta"]["count"] == 1
    item = r.json()["data"][0]
    assert item["audit_id"] == audit_id and item["tool"] == "restock_feed" and item["args"] == {"sku": "alkali-flat-water-2", "quantity": 16.7} and item["agent"] == "water_feed" and item["age_s"] >= 0


async def test_a_post_without_the_token_is_401_and_changes_nothing(api_target: StoreTarget, app: FastAPI, client: httpx.AsyncClient, performed: list[Any]) -> None:
    (audit_id,) = await _plant_pauses(api_target, "alkali-flat-water:water_low")
    for headers in ({}, {"Authorization": "Bearer wrong-wrong-wrong-wrong-wrong"}, {"Authorization": f"Basic {SECRET}"}, {"Authorization": f"Bearer {SECRET[:-1]}"}):
        r = await client.post("/ops/gate", json={"audit_id": audit_id, "decision": "approve"}, headers=headers)
        assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED" and r.headers["WWW-Authenticate"] == "Bearer", headers
    assert performed == [], "nothing was performed"
    async with checkpointer(api_target) as saver:
        assert [p.audit_id for p in await pending(build_gate(saver))] == [audit_id], "the pause is still open"
    assert set(await _receipt_counts(app)) == {audit_id} and (await _receipt_counts(app))[audit_id] == 1, "one receipt, the proposal; no decision was recorded"
    assert (await client.get("/ops/gate")).json()["meta"]["count"] == 1, "reads stay open"


async def test_approve_and_reject_over_http_land_both_halves_of_every_receipt(api_target: StoreTarget, app: FastAPI, client: httpx.AsyncClient, performed: list[Any]) -> None:
    """The M6 verification, over HTTP: reject one, approve the other, `decided_by` is the token's
    name, the write is performed exactly once, and every `audit_id` appears exactly twice in the
    log AND exactly twice in `audit_receipts` (`docs/issues.md` #10)."""
    with capture_logs() as logs:
        a, b = await _plant_pauses(api_target, "alkali-flat-water:water_low", "windmill-pasture-water:water_low")
        assert set(unpaired_audit_ids(logs)) == {a, b}, "both pending, both dangling on purpose"

        rejected = await client.post("/ops/gate", json={"audit_id": a, "decision": "reject", "reason": "tank 2 is fine, no restock"}, headers=_auth())
        approved = await client.post("/ops/gate", json={"audit_id": b, "decision": "approve"}, headers=_auth("second-approver-secret-0002"))

    assert rejected.status_code == 200 and set(rejected.json()) == {"data"}
    assert rejected.json()["data"]["decision"] == "reject" and rejected.json()["data"]["result"] == "not_executed" and rejected.json()["data"]["decided_by"] == "scooter" and rejected.json()["data"]["reason"] == "tank 2 is fine, no restock"
    assert approved.status_code == 200
    assert approved.json()["data"]["result"] == "written" and approved.json()["data"]["decided_by"] == "renee" and approved.json()["data"]["upstream"] == '{"ok": true}' and approved.json()["data"]["latency_to_decision_ms"] >= 0
    assert [(t, args) for t, args, _ in performed] == [("restock_feed", {"sku": "alkali-flat-water-2", "quantity": 16.7})], "exactly one write performed, the approved one, once"
    assert performed[0][2] == Approval(audit_id=b, decided_by="renee")

    assert unpaired_audit_ids(logs) == {}, "every audit_id appears exactly twice in the log once nothing is pending"
    assert await _receipt_counts(app) == {a: 2, b: 2}, "and exactly twice in the table"
    async with session_factory(_engine(app))() as session:
        decided = {str(r.audit_id): r for r in (await session.execute(select(audit_receipts).where(audit_receipts.c.phase == "decided"))).all()}
    assert decided[a].decision == "reject" and decided[a].decided_by == "scooter" and decided[a].result == "not_executed" and decided[a].reason == "tank 2 is fine, no restock"
    assert decided[b].decision == "approve" and decided[b].decided_by == "renee" and decided[b].result == "written" and decided[b].upstream == '{"ok": true}'
    assert (await client.get("/ops/gate")).json()["meta"]["count"] == 0


async def test_a_second_decision_is_409_an_unknown_id_is_404_and_a_bare_reject_is_422(api_target: StoreTarget, client: httpx.AsyncClient, performed: list[Any]) -> None:
    (a,) = await _plant_pauses(api_target, "alkali-flat-water:water_low")
    first = await client.post("/ops/gate", json={"audit_id": a, "decision": "approve"}, headers=_auth())
    assert first.status_code == 200
    again = await client.post("/ops/gate", json={"audit_id": a, "decision": "reject", "reason": "changed my mind"}, headers=_auth())
    assert again.status_code == 409 and again.json()["error"]["code"] == "GATE_ALREADY_DECIDED" and "already decided" in again.json()["error"]["message"] and again.json()["error"]["details"] == {"audit_id": a}
    assert len(performed) == 1, "the write was performed once and the second answer did nothing"

    nope = await client.post("/ops/gate", json={"audit_id": "nope", "decision": "approve"}, headers=_auth())
    assert nope.status_code == 404 and nope.json()["error"]["code"] == "GATE_NOT_FOUND"

    (b,) = await _plant_pauses(api_target, "windmill-pasture-water:water_low")
    bare = await client.post("/ops/gate", json={"audit_id": b, "decision": "reject"}, headers=_auth())
    assert bare.status_code == 422 and bare.json()["error"]["details"]["field"] == "reason"
    async with checkpointer(api_target) as saver:
        assert [p.audit_id for p in await pending(build_gate(saver))] == [b], "a refused decision leaves the pause open"


# =========================================================================== #
# 4. the stream: ticks landing from another process
# =========================================================================== #
async def _insert_tick_later(app: FastAPI, *, delay: float, tick: int, **fields: Any) -> int:
    await asyncio.sleep(delay)
    async with session_factory(_engine(app))() as session:
        return await insert_tick(session, run_id="run-1", tick=tick, at=T0 + timedelta(minutes=5 * tick), fields={"duration_ms": 8140, "store": "test", "cost_usd": 0.0, "opened": 1, "error": None, "failed_stage": None, **fields})


def _events(body: str) -> list[dict[str, str]]:
    """The SSE frames as dicts, comments (the ping) dropped."""
    out: list[dict[str, str]] = []
    for frame in body.split("\n\n"):
        lines = [ln for ln in frame.splitlines() if ln and not ln.startswith(":")]
        if lines:
            out.append(dict(ln.split(": ", 1) for ln in lines))
    return out


async def test_a_tick_inserted_after_the_connection_opened_lands_as_an_event(app: FastAPI, client: httpx.AsyncClient) -> None:
    """The window's whole reason to exist. The table is empty when the stream opens, so the one
    event it closes on (`limit=1`) can only be the row another task inserted after that."""
    inserted = asyncio.create_task(_insert_tick_later(app, delay=0.2, tick=7))
    r = await client.get("/ops/stream", params={"limit": 1})
    row_id = await inserted
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream") and r.headers[REQUEST_ID_HEADER]
    events = _events(r.text)
    assert len(events) == 1 and events[0]["event"] == "tick" and events[0]["id"] == str(row_id)
    data = json.loads(events[0]["data"])
    assert data["tick"] == 7 and data["run_id"] == "run-1" and data["fields"]["opened"] == 1 and data["cost_usd"] == 0.0 and data["at"].startswith("2026-09-10T14:35:00")


async def test_the_latest_tick_is_sent_on_connect_and_last_event_id_resumes_after_it(app: FastAPI, client: httpx.AsyncClient) -> None:
    first = await _insert_tick_later(app, delay=0, tick=1)
    second = await _insert_tick_later(app, delay=0, tick=2)
    fresh = await client.get("/ops/stream", params={"limit": 1})
    assert [e["id"] for e in _events(fresh.text)] == [str(second)], "a window that just opened gets the latest tick, not a blank pane"

    inserted = asyncio.create_task(_insert_tick_later(app, delay=0.2, tick=3))
    resumed = await client.get("/ops/stream", params={"limit": 2}, headers={"Last-Event-ID": str(first)})
    third = await inserted
    assert [e["id"] for e in _events(resumed.text)] == [str(second), str(third)], "resume replays what landed after the cursor, then keeps going"

    bad = await client.get("/ops/stream", params={"limit": 1}, headers={"Last-Event-ID": "yesterday"})
    assert bad.status_code == 422 and bad.json()["error"]["details"]["field"] == "Last-Event-ID"


# =========================================================================== #
# 5. config: who may approve, and the refusal to start without an answer
# =========================================================================== #
@pytest.mark.parametrize(
    ("raw", "problem"),
    [("", "empty"), ("scooter", "name:secret"), ("scooter:", "name:secret"), (":correct-horse-battery-staple-01", "name:secret"), ("scooter:short", "shorter than 16"), ("scooter:correct-horse-battery-staple-01,renee", "name:secret")],
)
def test_ops_api_token_refuses_what_it_cannot_use(settings: Settings, raw: str, problem: str) -> None:
    settings.ops_api_token = raw
    with pytest.raises(ValueError, match=problem):
        settings.ops_tokens()


def test_ops_api_token_maps_each_secret_to_a_name(settings: Settings) -> None:
    settings.ops_api_token = " scooter:correct-horse-battery-staple-01 , renee:second-approver-secret-0002 "
    assert settings.ops_tokens() == {"correct-horse-battery-staple-01": "scooter", "second-approver-secret-0002": "renee"}
    settings.api_cors_origins = "https://sweetwater.vercel.app, http://localhost:3000"
    assert settings.cors_origins == ("https://sweetwater.vercel.app", "http://localhost:3000")
    assert Settings(_env_file=None).api_cors_origins == "" and Settings(_env_file=None).cors_origins == (), "no origin by default: a wildcard in a default is a default that ships"


def test_api_mode_refuses_to_start_without_a_token(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit 2 before a port is bound. A server that boots and approves nothing is a server someone
    will fix by removing the check."""
    settings.ops_api_token = ""
    monkeypatch.setattr("main.get_settings", lambda: settings)

    def _never_serve(*_a: object, **_k: object) -> None:
        raise AssertionError("uvicorn must not start without a usable OPS_API_TOKEN")

    monkeypatch.setattr("uvicorn.run", _never_serve)
    with capture_logs() as logs:
        assert entrypoint.api() == 2
    refusal = next(e for e in logs if e["event"] == "config_incomplete")
    assert refusal["missing"] == ["ops_api_token"]


def test_no_route_here_imports_a_ranch_client_or_a_model_client() -> None:
    """The rule in `src/api/CLAUDE.md`, as a grep: the API reads `sw_ops` and nothing else."""
    from pathlib import Path

    for name in ("routes.py", "schemas.py"):
        source = (Path(__file__).resolve().parents[1] / "src" / "api" / name).read_text(encoding="utf-8")
        for forbidden in ("src.tools.mcp_client", "src.tools.sensors", "src.tools.herd", "src.tools.chaos", "src.models", "src.agent.executor", "src.agent.workers", "httpx"):
            assert forbidden not in source, f"{name} imports {forbidden}; the API never calls the ranch or a model"
