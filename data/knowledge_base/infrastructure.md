# SOP: Infrastructure - containment, power, and reserves

Derived from `docs/sweetwater-ranch.md`. Nothing else in this repo may source these rules,
and no rule here may be invented at the prompt: an agent that cites `INFRA-09` when there is
no `INFRA-09` is worse than one that says it has no rule to cite.

**The standing order this file serves:** the crew is small, the distances are long, and
almost nothing out here is attended. What holds cattle, what powers a remote site, and what
fuels the trucks are the ranch's ability to operate at all. Keep them working.

**What makes this world different.** You are almost never looking at the loss itself. You
are looking at what removed the ranch's ability to prevent one. A downed energizer kills
nothing; cattle on a county road do. A dead battery does not empty a tank; it makes an empty
tank invisible, which is worse, because a silent sensor and a full tank are the same picture
from here. So these work orders are about restoring capability, and the urgency almost always
comes from what is standing behind the reading rather than from the reading.

**This file does not cover the wells or the instruments.** Wellhead pressure has its own
standing orders (`wellhead.md`, the no-spill side of the ranch) and sensor health has its own
(`sensors.md`). Both are also `infrastructure`'s work. They are separate files because they
are separate jobs with different clocks.

---

## INFRA-01 - A fence with no voltage is unfenced, and that is a today job

**Applies to:** `fence_down`, severity `critical`, on a `fence-voltage` sensor.

The energizer is not pushing. Miles of wire are holding nothing, and the cattle behind it are
loose in every sense except that they have not walked off yet.

1. **Get somebody on the energizer this shift.** Check the energizer itself first, then its
   power source. Most of these are the energizer, its ground rods, or the panel and battery
   feeding it, and all three are checked from the same truck.
2. **Say what is behind the fence in the work order,** by pasture and by head count when the
   packet has one. A dead energizer on an interior cross-fence and a dead energizer on a
   boundary next to a road are the same reading and are not the same emergency.
3. **Ride the line, do not just fix the box.** A hard short is a downed wire somewhere, and
   the wire is what put the energizer on the floor in the first place.
4. **Do not close it on a good voltage at the energizer.** The reading that matters is at the
   far end of the run.

**Escalate to the general manager** when the fence is a boundary or a leased-allotment
perimeter. Cattle off the deeded ground is somebody else's ground, a neighbour call, and on a
state or federal allotment a lease problem.

## INFRA-02 - A weak energizer is a short, not a shrug

**Applies to:** `fence_down`, severity `warning`, on a `fence-voltage` sensor.

The line is hot but underpowered, which means something is bleeding it: vegetation on the
wire, a cracked insulator, a wet post, or a gate handle left hooked wrong.

1. Put it on this week's fence route. It holds cattle poorly rather than not at all, and the
   difference is real.
2. **Read it against the weather at the same location before calling it a short.** A weak
   line the morning after high wind is wind damage and belongs with INFRA-06, not with
   routine maintenance.
3. If the same energizer has been in the warning band across several ticks, it is not
   weather. Stop routing it and schedule the line.

## INFRA-03 - An open gate is a question for a human, never an assumption

**Applies to:** `gate_open`, severity `warning`, on a `gate` sensor.

A gate reads open for two reasons: a crew opened it on purpose during a move, or cattle are
drifting somewhere they should not be. There is no reading that tells the two apart, which is
exactly why this never carries a critical band and always carries a phone call.

1. **Ask who opened it before sending anyone to close it.** Closing a gate in the middle of a
   move splits a herd, and that costs a day.
2. If nobody opened it, treat it as a containment event: what pasture is on each side, what
   is in them, and what road or water is reachable through the opening.
3. **Say plainly in the work order that the sensor cannot tell intent.** A work order that
   reads as though the gate were definitely a problem trains the crew to ignore this alarm,
   and it is the alarm least able to survive being ignored.

## INFRA-04 - A dying battery takes the pump and the eyes with it

**Applies to:** `power_low` at any severity, on a `battery-charge` sensor.

Remote sites here run on a panel, a battery, and sometimes a generator. The battery is what
stands between a working site and a dark one.

1. **Critical:** the site is about to go down. Whatever this battery powers, a pump or a
   telemetry radio or both, is minutes to hours from stopping. Get a charger, a generator, or
   a replacement battery on site today.
2. **Warning:** the site is drawing down faster than the panel is replacing it. Check the
   panel for snow, dust, or a bad angle, and check the charge controller, before assuming the
   battery is finished.
3. **Name what this site powers, and say it will go quiet as well as dark.** This is the one
   rule in this file with a second-order consequence worth writing down: when a solar water
   site loses its battery, the tank stops filling and the tank also stops reporting, and the
   second failure hides the first. If the packet shows a water sensor at this location, say
   that its silence is about to become meaningless rather than reassuring.

**Escalate to the general manager** when the site is a water site. A dead battery at a stock
tank is a water emergency wearing an electrical costume.

## INFRA-05 - Bulk fuel is a lead-time problem, so it is found early or not at all

**Applies to:** `fuel_low` at any severity, on a `fuel-level` sensor.

The diesel and propane at the home place feed the generators, the feed trucks, and the loader.
A delivery out here is not a same-day thing.

1. **Critical:** nearly out. Order the delivery now and say in the work order what stops
   working when it runs dry, because that is what makes this urgent rather than tidy.
2. **Warning:** schedule the delivery. This is the correct time to handle it, which is the
   whole point of the warning band existing on a tank nobody watches.
3. **Read it against the weather.** Propane at a warning level with a hard freeze coming is a
   different order than the same tank in September: tank heaters and cab heat both draw on it
   exactly when a truck cannot get in.
4. Do not estimate days of supply. Quote the percentage the sensor gave and let the person
   who knows the burn rate do that arithmetic.

## INFRA-06 - Wind is what puts wire on the ground, so read it with the fences

**Applies to:** `high_wind` at any severity, on a `wind-speed` sensor.

Wind on its own damages nothing the ranch can act on. What it does is cause the next several
alarms.

1. **Critical:** expect fence faults, limbs and tumbleweed on the wire, drifted two-tracks,
   and stock drifting to whatever shelter exists. The work order is a warning to the crew
   about the day ahead, not a repair.
2. **A high wind reading and a fence fault in the same tick are one problem, not two.** Write
   them as one cause with one response: ride the lines downwind of the weather station.
   Splitting them sends two trucks and diagnoses neither.
3. **The met tower's wind reading serves two masters and only one of them is yours.** It is on
   the ranch to prove the site for a wind developer. That is a development question, nobody is
   dispatched about it, and it is not what this work order is for.
4. Do not recommend moving cattle to shelter on a wind reading alone. Where the shelter is and
   whether the herd can get there is a crew judgment the packet does not contain.

---

## The two things never to write in an infrastructure work order

1. **An all-clear.** Code already found something wrong here before any of this was assembled.
   "The plant looks fine" is a contradiction, not a finding.
2. **The downstream consequence as if it were yours.** You may say a tank is now unmonitored,
   because that is what a dead battery did. You may not write the water order: `water_feed`
   owns whether the cattle at that tank are in trouble, it is already being told, and two work
   orders about one tank teaches the crew to skim both.
