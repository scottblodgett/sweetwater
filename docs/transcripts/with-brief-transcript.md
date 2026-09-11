# The with-brief run: `compliance` with its patch in place

The other half of the pair. Read [`no-brief-transcript.md`](no-brief-transcript.md) first: it
carries the experiment setup and the rendered evidence page, and this run received that page
**byte for byte identical**. One variable, and it is the ~2,100 characters of
`COMPLIANCE_MANDATE` from [`src/prompts/agent_prompts.py`](../src/prompts/agent_prompts.py).

Receipt: `bedrock`, `us.anthropic.claude-opus-5`, `tool_use`, 6,370 in / 1,215 out, 16,156 ms,
no error.

## The answer, with the brief

```json
{
  "severity_echo": "critical",
  "headline": "Alkali Flat: soil moisture 4.2% with 111 head on it, stocking review to GM, gauge suspect",
  "assessment": "Alkali Flat, 2026-09-10: alkali-flat-soil reads 4.2% soil moisture, at or below the 5% critical line, with 111 head standing on the 2400-acre pasture (status open, barbed-wire). This is a stocking decision, not a note. The same sensor's last 12 readings swing from 6.9% to 58% inside two hours (18.2% at 19:58Z, 58% at 20:08:45Z), so the instrument's stability is questionable and the defensible record for this period is thin; that repair belongs to infrastructure, but the hole in the record is ours. Supporting conditions in the same sweep: alkali-flat-temp 71 F, alkali-flat-humidity 37%, alkali-flat-battery at 11.1% charge, which may bear on why the soil series is jumping. Stock tank readings at this location (alkali-flat-water 2.6 gal, alkali-flat-water-2 17.6 gal) are water_feed's work order, not evidence about range condition, and are not addressed here.",
  "actions": [
    "Log for the program file now: Alkali Flat, sensor alkali-flat-soil, 4.2% soil moisture, 2026-09-10T20:08:41Z, critical line 5%, pasture alkali-flat 2400 acres carrying 111 head, status open (COMP-01, COMP-05)",
    "Escalate to the general manager for a stocking review on Alkali Flat with the lease in front of them: this ground will not carry what the grazing plan assumed. Do not set an AUM number in the field (COMP-01)",
    "Send someone to Alkali Flat to eyeball forage and ground cover against the reading, and to note whether the site is representative of the pasture, recorded as a dated observation (COMP-01, COMP-05)",
    "Hand alkali-flat-soil to infrastructure for verification: the 12-reading series runs 6.9% to 58% within two hours and alkali-flat-battery reads 11.1%. Note in the file that this period has no stable reading behind it (COMP-05)",
    "Pass the alkali-flat-water 2.6 gal and alkali-flat-water-2 17.6 gal tank readings to water_feed in their own work order, separate from this one (COMP-04)"
  ],
  "rules_cited": ["COMP-01", "COMP-04", "COMP-05"],
  "escalate": true,
  "escalate_reason": "COMP-01 requires the general manager on every critical range reading, and this pasture is carrying 111 head, which makes it a stocking and lease question rather than a crew call.",
  "unknowns": [
    "Whether Alkali Flat is state, federal, or deeded ground, and the AUM obligation attached to it; the packet does not say",
    "Recent precipitation, forage stage, and rest history for Alkali Flat, all of which govern recovery and none of which is on this page",
    "Readings from other soil moisture sensors on other allotments, needed to tell one dry site from a season",
    "Whether alkali-flat-soil is calibrated and powered correctly, given the 6.9% to 58% swing and 11.1% battery charge"
  ]
}
```

Also zero violations. Both orders ship.

## What the brief bought, line by line

Every difference below traces to a specific sentence of the mandate, which is the point of
running it as a controlled pair rather than eyeballing two answers.

| `COMPLIANCE_MANDATE`, verbatim | The no-brief run | The with-brief run |
| --- | --- | --- |
| "`water_feed` owns the tanks. This is the mistake to not make." | notices the tanks, says they "belong to a separate water work order", **names nobody**, and spends about a third of the assessment on them | "are `water_feed`'s work order, not evidence about range condition, and are not addressed here", one sentence, plus an action handing them over |
| "A quiet or impossible gauge is `infrastructure`'s repair. But an unmonitored reach still cannot be evidenced, and saying so is yours." | doubt about the gauge goes in `unknowns`, which is where facts go to be nobody's problem | "that repair belongs to `infrastructure`, but the hole in the record is ours", plus an action: "Hand `alkali-flat-soil` to `infrastructure` for verification" |
| "in a way a person could hand to an auditor eight months from now without you in the room to explain it" | never mentions the clock or the reader | "Log for the program file", "recorded as a dated observation", the date written into the action text |
| "the only one that can speak to a stocking rate at all. Use it, and only for that." | mentions 111 head, then files a note | "Escalate to the general manager for a stocking review ... with the lease in front of them ... Do not set an AUM number in the field" |
| "There is no hauling water to repair a record." | one action, and it is bookkeeping | five, including sending a human to walk the ground and eyeball forage |

The headline gap is the sharpest single item, and it is not something the mandate addresses
directly. The no-brief headline promises "get GM on stocking review" and its actions list
contains **no escalation step**. `escalate: true` is set, so the supervisor will page somebody,
but the order handed to the crew never says to. Nothing in the brief says "put the escalation in
the actions." What the brief did was give the agent four other things a person could actually go
do, and once it was writing instructions to people rather than a record for a file, the missing
one appeared with them.

## The finding

The experiment was set up expecting the unbriefed agent to flail: hedge, drift off its lane,
cite something that does not exist, contradict a severity code already decided. It did none of
that. **The unbriefed answer is coherent, correctly grounded, honestly uncertain, and passes
every rail with zero violations.** Verified by running `workers.check` over both payloads:

```
no_brief   violations: []  blocking: []
with_brief violations: []  blocking: []
```

So the finding is not "the brief prevents bad output." It is narrower and more useful:

> **The rails cannot detect a missing brief.** Every rail in `workers.check` asks whether the
> answer is *defensible* - does the severity match, is the sensor named, does the rule id
> exist, is there a real action. All four are local to one incident, and an unbriefed model
> answers all four correctly. What the brief supplies is scope: which of the four agents this
> one is, what belongs to the neighbours, who to hand a thing to, and who is reading the page
> in a year. None of that is checkable from inside a single work order, so a slice whose brief
> silently regresses to nothing keeps a green suite.

Two consequences, both already acted on:

1. `RECORDED_ANSWER` in [`tests/test_agent.py`](../tests/test_agent.py) exists precisely because
   this class of regression is invisible to the rails. It is a calibration fixture, not a rail:
   if a prompt change makes it fail, the answer got worse. It is the only thing in the suite
   that would notice.
2. The mandate's third part - "water and feed on an hours clock are not yours" - was written
   before this run as a guess about where an unbriefed compliance agent would wander. The
   no-brief answer wandered exactly there, on its own, with `COMP-04` sitting on the page telling
   it not to. **The SOP was not enough.** Both runs had the whole of `compliance.md` in front of
   them, `COMP-04` ("stock water is not the compliance gauge") and `COMP-05` ("the instrument is
   `infrastructure`'s repair, the hole in the record is yours") included. Standing orders describe
   the ranch. The brief tells an agent which part of it is its own, and only the second one
   produced the handoff.

That last point is the load-bearing one for the architecture, and it is why the brief is not
merely a nicer version of the SOP set. The knowledge base is what a domain knows; the mandate is
who is asking. A sub-agent inherits nothing, so if the mandate does not say it, the SOP sitting
right there on the page does not rescue it.

Reproduce with `logs/brief_experiment.py`. It spends two Opus calls, about four cents.
