"""Three log streams, JSON Lines, one instrument built before the thing it measures.

    logs/tick.jsonl    one line per tick        the heartbeat
    logs/agent.jsonl   one line per model call  the instrument
    logs/audit.jsonl   one line per side effect the receipt

Every line in all three carries `run_id` and `tick`, so the streams join on a grep.

structlog over the stdlib rather than beside it: the files have to be machine
readable (the read API and the eval rails both parse them) while the console has
to be human readable during a thirty-minute watch. One call, both outputs. Going
over the stdlib means httpx and langchain chatter lands in this stream instead of
a second, differently-formatted one.
"""

from __future__ import annotations

import atexit
import json
import logging
import logging.handlers
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from src.utils.config import get_settings

# Never written to a log, at any level, in any stream. Prompts and responses are
# the token bill and they are enormous; secrets are secrets.
_SECRET_KEYS = frozenset({"anthropic_api_key", "api_key", "database_url", "database_url_test", "authorization", "password", "token"})
_BULK_KEYS = frozenset({"prompt", "prompt_body", "response_body", "messages", "transcript", "system_prompt"})

TICK_STREAM = "sweetwater.tick"
AGENT_STREAM = "sweetwater.agent"
AUDIT_STREAM = "sweetwater.audit"

_configured = False
_run_id = ""


# --------------------------------------------------------------------------- #
# processors
# --------------------------------------------------------------------------- #
def _utc_ms_timestamp(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """UTC ISO 8601 with milliseconds, matching the ranch APIs exactly.

    structlog's own TimeStamper emits microseconds. The four upstream services all
    emit `2026-08-10T14:30:00.000Z`, and lexicographic ordering across mixed
    precision is a bug waiting for a quiet afternoon.
    """
    event_dict["ts"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{datetime.now(UTC).microsecond // 1000:03d}Z"
    return event_dict


def _redact(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Drop secrets outright; drop bulk bodies unless LOG_TRANSCRIPTS is on.

    Bulk keys are replaced with a marker rather than deleted, so a reader can tell
    "this call had a prompt we chose not to store" from "this call had no prompt."
    """
    keep_bulk = get_settings().log_transcripts
    for key in list(event_dict):
        lowered = key.lower()
        if lowered in _SECRET_KEYS:
            event_dict[key] = "[redacted]"
        elif lowered in _BULK_KEYS and not keep_bulk:
            event_dict[key] = "[omitted: set LOG_TRANSCRIPTS=1]"
    return event_dict


def _rename_event(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """`event` is structlog's positional field; in these files the useful name is `msg`."""
    if "event" in event_dict:
        event_dict["msg"] = event_dict.pop("event")
    return event_dict


# --------------------------------------------------------------------------- #
# setup
# --------------------------------------------------------------------------- #
def _foreign_chain() -> list[Any]:
    """The same processors, applied to log records that did NOT come from structlog.

    httpx, sqlalchemy, langchain, and alembic all log through the stdlib. Without this
    chain their records skip `_rename_event`, so the message lands under `event` while
    every renderer here is looking for `msg`, and the line prints as
    `event='Running upgrade -> 0001'` with no level, no timestamp, and no `run_id`.
    Caught in M1 by reading the output of a migration this repo had just documented.
    """
    return [structlog.contextvars.merge_contextvars, structlog.stdlib.add_log_level, structlog.stdlib.ExtraAdder(), _utc_ms_timestamp, _redact, _rename_event]


def _file_handler(path: Path, *, daily: bool) -> logging.Handler:
    """Rotation differs by stream, and the difference is the point.

    tick and agent are diagnostics: bounded by size, oldest discarded. audit is a
    receipt: rotated by date and never truncated by size, because a size cap on an
    audit trail means the trail ends exactly when the ranch got busiest.
    """
    handler: logging.Handler
    if daily:
        handler = logging.handlers.TimedRotatingFileHandler(path, when="midnight", utc=True, backupCount=0, encoding="utf-8")
    else:
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=get_settings().log_rotate_bytes, backupCount=5, encoding="utf-8")
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, structlog.processors.JSONRenderer()],
            foreign_pre_chain=_foreign_chain(),
        )
    )
    return handler


def configure_logging(run_id: str | None = None) -> str:
    """Wire all three streams plus the console. Idempotent; returns the run id."""
    global _configured, _run_id

    settings = get_settings()
    if _configured:
        return _run_id

    _run_id = run_id or uuid.uuid4().hex[:12]
    settings.log_path.mkdir(parents=True, exist_ok=True)

    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        _utc_ms_timestamp,
        _redact,
        _rename_event,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    console = logging.StreamHandler()
    console.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.dev.ConsoleRenderer(colors=False, event_key="msg") if settings.log_console_pretty else structlog.processors.JSONRenderer(),
            ],
            foreign_pre_chain=_foreign_chain(),
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(console)
    root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

    # httpx logs a line per request. At 160 sensor reads a tick that is 160 lines of
    # noise burying the one line that matters.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    for name, filename, daily in ((TICK_STREAM, "tick.jsonl", False), (AGENT_STREAM, "agent.jsonl", False), (AUDIT_STREAM, "audit.jsonl", True)):
        stream_logger = logging.getLogger(name)
        stream_logger.handlers.clear()
        stream_logger.addHandler(_file_handler(settings.log_path / filename, daily=daily))
        stream_logger.addHandler(console)  # a human watching the console still sees these
        stream_logger.setLevel(logging.INFO)
        stream_logger.propagate = False  # a tick line must never also land in the root stream

    structlog.contextvars.bind_contextvars(run_id=_run_id, tick=0)
    atexit.register(logging.shutdown)
    _configured = True
    return _run_id


def get_run_id() -> str:
    return _run_id


def bind_tick(tick: int) -> None:
    """Stamp every subsequent line with this tick number until the next call."""
    structlog.contextvars.bind_contextvars(tick=tick)


def get_logger(name: str = "sweetwater") -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger


# --------------------------------------------------------------------------- #
# the three streams
# --------------------------------------------------------------------------- #
def log_tick(**fields: Any) -> None:
    """One line per tick, written at tick end, ALWAYS, including when the tick failed.

    A tick that produces no line is indistinguishable from a dead loop, and the
    whole reason this file exists is to be able to tell those apart at 2am.
    """
    get_logger(TICK_STREAM).info("tick", **fields)


def log_agent_call(
    *,
    agent: str,
    tier: int,
    provider: str,
    model: str,
    finish_reason: str,
    latency_ms: int,
    input_tokens: int = 0,
    output_tokens: int = 0,
    tool_calls: int = 0,
    **fields: Any,
) -> None:
    """One line per model call, written IMMEDIATELY on return, BEFORE validation runs.

    `finish_reason` is required and positional-by-keyword on purpose. It is the only
    field that separates "the model is too weak" from "the model never got to
    answer": `length` means the output budget ran out mid-reasoning, `stop` means it
    answered and answered badly. One is a config bug, one is a model-selection
    decision, and in the response text they look identical.

    Before validation, so a response that fails a check still leaves a receipt of
    what was actually returned rather than vanishing into a retry.
    """
    get_logger(AGENT_STREAM).info(
        "agent_call",
        agent=agent,
        tier=tier,
        provider=provider,
        model=model,
        finish_reason=finish_reason,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        tool_calls=tool_calls,
        **fields,
    )


def new_audit_id() -> str:
    return uuid.uuid4().hex


def log_audit_proposed(*, audit_id: str, tool: str, args: dict[str, Any], proposed_by: str, incident_key: str | None = None, **fields: Any) -> None:
    """First of the two lines a side effect writes."""
    get_logger(AUDIT_STREAM).info("proposed", audit_id=audit_id, phase="proposed", tool=tool, args=args, proposed_by=proposed_by, incident_key=incident_key, **fields)


def log_audit_decided(*, audit_id: str, decision: str, decided_by: str, result: str, latency_to_decision_ms: int, **fields: Any) -> None:
    """Second of the two lines, correlated to the first by `audit_id`.

    Two lines rather than one, deliberately: a `proposed` with no matching `decided`
    is a pause nobody ever answered, and that should read as a dangling record you
    can grep for, not as an absence you have to already suspect.
    """
    get_logger(AUDIT_STREAM).info(
        "decided", audit_id=audit_id, phase="decided", decision=decision, decided_by=decided_by, result=result, latency_to_decision_ms=latency_to_decision_ms, **fields
    )


def write_transcript(*, tick: int, agent: str, payload: dict[str, Any]) -> Path | None:
    """Full prompt and response bodies, only when LOG_TRANSCRIPTS=1.

    Off by default because prompts dwarf everything else on disk. Invaluable for
    exactly one job: a finding that reads wrong and a log that cannot say why.
    """
    settings = get_settings()
    if not settings.log_transcripts:
        return None
    out_dir = settings.log_path / "transcripts" / (_run_id or "unknown")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{tick:06d}-{agent}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


class Stopwatch:
    """Elapsed milliseconds, because `latency_ms` appears in two of the three streams."""

    def __init__(self) -> None:
        self._start = time.perf_counter()

    def __enter__(self) -> Stopwatch:
        self._start = time.perf_counter()
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    @property
    def ms(self) -> int:
        return int((time.perf_counter() - self._start) * 1000)
