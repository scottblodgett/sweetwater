"""One-off, run by hand at M7 before the first live local tick. Not part of the tree.

    CHAOS_ENABLED=0 .venv/Scripts/python.exe logs/m7_measure.py

Runs the free pass against the live ranch, takes the first warning-severity finding a responder
owns, assembles the real packet (SOP included), and asks the Tier-1 model for a work order.
Reports what Scott asked for before a local tick runs: how many tokens the page actually is
against `num_ctx`, how long one call takes, whether Ollama serves two calls at once, and what
the rails said about the answer. Writes nothing to any ledger.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from src.agent.agent import RESPONDERS, owner_for
from src.agent.state import Incident
from src.agent.workers import MAX_OUTPUT_TOKENS, to_work_order
from src.models.llm_client import call_tier1
from src.prompts.system_prompts import WORK_ORDER_SCHEMA, system_prompt
from src.tools.evidence import assemble
from src.tools.sensors import fetch_catalog, sweep
from src.tools.triage import triage_sweep
from src.utils.config import get_settings
from src.utils.helpers import utc_now_iso


async def run() -> dict[str, object]:
    settings = get_settings()
    ranch_map, source = await fetch_catalog()
    swept = await sweep(ranch_map.sensors)
    findings = [f for f in triage_sweep(swept.readings) if owner_for(f.category) in RESPONDERS]
    warnings = [f for f in findings if f.severity == "warning"] or findings
    print(f"catalog via {source}: {len(ranch_map.sensors)} sensors, {len(swept.readings)} read, {len(findings)} routed findings, {len(warnings)} at warning")
    want = os.environ.get("SENSOR", "")
    picked = [f for f in warnings if f.sensor_id == want] if want else []
    finding = (picked or sorted(warnings, key=lambda f: (owner_for(f.category) != "water_feed", f.sensor_id)))[0]
    agent = owner_for(finding.category)
    now = utc_now_iso()
    incident = Incident(
        key=f"{finding.sensor_id}:{finding.category}",
        sensor_id=finding.sensor_id,
        sensor_type=finding.sensor_type,
        location=finding.location,
        category=finding.category,
        severity=finding.severity,
        status="opened",
        summary=finding.summary,
        last_value=f"{finding.value:g}{finding.unit}" if isinstance(finding.value, (int, float)) else str(finding.value),
        unit=finding.unit,
        threshold=finding.threshold,
        first_seen_at=now,
        last_seen_at=now,
        owner=agent,
    )
    (packet,) = await assemble([incident], readings=swept.readings, ranch_map=ranch_map)
    page, system = packet.render(), system_prompt(agent)
    print(f"packet: {incident.key} [{incident.severity}] -> {agent}, sop={packet.sop_name}, siblings={len(packet.siblings)}, head={packet.pasture.head_count if packet.pasture else None}")
    print(f"chars: system {len(system)}, page {len(page)} (SOP {len(packet.sop_text)}), schema {len(json.dumps(WORK_ORDER_SCHEMA))}; rough estimate at 4 chars/token: {(len(system) + len(page)) // 4} in, num_ctx {settings.ollama_num_ctx}")

    async def one(label: str) -> dict[str, object]:
        response = await call_tier1(agent=agent, system=system, user=page, schema=WORK_ORDER_SCHEMA, reasoning_effort="none", max_tokens=MAX_OUTPUT_TOKENS, incident_key=incident.key)
        order = to_work_order(packet=packet, agent=agent, response=response)
        print(f"  {label}: {response.finish_reason} in {response.latency_ms / 1000:.1f}s, {response.input_tokens} in / {response.output_tokens} out, status={order.status}, violations={list(order.violations)}, rules={list(order.rules_cited)}, ii={order.insufficient_information}")
        return {"finish_reason": response.finish_reason, "latency_ms": response.latency_ms, "input_tokens": response.input_tokens, "output_tokens": response.output_tokens, "status": order.status, "violations": list(order.violations), "payload": response.payload, "error": response.error}

    n = int(os.environ.get("N", "5"))
    print(f"{n} sequential calls, first includes the model load:")
    runs = [await one(f"call {i + 1}") for i in range(n)]
    summary = {
        "calls": n,
        "warm_latency_s": [round(int(r["latency_ms"]) / 1000, 1) for r in runs[1:]],
        "input_tokens": sorted({int(r["input_tokens"]) for r in runs}),
        "shipped": sum(1 for r in runs if r["status"] == "ok"),
        "rejected": sum(1 for r in runs if r["status"] == "rejected"),
        "insufficient_information": sum(1 for r in runs if (r["payload"] or {}).get("insufficient_information")),
        "rule_citation_trimmed": sum(1 for r in runs if "rule_citation_trimmed" in r["violations"]),
        "invented_rule": sum(1 for r in runs if "invented_rule" in r["violations"]),
        "write_shape_invalid": sum(1 for r in runs if "write_shape_invalid" in r["violations"]),
        "sensor_named": sum(1 for r in runs if "sensor_not_named" not in r["violations"]),
        "proposed_a_write": sum(1 for r in runs if ((r["payload"] or {}).get("proposed_write") or {}).get("tool")),
    }
    print("summary:", json.dumps(summary))
    pair: list[dict[str, object]] = []
    wall = 0.0
    if os.environ.get("PAIR"):
        print("two concurrent calls (does Ollama parallelize on this box?):")
        started = time.perf_counter()
        pair = list(await asyncio.gather(one("par a"), one("par b")))
        wall = time.perf_counter() - started
        print(f"  pair wall clock {wall:.1f}s vs sum of their own latencies {sum(int(r['latency_ms']) for r in pair) / 1000:.1f}s")
    print("--- the Tier-1 work order, last call ---")
    print(json.dumps(runs[-1]["payload"], indent=2))
    return {"incident": incident.model_dump(mode="json"), "agent": agent, "sop": packet.sop_name, "page": page, "system": system, "summary": summary, "sequential": runs, "concurrent": pair, "pair_wall_s": wall}


if __name__ == "__main__":
    result = asyncio.run(run())
    out = Path(os.environ.get("OUT", "logs/m7_measure.json"))
    out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"written: {out}")
