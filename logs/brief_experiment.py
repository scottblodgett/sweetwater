"""One-off, run by hand at M3 to produce the two transcripts in docs/. Not part of the tree.

    .venv/Scripts/python.exe logs/brief_experiment.py

Runs the free pass against the live ranch, takes the first `compliance`-owned finding, and
asks the same model the same question twice: once with the patch removed from the brief
(`mandate=" "`), once with it. Same packet, same schema, same rails, one variable.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from src.agent.agent import owner_for
from src.agent.state import Incident
from src.models.llm_client import call_tier2
from src.prompts.system_prompts import WORK_ORDER_SCHEMA, WORK_ORDER_TOOL, WORK_ORDER_TOOL_DESCRIPTION, system_prompt
from src.tools.evidence import assemble
from src.tools.sensors import fetch_catalog, sweep
from src.tools.triage import triage_sweep
from src.utils.helpers import utc_now_iso

AGENT = "compliance"


async def run() -> dict[str, object]:
    ranch_map, source = await fetch_catalog()
    swept = await sweep(ranch_map.sensors)
    findings = [f for f in triage_sweep(swept.readings) if owner_for(f.category) == AGENT]
    print(f"catalog via {source}: {len(ranch_map.sensors)} sensors, {len(swept.readings)} read, {len(findings)} {AGENT} findings")
    if not findings:
        raise SystemExit(f"no {AGENT} finding on the ranch right now; nothing to run the experiment against")

    finding = findings[0]
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
        last_value=f"{finding.value:g}{finding.unit}",
        unit=finding.unit,
        threshold=finding.threshold,
        first_seen_at=now,
        last_seen_at=now,
        owner=AGENT,
    )
    (packet,) = await assemble([incident], readings=swept.readings, ranch_map=ranch_map)
    page = packet.render()
    print(f"packet: {incident.key}, sop={packet.sop_name}, {len(page)} chars")

    out: dict[str, object] = {"incident": incident.model_dump(mode="json"), "sop_name": packet.sop_name, "page": page, "runs": {}}
    for label, brief in (("no_brief", system_prompt(AGENT, mandate=" ")), ("with_brief", system_prompt(AGENT))):
        response = await call_tier2(
            agent=AGENT,
            system=brief,
            user=page,
            schema=WORK_ORDER_SCHEMA,
            schema_name=WORK_ORDER_TOOL,
            schema_description=WORK_ORDER_TOOL_DESCRIPTION,
            reasoning_effort="none",
            max_tokens=2048,
            incident_key=incident.key,
        )
        out["runs"][label] = {  # type: ignore[index]
            "system_chars": len(brief),
            "payload": response.payload,
            "finish_reason": response.finish_reason,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "latency_ms": response.latency_ms,
            "provider": response.provider,
            "model": response.model,
            "error": response.error,
        }
        print(f"{label}: {response.finish_reason}, {response.input_tokens} in / {response.output_tokens} out, {response.latency_ms} ms")

    return out


if __name__ == "__main__":
    Path("logs/brief_experiment.json").write_text(json.dumps(asyncio.run(run()), indent=2), encoding="utf-8")
    print("wrote logs/brief_experiment.json")
