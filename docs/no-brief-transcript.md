# The no-brief run: `compliance` with its patch removed

Run live on 2026-09-10 at M3, against the deployed ranch. Paired with
[`with-brief-transcript.md`](with-brief-transcript.md), which is the same model answering the
same page with one thing changed.

**The finding this pair exists to support is at the bottom of the other file.** Read this one
first, because the interesting thing about it is that it looks fine.

## The experiment

One variable. `system_prompt("compliance", mandate=" ")` strips the agent's patch out of the
brief and changes nothing else: the inherited rules are still there, the evidence packet is
byte-identical, the schema is the same forced tool call, the rails are the same rails, the model
is the same `us.anthropic.claude-opus-5` at `reasoning_effort="none"`.

So the difference between the two answers is exactly what
[`src/prompts/agent_prompts.py`](../src/prompts/agent_prompts.py) contributes, and nothing else.

| | no brief | with brief |
| --- | --- | --- |
| system prompt | 3,343 chars (inherited rules only) | 5,463 chars |
| input tokens | 5,713 | 6,370 |
| output tokens | 884 | 1,215 |
| latency | 12.7 s | 16.2 s |
| **rails** | **passes all of them** | passes all of them |
| actions written | **1** | 5 |
| neighbours named for handoff | **0** | 2 (`infrastructure`, `water_feed`) |

The brief costs 657 input tokens, about a third of a cent a call.

## What both runs were given

`compliance` was picked deliberately. It owns two of eighteen triage categories, so it is the
agent whose inherited rules pull hardest toward the ranch's louder problems: an unbriefed
compliance agent has cattle, water, and a dying battery on its page and no instruction that
those are somebody else's. `water_feed` would have been an easier and less honest test, because
the water SOP alone nearly is a brief.

The live sweep opened six `compliance` findings. The first one, and the page as the model
received it, verbatim:

```
## The incident, as triage ranked it in code

- severity: CRITICAL (already decided; not yours to change)
- category: range_dry
- sensor: alkali-flat-soil, a soil moisture sensor
- location: Alkali Flat
- reading: 4.2%, against a critical line of 5%
- triage said: Alkali Flat: soil moisture reads 4.2% on alkali-flat-soil, at or below the critical line of 5%. This ground is bare-dry. Grazing it at planned stocking is how a conservation payment turns into a finding.
- history of this incident: seen 1 time(s), first at 2026-09-10T20:08:41.585000+00:00, still opened

## This sensor's last 12 readings, newest first

  (This series is generated independently of the reading above and does NOT contain it. Use it for the range and the trend; the authoritative current value is the triaged reading.)
  - 2026-09-10T20:08:45.396Z: 58%
  - 2026-09-10T19:58:45.396Z: 18.2%
  - 2026-09-10T19:48:45.396Z: 26.1%
  - 2026-09-10T19:38:45.396Z: 31.4%
  - 2026-09-10T19:28:45.396Z: 28.2%
  - 2026-09-10T19:18:45.396Z: 25.1%
  - 2026-09-10T19:08:45.396Z: 35.8%
  - 2026-09-10T18:58:45.396Z: 17.1%
  - 2026-09-10T18:48:45.396Z: 6.9%
  - 2026-09-10T18:38:45.396Z: 56.2%
  - 2026-09-10T18:28:45.396Z: 45.6%
  - 2026-09-10T18:18:45.396Z: 23.1%

## Other sensors at Alkali Flat, read in this same sweep

  - battery charge (alkali-flat-battery): 11.1%
  - fence energizer voltage (alkali-flat-fence): 5.8 kV
  - gate (alkali-flat-gate): closed
  - relative humidity (alkali-flat-humidity): 37%
  - air temperature (alkali-flat-temp): 71 F
  - stock-tank level (alkali-flat-water): 2.6 gal
  - stock-tank level (alkali-flat-water-2): 17.6 gal

## What is standing behind it

  Alkali Flat (alkali-flat): 2400 acres, 111 head on it, barbed-wire, pasture status open

## The standing orders that apply (compliance.md)

  [the whole of data/knowledge_base/compliance.md, COMP-01 through COMP-05, unabridged]
```

Note what is already on that page without any brief at all: a severity decided in code, a full
SOP set, `COMP-04` telling the reader in as many words that stock water is not the compliance
gauge, and `COMP-05` telling it that a gauge it cannot believe is `infrastructure`'s repair while
the hole in the record is its own. That matters for reading what follows. **Nothing the brief
adds below is information the no-brief run lacked.**

## The answer, with no brief

```json
{
  "severity_echo": "critical",
  "headline": "Alkali Flat: soil moisture 4.2% with 111 head on it, log the reading and get GM on stocking review",
  "assessment": "alkali-flat-soil reads 4.2% at Alkali Flat, at or below the 5% critical line, first seen 2026-09-10T20:08:41Z and still open. Alkali Flat is 2400 acres carrying 111 head, pasture status open, so this is a stocking decision and not just a note. The recent series on the same sensor swings from 6.9% to 58% within two hours, so the instrument's own record is erratic and the defensible record for this period is thin; the current triaged value is still the authoritative reading. Sibling sensors in the same sweep say nothing wet or dry about the ground: alkali-flat-temp 71 F, alkali-flat-humidity 37%, alkali-flat-battery 11.1% (low enough to question the datalogging), and two tanks reading alkali-flat-water 2.6 gal and alkali-flat-water-2 17.6 gal, which are stock water on an hours clock and belong to a separate water work order, not to this record.",
  "actions": [
    "Log the compliance record now: alkali-flat-soil 4.2% at Alkali Flat, critical line 5%, first seen 2026-09-10T20:08:41Z, pasture alkali-flat 2400 acres, 111 head on it, status open (COMP-01, COMP-05)"
  ],
  "rules_cited": ["COMP-01", "COMP-05", "COMP-04"],
  "escalate": true,
  "escalate_reason": "COMP-01 requires the general manager on every critical range reading, and 111 head on Alkali Flat makes this a stocking and lease question that is not the crew's to answer.",
  "unknowns": [
    "Whether Alkali Flat sits on a state or federal allotment, and its AUM stocking obligation",
    "Recent precipitation, forage stage, and rest history for this pasture: none on this page",
    "Whether alkali-flat-soil is reading true; battery alkali-flat-battery at 11.1% and a series ranging 6.9% to 58% in two hours are not a trustworthy record",
    "Which period of the moisture record has no defensible reading behind it if the sensor is bad"
  ]
}
```

That is not flailing. It echoes severity correctly, names the sensor behind every number, quotes
nothing that is not on the page, cites three rule ids that all exist, and escalates for a real
reason. **Run through `workers.check` it produces zero violations and ships.** The expectation
going in was a visibly incompetent answer, and that is not what came back. What came back is
worse, and the two failures in it are the ones a rail cannot see:

1. **One action.** The whole job has collapsed into bookkeeping. The headline promises "get GM on
   stocking review" and the actions list does not contain it, so the one thing this order asks a
   human to do is file a note. Nobody is sent to look at the ground.
2. **Nobody is named.** It notices the tanks and says they "belong to a separate water work
   order" without saying whose. It doubts the gauge and puts that doubt in `unknowns`, which is
   where facts go to be nobody's problem, rather than handing the instrument to the agent that
   repairs instruments. `COMP-04` and `COMP-05` were both on the page and both say who; the model
   read them as background rather than as instructions to itself, because nothing told it that it
   is one of four agents working one sweep.

Then it does the thing the brief's third section exists to prevent: it spends a third of its
assessment on two stock tanks. Correctly, even, and in a compliance record where it is noise.

The other file is the same question with the patch put back.
