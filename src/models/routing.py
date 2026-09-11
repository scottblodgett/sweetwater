"""Job to tier to model, the price table, and the escalation predicate. Lands at M7.

Not to be confused with the routing table in `src/agent/agent.py`, which answers a
different question: that one decides which of five agents owns an incident, this one
decides which model runs a job and what it cost.

## Two model jobs, two tiers

There are exactly two places in this repo where a model is called: the per-incident work
order (`workers.judge_packet`, one call per newly-opened incident, the bill) and the fused
shift report (`agent.synthesize`, one call per tick when two or more worlds opened
incidents). The plan listed four Tier-1 jobs; two of them never existed (chaos is pure code,
shift-report *assembly* is the free code path) and the other two were one call. The ledger
in `docs/model-routing.md` was corrected before anything moved.

Tier 1 is local (`TIER1_MODEL` on Ollama, default `gemma4:e4b`) and writes the work order
for every incident the predicate does not claim. Tier 2 is Opus and owns the shift report,
the critical incidents, and every rewrite.

## The predicate is per incident, and three of its four conditions fire after the call

  * **critical**                  known before the call. Tier 1 is never asked.
  * **rejected**                  the Tier-1 answer failed a blocking rail (`all_clear` is the
                                  one this whole design exists for: code flagged the incident,
                                  so "nothing to do" from the cheap judge is a contradiction)
  * **insufficient_information**  the Tier-1 judge said so on purpose. A model allowed to say
                                  "I do not know" says it instead of inventing
  * **proposed_write**            only a Tier-2 proposal may reach the gate
  * **no_answer**                 Tier 1 never answered (transport, truncation). The packet is
                                  unjudged and Opus is standing right there

Escalation is a **rewrite**, not a review: Opus gets the identical page and writes its own
order. The Tier-1 answer stays in `agent.jsonl` as the receipt; the Tier-2 order is stored.
Nothing is retried at the same tier: a Tier-2 rejection is stored as rejected.

**The world count is not a per-incident trigger.** "Two or more sensing worlds opened
incidents" says nothing about what one packet contains; applying it per incident would send
every work order on every storm tick to Opus, which is the bill M7 exists to cut. What it
decides is whether the tick needs someone reading across the ranch, and that is
`FUSION_THRESHOLD` on the shift report, already Tier 2.

## The price table replaces a constant, not a field

`cost_usd` on the tick line keeps its name and meaning. M4 priced every call at one assumed
rate, $15/$75 per million, which turned out to be 3x the Opus 5 list price; the tokens in the
ledger were always the measurement and the dollars were arithmetic, corrected here in one
place. Tier 1 is $0.00 by tier, not by model name, so a renamed local model cannot bill.
"""

from __future__ import annotations

from src.agent.state import Incident, WorkOrder
from src.utils.config import get_settings
from src.utils.logger import get_logger

log = get_logger(__name__)

TIER1 = 1
TIER2 = 2

#: Dollars per million tokens, `(input, output)`, keyed by the model id **as the provider
#: spells it**, because that is the string on every `agent.jsonl` line and on every stored
#: work order. First-party Anthropic list price for Claude Opus 5, read from the API
#: reference on 2026-09-11. The Bedrock row is the same number: Bedrock's public pricing page
#: did not render an Opus 5 row when checked, so parity with first-party is an assumption
#: marked here rather than a fact, and it is the one entry in this table to re-verify.
PRICE_TABLE: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "us.anthropic.claude-opus-5": (5.00, 25.00),  # Bedrock, us-east-1 cross-region profile. UNVERIFIED: assumed equal to first-party
}

#: What an unknown **paid** model is billed at. The most expensive row, on purpose: a model
#: missing from the table is a bookkeeping gap, and the honest failure is to over-count
#: toward the spend ceiling rather than under-count and let the loop run past it.
FALLBACK_RATE: tuple[float, float] = (5.00, 25.00)

_unknown_models_warned: set[str] = set()


def rate_for(model: str, *, tier: int = TIER2) -> tuple[float, float]:
    """`(usd_per_M_in, usd_per_M_out)`. Tier 1 is free by tier; an unknown Tier-2 model bills at the fallback and warns once."""
    if tier == TIER1:
        return (0.0, 0.0)
    rate = PRICE_TABLE.get(model)
    if rate is None:
        if model and model not in _unknown_models_warned:
            _unknown_models_warned.add(model)
            log.warning("price_unknown", model=model, using=FALLBACK_RATE, hint="add the model to routing.PRICE_TABLE; billing at the most expensive known rate until then")
        return FALLBACK_RATE
    return rate


def cost_usd(input_tokens: int, output_tokens: int, *, model: str = "", tier: int = TIER2) -> float:
    """Dollars for one call at the model's rate. Exact zero for zero tokens and for Tier 1."""
    rate_in, rate_out = rate_for(model, tier=tier)
    return round((input_tokens * rate_in + output_tokens * rate_out) / 1_000_000, 6)


# --------------------------------------------------------------------------- #
# the predicate
# --------------------------------------------------------------------------- #
#: The reason codes, as they appear in `WorkOrder.escalation` and summed on the tick line as
#: `escalation_reasons`. A closed vocabulary so a `jq` over the log can count them.
ESCALATE_CRITICAL = "critical"
ESCALATE_REJECTED = "rejected"
ESCALATE_INSUFFICIENT = "insufficient_information"
ESCALATE_PROPOSED_WRITE = "proposed_write"
ESCALATE_NO_ANSWER = "no_answer"
ESCALATION_REASONS = frozenset({ESCALATE_CRITICAL, ESCALATE_REJECTED, ESCALATE_INSUFFICIENT, ESCALATE_PROPOSED_WRITE, ESCALATE_NO_ANSWER})


def tier1_enabled() -> bool:
    """Whether the cascade is on at all. `TIER1_ENABLED` in `config.py`; off is Tier 2 for everything, which is the M2 to M6 shape."""
    return bool(get_settings().tier1_enabled)


def tier_for(incident: Incident) -> int:
    """The tier that is asked first. The only pre-call condition is severity."""
    if not tier1_enabled():
        return TIER2
    return TIER2 if incident.severity == "critical" else TIER1


def escalation_reason(order: WorkOrder) -> str:
    """Why a Tier-1 order must be rewritten by Tier 2, or `""` when it stands.

    Reads the order, not the raw payload, so every condition is one code already computed:
    the rails in `workers.check`, the `insufficient_information` field, the cleaned proposal.
    Order matters only for which reason is recorded when several hold; `no_answer` first
    because there is nothing else to read on such an order.
    """
    if order.status == "no_answer":
        return ESCALATE_NO_ANSWER
    if order.status == "rejected":
        return ESCALATE_REJECTED
    if order.insufficient_information:
        return ESCALATE_INSUFFICIENT
    if order.proposed_write:
        return ESCALATE_PROPOSED_WRITE
    return ""


__all__ = [
    "ESCALATE_CRITICAL",
    "ESCALATE_INSUFFICIENT",
    "ESCALATE_NO_ANSWER",
    "ESCALATE_PROPOSED_WRITE",
    "ESCALATE_REJECTED",
    "ESCALATION_REASONS",
    "FALLBACK_RATE",
    "PRICE_TABLE",
    "TIER1",
    "TIER2",
    "cost_usd",
    "escalation_reason",
    "rate_for",
    "tier1_enabled",
    "tier_for",
]
