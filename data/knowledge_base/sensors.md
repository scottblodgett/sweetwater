# SOP: Sensor health - the instruments themselves

Derived from `docs/sweetwater-ranch.md`. Nothing else in this repo may source these rules,
and no rule here may be invented at the prompt: an agent that cites `SENSOR-08` when there is
no `SENSOR-08` is worse than one that says it has no rule to cite.

**The standing order this file serves:** the sensors are how a small crew watches 34,000 acres
it cannot drive every day. A broken sensor is a maintenance job. What makes it urgent is not
the instrument, it is the acre nobody is watching while it is broken.

**What makes this world different from every other file here.** Every other standing order
starts from a fact about the ranch. These start from the absence of one. The reading is not
telling you the tank is low or the fence is dead; it is telling you that you no longer know.
That inverts the usual instinct: the less a work order can say about what is actually
happening on the ground, the more it matters that somebody goes and looks.

**Every sensing world's instruments are worked here, by one agent, on purpose.** A dead water
probe, a dead fence sensor, and a dead creek gauge are the same job with the same truck and
the same spare parts. Whoever owns the acre still owns the acre; this file owns the box on
the post.

---

## SENSOR-01 - A silent sensor is not a quiet pasture

**Applies to:** `sensor_offline`, on a sensor whose status came back `offline` with no reading
at all.

The sensor is dark. It is reporting nothing, which is a completely different thing from
reporting that everything is fine, and the two are indistinguishable to anybody who does not
read this line.

1. **Say how long it has been dark if the packet can tell you, and say it is unknown if it
   cannot.** A sensor that dropped in the last few minutes is usually a radio; one that has
   been out for a day is a battery or a dead unit.
2. **Check power before hardware.** Most of these sites run on a panel and a battery, and a
   flat battery accounts for more dark sensors here than failed electronics do. If the packet
   shows a `battery-charge` sensor at this location, read it first and say what it said.
3. **Name what this sensor was watching and put physical eyes on it.** This is the action that
   actually matters. The sensor can wait for the next parts run. The tank, the fence, or the
   gate it was watching cannot wait to be unwatched.
4. **Do not infer the ground truth.** You have no reading. Anything about the state of what
   this sensor watches belongs in the unknowns, and the crew's eyes are what closes it.

**Escalate to the general manager** when two or more sensors at the same location went dark in
the same tick. That is not sensors failing, that is the site losing power or losing its uplink,
and it means the location is entirely unwatched rather than partly.

## SENSOR-02 - A degraded sensor is worse than a dark one, because it still answers

**Applies to:** `sensor_degraded`, on a sensor reporting `degraded` status while still
producing values.

The sensor is answering and its answers cannot be trusted. A dark sensor announces its own
failure. This one keeps talking, and every value it produces gets read by code and by people
as though it were measured.

1. **Say in the work order that this location's readings are currently unreliable in both
   directions.** A degraded probe can read high as easily as low, so the pasture is not
   "probably fine, sensor is just flaky."
2. **Schedule the repair or swap, and treat it as ahead of a dark sensor of the same age.**
   Ranking a talking-but-wrong instrument behind a silent one is the mistake this rule exists
   to prevent.
3. **Use the sibling readings at the same location to say which way it is wrong, when they can
   tell you.** A degraded tank probe next to a second tank on the same supply is a diagnosis;
   the same probe alone is not.
4. **Do not average, correct, or interpolate a degraded reading into a number you then quote.**
   A repaired number reads exactly like a measured one and nobody downstream can tell.

## SENSOR-03 - A reading outside physical possibility is a broken probe, not weather

**Applies to:** `sensor_fault`, on a sensor whose value falls outside what its type could
physically produce.

The value is impossible, not extreme. This ranch has a temperature sensor that returns -500
while reporting itself online and healthy, and that is the whole lesson: a sensor can be
confident and wrong at the same time.

1. **Say the reading is a fault and name the value, so the next person does not rediscover it.**
   Quote it. Do not describe it as a cold snap, a surge, an empty tank, or a dry creek.
2. **Do not act on the value in any direction.** Not as a low reading, not as a high one, and
   not as a hint. The number carries no information about the ground at all.
3. **A wired probe, its connector, or its cable is the usual cause,** so the repair is a site
   visit with the sensor's spare parts rather than a diagnostic from the yard.
4. **Treat the location as unmonitored until it is fixed,** exactly as with a dark sensor, and
   get eyes on whatever it was watching.

## SENSOR-04 - A sensor type nobody has written a rule for is a gap in this document

**Applies to:** `unknown_sensor_type`, on any sensor whose type has no standing orders.

Something is reporting from the ranch and no rule here knows what normal looks like for it.
This is a real finding and it is a finding about this repository, not about the pasture.

1. **Report the type name and the reading exactly as they arrived, and stop there.** Do not
   reason about whether the value is good or bad. You have no band, and inventing one is how a
   new sensor type gets a threshold nobody ever measured.
2. **Say plainly that no standing order covers this type,** and cite this rule as the reason
   there is nothing else to cite.
3. **The action is to get a rule written, not to dispatch a truck.** Somebody has to decide
   what normal is for this type and add it to the standing orders. Until then the sensor is
   decoration.
4. Do not guess the type's units from its name. A number whose unit you assumed is worse than
   a number with no unit at all.

## SENSOR-05 - Report the instrument, and let the acre's owner report the acre

**Applies to:** every category in this file.

You own the boxes on the posts across all of the ranch's sensing worlds, which means you will
routinely write about tanks, bins, fences, and creeks that you do not otherwise own.

1. **The instrument is yours. The consequence is not.** "The water probe at this tank is
   reading outside physical possibility and needs a probe and a connector" is this work order.
   "The cattle here may be out of water" belongs to whoever owns that water, they are already
   being told, and writing it here produces two work orders about one tank and teaches the crew
   to skim both.
2. **One exception, and it is the reason this rule is last rather than first: always say what
   went blind.** The pasture, the tank, the reach, or the line that is now unwatched is a fact
   about the instrument, not a claim about the ground, and it is the only part of this work
   order that makes anybody hurry.
3. **Two readings a few seconds apart are not expected to agree,** and a difference between
   them is not evidence of a fault by itself. Do not build a case for a broken sensor out of
   two values that were never meant to match.

---

## The two things never to write in a sensor work order

1. **An all-clear.** Code already found this instrument untrustworthy. "The sensor appears to
   be working" is a contradiction, and here it is the one that puts an acre back on the watched
   list without anybody having watched it.
2. **A reading you repaired.** Not an average, not an interpolation, not a corrected value, and
   not the last good number as though it were current. The whole point of this file is that
   there is no number, and a plausible substitute is the most expensive thing you could offer.
