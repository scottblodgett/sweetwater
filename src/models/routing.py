"""Job to tier to model, the price table, and the escalation predicate. Lands at M7, inverted at M10.

Not to be confused with the routing table in `src/agent/agent.py`, which answers a
different question: that one decides which of five agents owns an incident, this one
decides which model runs a job and what it cost.

## Three model jobs, two tiers

There are exactly three places in this repo where a model is called: the per-incident work
order (`workers.judge_packet`, one call per newly-opened incident, the bill), the fused shift
report (`agent.synthesize`, one call per tick when two or more worlds opened incidents), and
from M10 the investigator (`investigator.investigate`, a bounded tool loop that fires only when
the Tier-1 judge said `insufficient_information`, local only, never billed). The plan listed four
Tier-1 jobs; two of them never existed (chaos is pure code, shift-report *assembly* is the free
code path) and the M7 ledger was corrected before anything moved.

Tier 1 is local (`TIER1_MODEL` on Ollama, default `gemma4:e4b`). **From M10 it is asked first
for every job**, the work order and the shift report alike, whatever the severity. Tier 2 is Opus
and is reached one way only: by a reason read off the Tier-1 answer after the call, plus the one
pre-call reason code can know, that the page does not fit the local context window.

## The predicate is per call, and every condition but one fires after the call

  * **page_too_long**            known before the call, and code's. `fits_tier1` estimates the
                                 prompt against `num_ctx` less the answer budget; a page that
                                 does not fit goes to Opus rather than to a model that would
                                 read it with its front cut off (`tier1_context_full`)
  * **rejected**                 the Tier-1 answer failed a blocking rail (`all_clear` is the
                                 one this whole design exists for: code flagged the incident,
                                 so "nothing to do" from the cheap judge is a contradiction)
  * **insufficient_information** the Tier-1 judge said so on purpose. A model allowed to say
                                 "I do not know" says it instead of inventing. From M10 this
                                 reason runs the investigator first, and only a page still thin
                                 after the loop (or a loop that did not finish) reaches Opus
  * **proposed_write**           only a Tier-2 proposal may reach the gate
  * **no_answer**                Tier 1 never answered (transport, truncation). The packet is
                                 unjudged and Opus is standing right there

Escalation is a **rewrite**, not a review: Opus gets the identical page and writes its own
order. The Tier-1 answer stays in `agent.jsonl` as the receipt; the Tier-2 order is stored.
Nothing is retried at the same tier: a Tier-2 rejection is stored as rejected.

**Critical is not a pre-call reason from M10.** M7 sent every critical incident to Opus before
asking, on the argument that the worst incident deserved the best writer. Scott's call at the
M10 check-in: severity is code's and the rails hold at every severity, so the cheap judge is
asked first for the dead cow too, and its note proposal still escalates on `proposed_write`.

**The world count is not a per-incident trigger.** "Two or more sensing worlds opened
incidents" says nothing about what one packet contains; applying it per incident would send
every work order on every storm tick to Opus. What it decides is whether the tick needs
someone reading across the ranch, and that is `FUSION_THRESHOLD` on the shift report, which
from M10 is a Tier-1 call first like everything else.

## The price table replaces a constant, not a field

`cost_usd` on the tick line keeps its name and meaning. M4 priced every call at one assumed
rate, $15/$75 per million, which turned out to be 3x the Opus 5 list price; the tokens in the
ledger were always the measurement and the dollars were arithmetic, corrected here in one
place. Tier 1 is $0.00 by tier, not by model name, so a renamed local model cannot bill.
"""

from __future__ import annotations

from src.agent.state import WorkOrder
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
#: The reason codes, as they appear in `WorkOrder.escalation` and `ShiftReport.escalation` and
#: summed on the tick line as `escalation_reasons`. A closed vocabulary so a `jq` over the log
#: can count them. `critical` left the vocabulary at M10.
ESCALATE_PAGE_TOO_LONG = "page_too_long"
ESCALATE_REJECTED = "rejected"
ESCALATE_INSUFFICIENT = "insufficient_information"
ESCALATE_PROPOSED_WRITE = "proposed_write"
ESCALATE_NO_ANSWER = "no_answer"
ESCALATION_REASONS = frozenset({ESCALATE_PAGE_TOO_LONG, ESCALATE_REJECTED, ESCALATE_INSUFFICIENT, ESCALATE_PROPOSED_WRITE, ESCALATE_NO_ANSWER})

#: Characters per gemma token, conservative. Measured 3.4 on the real 18-order fused page
#: (35,848 chars, 10,630 tokens with the mandate, via `/api/generate` `prompt_eval_count` on
#: 2026-09-12) and 3.6 on a feed packet; 3.0 over-counts on purpose, because the failure this
#: guards is Ollama silently truncating the front of the prompt, and the cost of over-counting is
#: one Opus call.
CHARS_PER_TOKEN = 3.0
#: Room left for the chat template and the schema grammar, which are neither the prompt nor the answer.
CONTEXT_MARGIN_TOKENS = 512


def tier1_enabled() -> bool:
    """Whether the cascade is on at all. `TIER1_ENABLED` in `config.py`; off is Tier 2 for everything, which is the M2 to M6 shape."""
    return bool(get_settings().tier1_enabled)


def estimate_tokens(text: str) -> int:
    """A ceiling estimate of the local model's token count for `text`, at `CHARS_PER_TOKEN`."""
    return int(len(text) / CHARS_PER_TOKEN) + 1


def fits_tier1(text: str, *, max_tokens: int, num_ctx: int | None = None) -> bool:
    """Whether the prompt, the answer budget, and the margin fit inside `num_ctx`.

    M10, the one pre-call reason left. Ollama does not fail a prompt longer than `num_ctx`; it
    truncates from the front and answers about the rest, which for this repo's pages means the
    SOP or the first work orders are gone and the model cites what it never read. `call_tier1`
    catches that after the fact as `tier1_context_full`; this catches it before the call and sends
    the page to the tier that can read it whole.
    """
    ceiling = int(num_ctx or get_settings().ollama_num_ctx)
    return estimate_tokens(text) + int(max_tokens) + CONTEXT_MARGIN_TOKENS <= ceiling


def tier_for(text: str, *, max_tokens: int) -> int:
    """The tier that is asked first, for any job, given the prompt it will be asked with.

    Tier 1 for everything when the cascade is on and the page fits; Tier 2 otherwise. From M10
    the incident's severity is not an input: the rails hold at every severity and a human still
    answers every write, so there is nothing a critical incident needs from Opus before the cheap
    judge has been asked (`docs/state.md`, the M10 decisions).
    """
    if not tier1_enabled():
        return TIER2
    return TIER1 if fits_tier1(text, max_tokens=max_tokens) else TIER2


def pre_call_reason(text: str, *, max_tokens: int) -> str:
    """`page_too_long` when `tier_for` chose Tier 2 with the cascade on, or `""`. The code the
    stored order or report carries so the tick line can count why Opus was paid before any call."""
    return ESCALATE_PAGE_TOO_LONG if tier1_enabled() and not fits_tier1(text, max_tokens=max_tokens) else ""


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
    "CHARS_PER_TOKEN",
    "CONTEXT_MARGIN_TOKENS",
    "ESCALATE_INSUFFICIENT",
    "ESCALATE_NO_ANSWER",
    "ESCALATE_PAGE_TOO_LONG",
    "ESCALATE_PROPOSED_WRITE",
    "ESCALATE_REJECTED",
    "ESCALATION_REASONS",
    "FALLBACK_RATE",
    "PRICE_TABLE",
    "TIER1",
    "TIER2",
    "cost_usd",
    "escalation_reason",
    "estimate_tokens",
    "fits_tier1",
    "pre_call_reason",
    "rate_for",
    "tier1_enabled",
    "tier_for",
]
