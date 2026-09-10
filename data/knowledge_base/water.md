# SOP: Water - the production world

Derived from `docs/sweetwater-ranch.md`. Nothing else in this repo may source these rules,
and no rule here may be invented at the prompt: an agent that cites `WATER-04` when there
is no `WATER-04` is worse than one that says it has no rule to cite.

**The standing order this file serves:** keep ~1,000 head watered across 34,000 deeded and
leased acres with a small year-round crew and brutal driving distances. Nobody dies of
thirst. Everything below is downstream of that sentence.

**What makes water different from every other sensing world here.** A dry tank is not a
degradation, it is a countdown. Cattle in this country drink 15 to 20 gallons a head a day
and closer to double that over 90 degrees. There is no reserve behind a stock tank: when it
is empty the animals on it have nothing, and the nearest fix is a human in a truck who may
be an hour out. That is why water escalates on a shorter clock than anything else on the
ranch, and why the crew would rather be sent to a full tank by mistake than not sent to an
empty one.

---

## WATER-01 - A tank at or below the critical line is a haul today, not a work order for the morning

**Applies to:** `water_low`, severity `critical`.

The tank is effectively dry. Assume the cattle on it are already out of water rather than
about to be.

1. **Dispatch, do not investigate.** Send water to the location on this shift. Confirming
   the reading first costs the same drive as fixing it.
2. **Name the pasture and the head count in the work order.** The crew prioritizes by how
   many animals are behind the problem, and that number is what makes this tank the first
   stop instead of the third.
3. **Check the well or windmill that feeds it before you leave the yard.** A tank that fell
   to nothing is usually a supply failure, not consumption. If the site is solar-fed, the
   battery is the first suspect, because a dead pump and a dead telemetry radio arrive
   together and a blind tank reads exactly like a full one.
4. **Do not close this out on a single good reading.** The tank has to hold, not just fill.

**Escalate to the general manager** when two or more tanks are critical in the same tick, or
when the same tank goes critical again after a haul. Both mean the supply, not the tank.

## WATER-02 - A tank at the warning line is today's route, not tomorrow's

**Applies to:** `water_low`, severity `warning`.

The tank is drawing down faster than it is filling. It has hours, not days, and heat takes
those hours away.

1. Put it on today's water route. It does not need its own trip; it does need to be seen
   before dark.
2. **Read it against the location's other sensors before deciding it is normal.** A falling
   tank next to a hot air temperature is a different problem than a falling tank on a cool
   morning, and the second one is a leak.
3. If the same tank has appeared on this list more than a couple of ticks running, stop
   treating it as consumption and start treating it as a leak or a failed float.

## WATER-03 - Freeze is a water problem before it is a weather problem

**Applies to:** `freeze_risk` at any severity, on a `temperature` sensor.

Cold does not kill cattle here. Cold cuts them off from water, and thirst does.

1. **Critical (hard freeze):** tanks at this location go solid. Check tank heaters and
   floats at every water site in the pasture, not just the one that alarmed, and plan to
   chop ice if the heaters are not holding.
2. **Warning:** open water starts skinning over. Confirm heaters are drawing and that the
   propane or battery behind them has a reserve.
3. **A freeze reading and a low tank at the same location is the ranch's worst pairing.**
   A tank that is both low and freezing has less water and less of it reachable. Treat it as
   WATER-01 regardless of what the level band said on its own.

## WATER-04 - Heat doubles demand and every low tank behind it

**Applies to:** `heat_stress` at any severity, on a `temperature` sensor.

1. **Critical:** water demand roughly doubles. Any tank already at a warning level will not
   keep up through the afternoon, so pull those forward to today rather than waiting for
   them to cross the critical line on their own.
2. **Warning:** consumption climbs above the tank refill rate. Worth checking that the well
   feeding the pasture is running, not just that the tank is currently full.
3. Shade and wind matter to the animal; water is what the ranch controls. Keep the work
   order about water.

## WATER-05 - A sensor you cannot believe is a maintenance job, and the pasture is still unwatched

**Applies to:** a water site whose sensor is `offline`, `degraded`, or reading outside
physical possibility.

1. The sensor fault itself belongs to `infrastructure`. Do not write it as a water order.
2. **But the pasture behind it is now unmonitored, and that is a water fact.** Say so
   plainly and put physical eyes on the tank on the next pass through. A silent sensor and a
   full tank are indistinguishable from here, and only one of them is survivable.

## WATER-06 - Stock water is not the compliance gauge, and the two do not substitute

**Applies to:** `stream_flow_low`, and to any temptation to read a creek gauge as stock water.

Riparian flow is what the habitat and conservation payments are scored on, and it is
`compliance`'s to work. A thin creek is not permission to leave a tank empty, and a full
tank is not evidence the reach is flowing. Keep them in separate work orders.

---

## The two things never to write in a water work order

1. **An all-clear.** Code already decided this location has a problem before any of this was
   assembled. "Nothing appears to be wrong here" is a contradiction, not a finding, and on
   water it is the specific failure that gets animals killed.
2. **A number nobody measured.** Quote the reading in the packet, name the sensor that
   produced it, and say the rest is unknown. Estimated gallons, invented head counts, and
   inferred fill rates all read as authority and carry none.
