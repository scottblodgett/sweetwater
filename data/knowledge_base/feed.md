# SOP: Feed and winter access - the production world

Derived from `docs/sweetwater-ranch.md`. Same rule as `water.md`: these rules are the only
ones that may be cited, and a rule that is not written here does not exist.

**The standing order:** reserves before a storm. Feed is the half of `water_feed` that runs
on a longer clock than water, which is exactly why it is the half that gets discovered late.
A bin nobody looked at in October is a phone call in January.

Irrigated hay off the meadows is one of the ranch's revenue lines as well as its feed
supply, so what is in the bins is inventory with a sale price, not just forage.

---

## FEED-01 - A bin at the critical line means a feeding gets missed

**Applies to:** `feed_low`, severity `critical`.

1. **Fill it today.** The cattle on this bin miss a feeding otherwise, and a missed feeding
   in cold weather shows up as condition loss that takes months to put back on.
2. Name the location and the head count it serves. Which bin gets the loader first is
   decided by that number.
3. **Check the bulk fuel behind the fill.** The loader and the feed truck both draw on the
   yard tank, and a fuel delivery out here is not a same-day thing. If fuel is also low,
   say so in this work order rather than letting it arrive as a separate surprise.

## FEED-02 - A bin at the warning line is a delivery to schedule, not a fire

**Applies to:** `feed_low`, severity `warning`.

1. The bin is into its reserve. Schedule the fill, and schedule it against the forecast
   rather than against the calendar.
2. **Weather is what turns this from routine into urgent.** A bin at reserve with snow or
   hard cold coming is a today problem. The same bin under a clear week is a Thursday
   problem. Say which one you think it is and why.

## FEED-03 - Deep snow removes feed access and truck access at the same time

**Applies to:** `deep_snow`, any severity, on a `snow-depth` sensor.

1. **Warning:** cattle stop pawing through to grass at this depth and start living entirely
   on what is hauled to them. Consumption steps up immediately, so the bins that looked fine
   yesterday are now on a shorter clock.
2. **Critical:** vehicle access to the far pastures is gone along with the feed access.
   Check reserves at the far bins first, because those are the ones that cannot be topped up
   once the two-tracks drift shut.
3. **Do not write this as a snow report.** The finding is about reserves and routes. Depth
   is the reason, not the work order.

## FEED-04 - Feed and water are one agent on purpose

An animal short of water will not eat, and an animal short of feed drinks differently. If
the packet shows a feed problem and a water problem at the same location, write one work
order that names both and says which one the crew fixes first. Two separate orders for the
same pasture is how the second one gets ignored.

---

## The two things never to write in a feed work order

1. **An all-clear.** Code flagged this bin before the packet existed.
2. **A number nobody measured.** Days of feed remaining, herd consumption rates, and
   delivery lead times are not in the packet. Quote the weight that is, and say the rest is
   unknown.
