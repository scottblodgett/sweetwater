# SOP: Compliance - range, habitat, and the payments attached to both

Derived from `docs/sweetwater-ranch.md`. Nothing else in this repo may source these rules,
and no rule here may be invented at the prompt: an agent that cites `COMP-06` when there is no
`COMP-06` is worse than one that says it has no rule to cite.

**The standing order this file serves:** keep the payments, prove the stewardship. Two of this
ranch's revenue lines are not cattle. State and federal grazing leases carry AUM stocking
obligations, and the conservation and habitat programs pay against grazing management, water
infrastructure, and wildlife migration. Both are paid for demonstrated conditions, and a
condition nobody recorded was not demonstrated.

**What makes this world different, and do not borrow urgency from the others.** Nothing in this
file kills an animal this afternoon. What it costs is money and eligibility, months later, in a
form nobody can drive out and fix: a stocking obligation missed, a reach run thin through a
period that gets scored, a stewardship claim with no record behind it. There is no hauling water
to repair a record. That is a longer clock, not a lower priority, and the two are easy to
confuse.

**So these work orders are evidence as much as instruction.** Be specific about the reading, the
sensor, the location, and the time in a way somebody could hand to an auditor eight months from
now without you in the room to explain it. Vagueness costs nothing today and costs the payment
later.

---

## COMP-01 - Bare-dry ground is a stocking decision, and it will not wait for the next season

**Applies to:** `range_dry`, severity `critical`, on a `soil-moisture` sensor.

Soil moisture on this ground has fallen far below what the grazing plan assumes. Continuing to
graze it at planned stocking is how a conservation payment turns into an audit finding.

1. **Record the reading, the sensor, the location, and the time first,** before any
   recommendation. This is the part that is still useful in eight months.
2. **Name the allotment or pasture and whether the packet shows animals on it.** A dry
   allotment with nothing on it is a note. A dry allotment carrying cattle is a decision.
3. **Recommend a stocking review, not a number.** Reducing AUMs on a leased allotment is the
   general manager's call with the lease in front of them. Say the ground will not carry what
   the plan assumed and let that conversation happen.
4. **Say what is unknown.** Recovery depends on precipitation, forage stage, and rest, and none
   of that is on this page. Put it in the unknowns rather than forecasting it.

**Escalate to the general manager** on every critical range reading on a state or federal
allotment. A stocking obligation on somebody else's ground is a lease question and a
relationship, and it is not the crew's to answer.

## COMP-02 - Drying past the plan is the moment the record is cheap to make

**Applies to:** `range_dry`, severity `warning`, on a `soil-moisture` sensor.

The ground is drying past what the grazing plan assumed. It is not yet a stocking problem.

1. **Log it as a dated observation with the reading.** That is the entire deliverable and it is
   worth more than it looks: a documented trend that starts in the warning band is what makes a
   later stocking decision defensible rather than reactive.
2. **Read it against the other soil sensors in the packet.** One dry site is a site. Several
   across the allotments is a season, and only the second one changes a grazing plan.
3. **Do not dispatch anybody.** There is nothing for a truck to do about soil moisture, and a
   work order that sends one teaches the crew that this whole category is noise.

## COMP-03 - The creek gauges are what the habitat program is scored on

**Applies to:** `stream_flow_low` at any severity, on a `stream-flow` sensor.

Riparian flow on this ranch is a scored condition. Several habitat and conservation payments are
tied to it and to the wildlife that depend on it, and a thin reach through a scored period is
money.

1. **Critical:** the reach is nearly dry. Record it precisely, note the date, and flag it for
   the program file. Whether it is drought, an upstream diversion, or a change in irrigation
   practice is the question that follows, and the packet does not answer it.
2. **Warning:** flow is thin. Log the reading and the trend from the recent readings. This is
   the band where a documented series is built.
3. **Say whether irrigation is a plausible factor and mark it unknown if the page cannot say.**
   The irrigated hay ground and the creeks draw from related water, so a thin reach during
   irrigation season is a different record than a thin reach in October. Do not assert which one
   this is without evidence on the page.
4. **A dry reach also matters to the migration corridors and the camera sites near it.** One
   sentence, if the packet shows anything at that location. Do not build the wildlife case out of
   nothing.

## COMP-04 - Stock water is not the compliance gauge, and this is the mistake to not make

**Applies to:** `stream_flow_low`, and to every temptation to read a creek gauge as stock water
or a tank as evidence of flow.

This is the single most likely error in this file, because both are water and one agent can see
both.

1. **A thin creek is not permission to leave a tank empty.** The stock tanks are `water_feed`'s
   and they run on a clock of hours. Nothing in this file changes that or defers it.
2. **A full tank is not evidence a reach is flowing.** They are different water, measured by
   different instruments, scored for different reasons.
3. **Keep them in separate work orders,** always. Merging them produces a document that is
   useless to the crew this afternoon and useless to an auditor next year.
4. **You may count animals. You may not diagnose them.** The head count on a pasture is the
   numerator of every stocking question and that is what it is for here. Animal health belongs
   to `herd_health`, and a compliance work order is a poor place for a medical opinion.

## COMP-05 - A record without a reading is not a record

**Applies to:** every category in this file, at every severity.

1. **Every claim traces to a sensor id and a value on this page.** Name both. An assertion about
   range condition with no instrument behind it is worth nothing at audit and it is worth less
   than nothing if it turns out to be wrong.
2. **Never estimate acres, AUMs, forage tonnage, precipitation, or a percentage of a lease.**
   These are exactly the numbers that look authoritative in a compliance document and exactly
   the ones a program officer can check. If it is not measured, it is an unknown.
3. **A stated absence is a gap, not a zero.** "No animal roster returned for this pasture" is
   not "no animals on this pasture," and in a stocking record those two sentences are opposites.
4. **When a gauge cannot be believed, the reach cannot be evidenced.** The instrument is
   `infrastructure`'s repair. The hole in the record is yours: say which period has no defensible
   reading behind it, because that gap is what somebody asks about later.

---

## The two things never to write in a compliance work order

1. **An all-clear.** Code already put this reading outside its band. "Range conditions appear
   satisfactory" in a document meant to demonstrate stewardship is worse than saying nothing,
   because it is a stewardship claim nobody verified.
2. **A finding of compliance or non-compliance.** You are not the program officer and you are
   not the lease administrator. Record the condition, name the obligation it bears on, and let a
   human make the determination. A work order that rules on a lease creates a written admission
   or a written false assurance, and both are expensive.
