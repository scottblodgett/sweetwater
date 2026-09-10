# SOP: Wellhead - the safety and regulatory world

Derived from `docs/sweetwater-ranch.md`. Nothing else in this repo may source these rules,
and no rule here may be invented at the prompt: an agent that cites `WELL-07` when there is
no `WELL-07` is worse than one that says it has no rule to cite.

**The standing order this file serves:** no spill, no fault, no fine. A few oil and gas wells
sit on this ranch and pay mineral royalties. The ranch does not operate them and does not
earn from producing them harder. It earns from the royalty and it loses from an incident, so
every pressure reading is asymmetric: there is no upside to a well running hot and there is a
large downside to one leaking.

**What makes this world different.** Everything else on this ranch fails toward an animal.
This fails toward the ground, the water, and a regulator. The clock is short like water's, but
the reason is different: a release is not recoverable by getting there later. And unlike a
tank, the fix is almost never something the ranch crew performs. Somebody with authority over
that wellhead has to act, which makes the phone call the deliverable.

**Separate from `infrastructure.md` on purpose,** even though the same agent works both. A
gate and a wellhead are not the same job, and a packet about an open gate should not be billed
for these rules.

---

## WELL-01 - Overpressure is a call now, not a check later

**Applies to:** `wellhead_overpressure`, severity `critical`, on a `wellhead-pressure` sensor.

Pressure is above the band the well is supposed to hold. From a gauge, an impending relief
event and an active one look the same.

1. **Notify the operator of record for this well immediately, and say so as the first action.**
   The ranch crew does not adjust a wellhead. Anyone who does not have that authority driving
   out there is exposure, not help.
2. **Get eyes on the pad from a safe distance** and report what is visible: staining on the
   ground, sound, vapour, anything at the tank battery. Visual confirmation is what turns a
   reading into a report somebody can act on.
3. **Say whether the flowline or the ROW runs toward water.** A release next to a creek or a
   riparian reach is a different event, and the packet's location is what tells you.
4. **Treat the H2S possibility as a hard stop on approach, not a footnote.** If this pad
   carries sour gas, nobody walks up to it on a pressure alarm. Write that into the work order
   rather than assuming the crew remembers which pads those are.

**Escalate to the general manager** on every critical wellhead reading, without exception.
This is the one category where the ranch's own liability and its lease and easement standing
are both on the line, and it is not a decision the person driving the truck should be making
alone.

## WELL-02 - Above the band is worth eyes before it becomes an event

**Applies to:** `wellhead_overpressure`, severity `warning`, on a `wellhead-pressure` sensor.

Running above normal is not a release. It is the reading that precedes one often enough to be
worth a look on this shift.

1. Report it to the operator. That is the whole action, and it is enough. Do not build a
   response the ranch is not the one performing.
2. **Read the trend in the packet's recent readings, and say which direction it is going.**
   Pressure drifting up over the last two hours and pressure that spiked and settled are the
   same band and not the same day.
3. Do not send the crew to the pad on a warning. There is nothing for them to do there and the
   approach itself carries risk.

## WELL-03 - Falling pressure is what a leak looks like from here

**Applies to:** `wellhead_underpressure` at any severity, on a `wellhead-pressure` sensor.

Pressure sagging below the band reads as a leak, a failing pump, or a line losing containment.
Underpressure is quieter than overpressure and not safer: a slow release runs longer before
anybody notices.

1. **Critical:** report loss of containment as the working assumption and let the operator rule
   it out. Do not soften it into "pressure is a bit low" because nothing dramatic is visible;
   a slow leak is exactly the one nothing is visible at.
2. **Warning:** report it and note the trend. A steady sag is mechanical and a stepwise drop is
   usually a line.
3. **Walk the flowline and the pipeline right-of-way toward the low ground,** and say what the
   ground looks like. Product goes downhill and so does the evidence.
4. **Say whether this pad's cathodic protection is something the packet can speak to.** If it
   is not in front of you, put it in the unknowns rather than guessing. Corrosion on an unprotected
   line is the ordinary cause of a slow loss, and the reason the question belongs in the order.

## WELL-04 - Write the report, do not write the ruling

**Applies to:** every wellhead category, at every severity.

There is a real temptation in this world to write as though the ranch were the regulator or the
operator. It is neither.

1. **State the reading, the sensor, the location, the time, and what was observed.** That is a
   record somebody can hand to an operator or an agency and it holds up without you in the room.
2. **Do not characterize reportability, volume released, or a violation.** Whether an event is
   reportable, and by whom, is a legal determination with thresholds this packet does not
   contain. Guessing at it in a work order creates a document that is wrong in writing.
3. **Do not estimate a spill volume, an area, or a duration.** If the number is not on the page,
   it goes in the unknowns. An invented volume in a record about a release is the single most
   expensive fabrication available anywhere in this system.

---

## The two things never to write in a wellhead work order

1. **An all-clear.** Code already put this reading outside its band. "The well appears normal"
   is a contradiction, and here it is a contradiction in a document that may be read by
   somebody other than the crew.
2. **A number nobody measured.** Quote the pressure the sensor reported and name the sensor.
   Everything else, including how long it has been out of band and how much has moved, is
   unknown until somebody is standing there.
