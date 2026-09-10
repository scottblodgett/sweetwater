"""M0 rails for config and logging.

The instrument is built before the thing it measures, so the instrument gets tests
first. A logger that silently drops `finish_reason`, or a config that quietly turns
`{base}/sensors` into `//sensors`, fails in a way that presents as a model problem
much later.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.tools.mcp_client import flatten_exception, parse_ranch_map
from src.utils.config import Settings
from src.utils.helpers import backoff_delay, utc_now_iso
from src.utils.logger import _redact


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def test_trailing_slash_is_stripped_from_every_upstream() -> None:
    """`f"{base}/sensors"` with a trailing slash yields `//sensors`, which API Gateway
    answers with a 404 that reads like a missing route rather than a formatting bug."""
    s = Settings(mcp_url="https://x/", farm_api="https://f/", feed_api="https://d/", sensor_api="https://s/", care_api="https://c/", _env_file=None)
    assert (s.mcp_url, s.farm_api, s.feed_api, s.sensor_api, s.care_api) == ("https://x", "https://f", "https://d", "https://s", "https://c")


def test_missing_upstreams_reports_all_of_them_at_once() -> None:
    """All of them, not just the first: a config error should take one round trip to fix,
    not four runs discovering one blank variable at a time."""
    s = Settings(mcp_url="https://x", farm_api="", feed_api="", sensor_api="https://s", care_api="", _env_file=None)
    assert set(s.missing_upstreams()) == {"farm_api", "feed_api", "care_api"}
    assert Settings(mcp_url="a", farm_api="b", feed_api="c", sensor_api="d", care_api="e", _env_file=None).missing_upstreams() == []


def test_chaos_cohort_parses_and_tolerates_whitespace() -> None:
    s = Settings(chaos_animal_cohort=" cow-0901, cow-0902 ,, cow-0903 ", _env_file=None)
    assert s.chaos_cohort == ("cow-0901", "cow-0902", "cow-0903")


def test_empty_chaos_cohort_is_empty_not_a_single_blank() -> None:
    """An empty cohort must mean "no animal may be mutated", never a cohort of one
    animal named "". A guard that parses to `('',)` is a guard that passes nothing."""
    assert Settings(chaos_animal_cohort="", _env_file=None).chaos_cohort == ()


# --------------------------------------------------------------------------- #
# logging
# --------------------------------------------------------------------------- #
def test_secrets_are_redacted_and_bulk_bodies_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.utils.logger.get_settings", lambda: Settings(log_transcripts=False, _env_file=None))

    out = _redact(None, "info", {"anthropic_api_key": "sk-ant-real", "database_url": "postgres://u:p@h/db", "prompt": "x" * 5000, "tick": 4})

    assert out["anthropic_api_key"] == "[redacted]"
    assert out["database_url"] == "[redacted]"
    assert "sk-ant-real" not in json.dumps(out)
    assert out["tick"] == 4, "redaction must not touch ordinary fields"
    # A marker, not a deletion: a reader has to be able to tell "there was a prompt we
    # chose not to store" from "there was no prompt."
    assert out["prompt"].startswith("[omitted")


def test_transcripts_flag_lets_bulk_bodies_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.utils.logger.get_settings", lambda: Settings(log_transcripts=True, _env_file=None))
    out = _redact(None, "info", {"prompt": "the real prompt", "anthropic_api_key": "sk-ant-real"})
    assert out["prompt"] == "the real prompt"
    assert out["anthropic_api_key"] == "[redacted]", "a secret is redacted even with transcripts on"


def test_timestamp_has_millisecond_precision_and_z_suffix() -> None:
    """Must match the four upstream services exactly. Lexicographic ordering is relied
    upon, and sorting mixed precision misorders silently."""
    ts = utc_now_iso()
    assert ts.endswith("Z") and len(ts) == len("2026-09-10T14:30:00.000Z"), ts
    assert ts[-5] == "." or ts[19] == ".", ts


# --------------------------------------------------------------------------- #
# exception flattening (the M0 defect, pinned)
# --------------------------------------------------------------------------- #
def test_flatten_exception_unwraps_a_task_group() -> None:
    """`unhandled errors in a TaskGroup (1 sub-exception)` is the same string whether
    the host refused the connection or the server returned a 401. Flatten to the leaf."""
    group = BaseExceptionGroup("unhandled errors in a TaskGroup", [ConnectionRefusedError("All connection attempts failed")])
    assert flatten_exception(group) == "ConnectionRefusedError: All connection attempts failed"


def test_flatten_exception_handles_nesting() -> None:
    inner = BaseExceptionGroup("inner", [ValueError("bad value")])
    outer = BaseExceptionGroup("outer", [inner, TimeoutError("timed out")])
    assert flatten_exception(outer) == "ValueError: bad value; TimeoutError: timed out"


def test_flatten_exception_deduplicates() -> None:
    """160 concurrent reads failing the same way otherwise produce 160 identical leaves
    and a log line nobody scrolls to the end of."""
    group = BaseExceptionGroup("g", [ConnectionRefusedError("refused") for _ in range(160)])
    assert flatten_exception(group) == "ConnectionRefusedError: refused"


def test_flatten_exception_survives_a_message_less_error() -> None:
    assert flatten_exception(BaseExceptionGroup("g", [RuntimeError()])) == "RuntimeError"


# --------------------------------------------------------------------------- #
# ranch map parsing
# --------------------------------------------------------------------------- #
GROUPED = {
    "data": {
        "locations": [
            {"locationName": "Alkali Flat", "sensors": [{"id": "alkali-flat-water", "type": "water-level", "status": "ok", "coordinates": {"x": 12.0, "y": 40.5}}]},
            {"locationName": "East Allotment", "sensors": [{"id": "east-allotment-fence", "type": "fence-voltage", "status": "ok"}]},
        ]
    }
}
FLAT = [{"id": "home-place-diesel", "type": "fuel-level", "locationName": "Home Place", "status": "ok"}]


def test_parses_the_location_grouped_shape() -> None:
    m = parse_ranch_map(GROUPED)
    assert len(m.sensors) == 2
    assert m.locations == ("Alkali Flat", "East Allotment")
    assert m.types == ("fence-voltage", "water-level")
    tank = m.get("alkali-flat-water")
    assert tank is not None and tank.location == "Alkali Flat" and tank.coordinates == {"x": 12.0, "y": 40.5}


def test_parses_a_flat_list_too() -> None:
    """Tolerant on purpose: a harmless upstream reshape should degrade into a different
    code path, not into an empty map. An empty map looks exactly like a calm ranch."""
    m = parse_ranch_map(FLAT)
    assert len(m.sensors) == 1 and m.sensors[0].location == "Home Place"


def test_filters_by_type_and_location() -> None:
    """`GET /sensors` takes pagination only, so type filtering has to happen here."""
    m = parse_ranch_map(GROUPED)
    assert [s.sensor_id for s in m.of_type("water-level")] == ["alkali-flat-water"]
    assert [s.sensor_id for s in m.at_location("East Allotment")] == ["east-allotment-fence"]
    assert m.of_type("wellhead-pressure") == ()


def test_entries_without_an_id_are_dropped() -> None:
    m = parse_ranch_map([{"type": "water-level"}, {"id": "ok-1", "type": "gate"}])
    assert [s.sensor_id for s in m.sensors] == ["ok-1"]


# --------------------------------------------------------------------------- #
# backoff
# --------------------------------------------------------------------------- #
def test_backoff_is_jittered_and_capped() -> None:
    """Jittered because the sweep fires 160 requests at once: on a shared outage,
    unjittered retries rebuild the exact thundering herd backoff was added to avoid."""
    import random

    rng = random.Random(1)
    delays = [backoff_delay(a, rng=rng) for a in range(8)]
    assert all(0 <= d <= 60.0 for d in delays)
    assert len(set(delays)) > 1, "identical delays across attempts means no jitter"
    assert backoff_delay(50, base=1.0, cap=60.0, rng=rng) <= 60.0


# --------------------------------------------------------------------------- #
# repo hygiene
# --------------------------------------------------------------------------- #
def test_no_upstream_url_is_hardcoded_outside_config_and_examples() -> None:
    """The deployed ranch has moved hosts before. A URL in a source file is a URL you
    find with grep at the worst possible moment."""
    root = Path(__file__).resolve().parents[1]
    offenders = [
        p.relative_to(root).as_posix()
        for p in [*root.glob("src/**/*.py"), root / "main.py"]
        if "execute-api" in p.read_text(encoding="utf-8") or "lambda-url" in p.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"hardcoded upstream URL in {offenders}; read it from config instead"
