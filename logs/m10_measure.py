"""One-off, run by hand at M10. Not part of the tree, like `m7_measure.py` beside it.

Two measurements, one script, chosen by the first argument:

    CHAOS_ENABLED=0 .venv/Scripts/python.exe logs/m10_measure.py feed      # Piece 1, #12: the feed page before and after the conditions block, on the local model, $0
    .venv/Scripts/python.exe logs/m10_measure.py shares logs/m9-close logs/m7-compare logs   # Piece 3: calls and dollars by tier and by job, per log directory

`feed` runs the free pass against the live ranch (HTTP only, no ledger), builds the real packet for
the lowest-reading feed bin (or the one named in `SENSOR=`), renders the page twice, once with the
M10 conditions block stripped and once as `evidence.assemble` now builds it, and asks the Tier-1
model for a work order `N` times on each. The number that matters is how often the local judge
sets `insufficient_information`, because at M7 that flag is what sent the feed order to Opus. The
incident is constructed here at warning severity whatever the bin reads today, because FEED-02 is
the rule that asks about the weather; the model is told the severity is not its to change, as
always. Nothing is written to any ledger and no Opus call is made.

`shares` reads `agent.jsonl` (and `compare.jsonl` when present) under each directory and prints,
per directory, how many model calls each tier made and what they cost at `routing.PRICE_TABLE`,
split by job (`work_order` / `shift_report` / `investigate`), so the before and after in
`docs/model-routing.md` come from the same arithmetic.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

from src.agent.state import Incident
from src.agent.workers import MAX_OUTPUT_TOKENS, to_work_order
from src.models.llm_client import call_tier1
from src.models.routing import TIER1, cost_usd
from src.prompts.system_prompts import WORK_ORDER_SCHEMA, system_prompt
from src.tools.evidence import EvidencePacket, assemble
from src.tools.sensors import fetch_catalog, sweep
from src.tools.triage import RULES
from src.utils.config import get_settings
from src.utils.helpers import utc_now_iso

AGENT = "water_feed"


async def one(label: str, *, packet: EvidencePacket, page: str, system: str) -> dict[str, object]:
    response = await call_tier1(agent=AGENT, system=system, user=page, schema=WORK_ORDER_SCHEMA, reasoning_effort="none", max_tokens=MAX_OUTPUT_TOKENS, incident_key=packet.incident.key)
    order = to_work_order(packet=packet, agent=AGENT, response=response)
    print(f"  {label}: {response.finish_reason} in {response.latency_ms / 1000:.1f}s, {response.input_tokens} in / {response.output_tokens} out, status={order.status}, ii={order.insufficient_information}, unknowns={list(order.unknowns)}, rules={list(order.rules_cited)}")
    return {"finish_reason": response.finish_reason, "latency_ms": response.latency_ms, "input_tokens": response.input_tokens, "output_tokens": response.output_tokens, "status": order.status, "violations": list(order.violations), "insufficient_information": order.insufficient_information, "unknowns": list(order.unknowns), "rules_cited": list(order.rules_cited), "actions": list(order.actions), "assessment": order.assessment, "error": response.error}


async def feed() -> dict[str, object]:
    settings = get_settings()
    ranch_map, source = await fetch_catalog()
    swept = await sweep(ranch_map.sensors)
    bins = sorted((r for r in swept.readings if r.sensor_type == "feed-bin-weight" and isinstance(r.value, (int, float))), key=lambda r: float(r.value or 0))
    want = os.environ.get("SENSOR", "")
    picked = next((r for r in bins if r.sensor_id == want), None) if want else None
    bin_ = picked or bins[0]
    rule = RULES["feed-bin-weight"]
    warning_line = next(b.warning for b in rule.bands if b.category == "feed_low")
    now = utc_now_iso()
    incident = Incident(
        key=f"{bin_.sensor_id}:feed_low",
        subject_id=bin_.sensor_id,
        subject_type="feed-bin-weight",
        location=bin_.location,
        category="feed_low",
        severity="warning",
        status="opened",
        summary=f"feed-bin weight at {bin_.location} is {bin_.value:g}{rule.unit}, below the warning line of {warning_line:g}{rule.unit}",
        last_value=f"{bin_.value:g}{rule.unit}",
        unit=rule.unit,
        threshold=warning_line,
        first_seen_at=now,
        last_seen_at=now,
        owner=AGENT,
    )
    print(f"catalog via {source}: {len(ranch_map.sensors)} sensors, {len(swept.readings)} read; {len(bins)} feed bins, lowest {bin_.sensor_id} at {bin_.value:g} lbs in {bin_.location}")
    (after,) = await assemble([incident], readings=swept.readings, ranch_map=ranch_map)
    before = replace(after, conditions=(), conditions_missing=())
    system = system_prompt(AGENT)
    pages = {"before": before.render(), "after": after.render()}
    print(f"conditions on the after page: {[c.render().strip() for c in after.conditions]}, missing {list(after.conditions_missing)}")
    print(f"chars: before {len(pages['before'])}, after {len(pages['after'])} (+{len(pages['after']) - len(pages['before'])}); num_ctx {settings.ollama_num_ctx}")

    n = int(os.environ.get("N", "3"))
    runs: dict[str, list[dict[str, object]]] = {}
    for label in ("before", "after"):
        print(f"{label}: {n} sequential Tier-1 calls")
        runs[label] = [await one(f"{label} {i + 1}", packet=after if label == "after" else before, page=pages[label], system=system) for i in range(n)]
    summary = {
        label: {
            "calls": n,
            "insufficient_information": sum(1 for r in rs if r["insufficient_information"]),
            "shipped": sum(1 for r in rs if r["status"] == "ok"),
            "rejected": sum(1 for r in rs if r["status"] == "rejected"),
            "no_answer": sum(1 for r in rs if r["status"] == "no_answer"),
            "input_tokens": sorted({int(r["input_tokens"]) for r in rs}),
            "latency_s": [round(int(r["latency_ms"]) / 1000, 1) for r in rs],
            "unknowns_named": sorted({u for r in rs for u in r["unknowns"]}),  # type: ignore[union-attr]
            "weather_or_fuel_in_prose": sum(1 for r in rs if any(w in str(r["assessment"]).lower() + " ".join(map(str, r["actions"])).lower() for w in ("wind", "temperature", "snow", "cold", "fuel", "diesel", " f,", " f "))),  # type: ignore[arg-type]
        }
        for label, rs in runs.items()
    }
    print("summary:", json.dumps(summary, indent=2))
    return {"incident": incident.model_dump(mode="json"), "source": source, "pages": pages, "system": system, "summary": summary, "runs": runs}


def shares(dirs: list[str]) -> dict[str, object]:
    out: dict[str, object] = {}
    for d in dirs:
        path = Path(d) / "agent.jsonl"
        if not path.exists():
            print(f"{d}: no agent.jsonl")
            continue
        calls: Counter[tuple[int, str]] = Counter()
        dollars: defaultdict[tuple[int, str], float] = defaultdict(float)
        tokens_in: defaultdict[tuple[int, str], int] = defaultdict(int)
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (row.get("msg") or row.get("event")) != "agent_call":  # structlog writes the event under `msg` in this repo's JSON renderer
                continue
            tier = int(row.get("tier") or 2)
            job = str(row.get("job") or ("shift_report" if str(row.get("agent")) == "supervisor" else "work_order"))
            key = (tier, job)
            calls[key] += 1
            tokens_in[key] += int(row.get("input_tokens") or 0)
            dollars[key] += cost_usd(int(row.get("input_tokens") or 0), int(row.get("output_tokens") or 0), model=str(row.get("model") or ""), tier=tier)
        total_calls, total_usd = sum(calls.values()), sum(dollars.values())
        rows = {f"tier{t}:{j}": {"calls": calls[(t, j)], "input_tokens": tokens_in[(t, j)], "usd": round(dollars[(t, j)], 4)} for (t, j) in sorted(calls)}
        local_calls = sum(c for (t, _), c in calls.items() if t == TIER1)
        summary = {"calls": total_calls, "usd": round(total_usd, 4), "local_share_of_calls": round(local_calls / total_calls, 3) if total_calls else None, "local_share_of_dollars": 0.0 if total_usd else None, "by_job": rows}
        print(f"{d}: {json.dumps(summary)}")
        out[d] = summary
    return out


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "feed"
    if mode == "feed":
        result = asyncio.run(feed())
        out = Path(os.environ.get("OUT", "logs/m10_measure_feed.json"))
        out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        print(f"written: {out}")
    elif mode == "shares":
        shares(sys.argv[2:] or ["logs"])
    else:
        raise SystemExit(f"unknown mode {mode!r}: feed | shares")
