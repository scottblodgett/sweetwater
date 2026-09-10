"""The rules every sub-agent brief inherits, plus the shape they answer in. Arrives at M2.

Three of them are already decided and are not up for negotiation by a prompt:

  * **Severity is not yours.** `src/tools/triage.py` already ranked it in code. A model
    handed a verdict and asked to justify it fabricates the justification.
  * **Cite your SOP.** A work order that names no rule is an opinion.
  * **No invented premises.** Name the real sensor and quote the real reading, or say you
    cannot. A confident all-clear is the single worst output a ranch monitor can produce.

Written against a packet that was printed first, per the build order, and the printed page
is why several lines below exist at all. Specifically:

  * The packet's history series is **generated independently of the triaged reading** and
    disagrees with it (3.4 gal on the incident, 0.8 gal at the top of the series, later
    timestamp). The packet labels the seam; the brief has to say which number goes in the
    work order, or a model reasonably quotes the newer-looking one.
  * The packet's most useful fact at Alkali Flat was a **sibling**: the second tank on the
    same pasture reading 29.7 gal, which is what separates a float failure from a supply
    failure. Nothing in the SOP tells a model to look there, so the brief does.
  * The packet states its absences as sentences (`no pasture is mapped to...`). A brief that
    does not name that convention gets a model treating a stated absence as a fact it may
    reason from.

The output is a **forced tool call**, not free prose. That is not a formatting preference:
it is what lets the severity echo be checked mechanically in `src/agent/executor.py` rather
than trusted. The schema and the words describing it live in one file so they cannot drift.
"""

from __future__ import annotations

from typing import Any

from src.prompts.agent_prompts import MANDATES
from src.utils.logger import get_logger

log = get_logger(__name__)

#: What every brief inherits. Written as the reasons behind the rules and not just the
#: rules: a prompt that says "do not invent numbers" without saying why gets obeyed on the
#: examples it lists, and a prompt that explains the failure gets obeyed on the ones it
#: does not.
INHERITED_RULES = """You are a working sub-agent on Sweetwater Land & Cattle Co., a fourth-generation Wyoming cattle ranch of roughly 34,000 acres and roughly 1,000 mother cows, run by a small year-round crew with long driving distances between places. Your output is read by a person who is about to get in a truck.

You are handed one evidence packet, already assembled. There is nothing to go look up and no tools to call. Everything you may rely on is on the page in front of you. Judge it and write the work order.

Four rules, all of which outrank anything else in your brief.

1. SEVERITY IS NOT YOURS. The packet's severity was decided in code by threshold rules before you were called. Echo it back exactly as given. Do not re-rank it, do not soften it, do not argue with it, and do not decide the sensor is probably fine. If the evidence genuinely contradicts the ranking, say so in one sentence inside your assessment and still echo the severity you were given.

2. NEVER WRITE AN ALL-CLEAR. Code already found something wrong here. "Nothing appears to be wrong" is not a finding, it is a contradiction, and on this ranch it is the specific output that gets animals killed. If the packet is thin, write what is known, name what is unknown, and send someone to look.

3. QUOTE ONLY WHAT IS ON THE PAGE. Every number in your output must appear in the packet, and you must name the sensor it came from. Do not estimate gallons, fill rates, days of feed remaining, drive times, or head counts that are not printed. If you need a number the packet does not have, put the need in `unknowns` instead of filling it in. An invented number reads as authority and carries none, and the crew cannot tell yours from a measured one.

   Two specific traps on this page:
   - The reading under "the incident" is the authoritative current value. The recent-readings series is generated separately and does NOT include it, so use that list only for the operating range and the trend. Never quote the top of the series as "current."
   - The packet states its absences in words ("no pasture is mapped to...", "none available: ..."). A stated absence is a gap, not a fact. Do not reason from it as though it were zero.

4. CITE THE RULE YOU ACTED ON. Every action traces to a rule id from the standing orders at the bottom of the packet, quoted exactly as written there (for example `WATER-01`). If no rule covers what you are recommending, say that plainly instead of citing a rule id that does not exist. An invented rule id is worse than no citation, because the next person goes looking for it.

How to actually read the packet, in this order:
- The incident, and the reading against its line. That is the fact.
- The other sensors at the same location, read in the same sweep. This is where the diagnosis usually is: a second tank on the same ground reading full points at that tank rather than at the supply, and a hot air temperature next to a falling one changes what the falling one means.
- What is standing behind it. The head count is why one job goes before another, so put it in the work order when it is there.
- The standing orders. They decide what the crew does. The packet decides what is true.

Write the way the crew talks: short, concrete, no hedging, no restating the packet back. Name the place, name the sensor, say what to do first. No em dashes."""

#: Forced-tool schema. Every field is required, including the ones a lazy answer would
#: rather leave out: `unknowns` is where a number the packet does not have is supposed to go
#: instead of into `assessment`, and `rules_cited` empty is a legitimate answer that has to
#: be possible to say without lying.
WORK_ORDER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "severity_echo": {
            "type": "string",
            "enum": ["nominal", "warning", "critical"],
            "description": "The severity from the packet, copied exactly. Not your judgment. Checked in code against the incident and a mismatch is rejected.",
        },
        "headline": {
            "type": "string",
            "description": "One line the crew reads first. Name the location and what to do. Under 120 characters, no trailing period.",
        },
        "assessment": {
            "type": "string",
            "description": "Two to five sentences on what is true and what it most likely means. Name the sensor behind every number. Use the sibling readings at this location; that is usually where the diagnosis is. Do not restate the packet.",
        },
        "actions": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 5,
            "description": "What the crew does, in the order they do it. First item is what happens now. Each one concrete enough to hand to a person with a truck. At most five, and fewer is better: a seven-step list gets skimmed and the important step is the one that gets skipped.",
        },
        "rules_cited": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Rule ids from the standing orders in this packet, exactly as spelled there. Empty only when genuinely no rule covers this, and then say so in the assessment.",
        },
        "escalate": {
            "type": "boolean",
            "description": "True when the standing orders say a human above the crew needs to know, or when the packet is too thin to act on safely.",
        },
        "escalate_reason": {
            "type": "string",
            "description": "Why, in one sentence, or an empty string when escalate is false.",
        },
        "unknowns": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 4,
            "description": "Facts you needed and the packet does not contain. This is the place for anything you would otherwise have estimated. Only the ones that would change what the crew does; a list of everything a sensor cannot see is not useful. Empty is fine when the page was genuinely enough.",
        },
    },
    "required": ["severity_echo", "headline", "assessment", "actions", "rules_cited", "escalate", "escalate_reason", "unknowns"],
    "additionalProperties": False,
}

WORK_ORDER_TOOL = "write_work_order"
WORK_ORDER_TOOL_DESCRIPTION = "Record the work order for this one incident. The only way to answer; do not reply in prose."


def system_prompt(agent: str, *, mandate: str = "") -> str:
    """The full brief for one sub-agent: inherited rules, then its patch.

    A sub-agent inherits nothing at runtime (`src/agent/CLAUDE.md`), so the shared rules are
    concatenated into every brief rather than assumed to be in the room. Assembled in code
    instead of stored as five hand-maintained files, because the day rule 2 changes it has
    to change in one place or four agents keep the old one.

    A missing mandate is loud. The inherited rules alone read like a complete brief and
    ground nothing: the model would know it must cite a rule and not what patch it works.
    M3 briefed the four responders; `chaos` is the one agent that legitimately still trips
    this, and the log line is how M5 finds out it needs a brief.

    The `mandate=` override exists for exactly one caller and it is not production: the
    no-brief experiment in `docs/` passes `mandate=" "` to run a responder with its patch
    removed and nothing else changed. A whitespace mandate is falsy after `strip()` in the
    return but truthy here, so the flailing transcript is captured without the log line
    claiming a brief is missing from the repo.
    """
    patch = mandate or MANDATES.get(agent, "")
    if not patch:
        log.error("no_mandate_for_agent", agent=agent, known=sorted(MANDATES), hint="add the brief in src/prompts/agent_prompts.py; the inherited rules alone are not a brief")
    return f"{INHERITED_RULES}\n\n{patch}".strip()
