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
from src.tools.allowlists import WRITE_TOOL_ARGS, WriteArg, proposable_tools_for
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
        # M6. A proposal, never an action: it pauses for a human, who may say no. Described
        # neutrally on purpose. "The usual answer is none" is the truth about this ranch, and a
        # description that leaned the other way would manufacture proposals for the demo and
        # bill a human's attention for each one. The tools an agent may name, with their
        # arguments, are rendered into its brief from `allowlists.WRITE_TOOL_ARGS`.
        "proposed_write": {
            "type": "object",
            "properties": {
                "tool": {
                    "type": "string",
                    "description": "Empty string when no change to the ranch's own records is warranted, which is the usual answer. Otherwise the exact name of one write tool listed in your brief. Anything you name here is a proposal a person approves or rejects before it runs; it is not something you have done.",
                },
                "args": {
                    "type": "object",
                    "description": "The tool's arguments, exactly as your brief names them, and empty when tool is empty. Every id and every quantity must appear on the page; a value that is not on the page is rejected in code.",
                    "additionalProperties": True,
                },
            },
            "required": ["tool", "args"],
            "additionalProperties": False,
        },
    },
    "required": ["severity_echo", "headline", "assessment", "actions", "rules_cited", "escalate", "escalate_reason", "unknowns", "proposed_write"],
    "additionalProperties": False,
}

WORK_ORDER_TOOL = "write_work_order"
WORK_ORDER_TOOL_DESCRIPTION = "Record the work order for this one incident. The only way to answer; do not reply in prose."

#: The supervisor's schema, arriving at M3. Forced tool call for the same reason: `linked` has
#: to be a list of keys code can check against the keys it handed over, and a model asked for
#: prose writes "the battery and the tank at Alkali Flat are one problem" in a sentence that
#: no rail can verify.
#:
#: `situation` is the only field whose description says what NOT to write. It is the field the
#: whole page lives or dies on and the one a model most wants to turn into a list.
SHIFT_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "headline": {
            "type": "string",
            "description": "The one line the person coming on shift reads first. What is happening to this ranch right now, not how many work orders there are. Under 120 characters, no trailing period.",
        },
        "situation": {
            "type": "string",
            "description": "Two to five sentences fusing the work orders into one picture: what is going on, what is causing what, and where two problems are actually one. Do NOT summarize each agent in turn. If this reads as one sentence per world, it is wrong.",
        },
        "priorities": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 6,
            "description": "What the shift does, in the order it does it, across every world. First item is what happens now. Say why the first one is first. At most six: a ranked list nobody can hold in their head is an unranked list.",
        },
        "linked": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Incident keys you are claiming belong to one event, spelled exactly as printed on the page. Checked in code against the keys you were given. Empty when nothing genuinely connects, which is an honest answer and a common one.",
        },
        "escalations": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 4,
            "description": "One line each for what a human above the crew needs to know. A work order that already asked to escalate belongs here, said once, in the supervisor's words rather than copied.",
        },
    },
    "required": ["headline", "situation", "priorities", "linked", "escalations"],
    "additionalProperties": False,
}

SHIFT_REPORT_TOOL = "write_shift_report"
SHIFT_REPORT_TOOL_DESCRIPTION = "Record the one shift report for this tick. The only way to answer; do not reply in prose."


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
    return f"{INHERITED_RULES}\n\n{patch}\n\n{write_proposal_brief(agent)}".strip()


def write_proposal_brief(agent: str) -> str:
    """The one paragraph about `proposed_write`, rendered from the allowlist rather than written.

    Empty while `GATE_LANDED` is False, and empty for an agent with no write in its slice, so a
    brief never mentions a tool a proposal check would reject. Rendered from
    `WRITE_TOOL_ARGS` rather than hand-written so the names the model is told and the names
    `workers.check` validates cannot drift apart. Deliberately neutral: it says what the field
    is for and that none is the usual answer, and it does not suggest that proposing one is
    good work. Steering a model toward a write so the demo pauses is how a gate becomes a
    formality.
    """
    tools = sorted(proposable_tools_for(agent))
    if not tools:
        return ""
    lines = [
        "THE `proposed_write` FIELD. Almost always leave `tool` as an empty string with empty `args`. Use it only when a change to the ranch's own records is the correct next step and the standing orders support it, and know that it is a proposal: a person reads it and approves or rejects it before anything runs, and the crew's actions in your list do not depend on it. If you do name one, every id and every quantity must be on this page exactly, and an argument that is not on the page is rejected in code. The tools you may name, and nothing else:",
    ]
    for tool in tools:
        args = ", ".join(_describe_arg(a) for a in WRITE_TOOL_ARGS.get(tool, ()))
        lines.append(f"- {tool}({args})")
    return "\n".join(lines)


def _describe_arg(arg: WriteArg) -> str:
    kind = {"id": "an id printed on this page", "number": "a number printed on this page", "enum": "one of " + "/".join(arg.choices), "timestamp": "ISO 8601 timestamp", "text": "free text"}[arg.kind]
    return f"{arg.name}: {kind}{'' if arg.required else ', optional'}"
