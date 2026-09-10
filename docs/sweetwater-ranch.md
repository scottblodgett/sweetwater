# Sweetwater Land & Cattle Co., LLC — the scenario canon

This is the single source of truth for the ranch the whole exercise now models.
Everything downstream — the sensor service, the agent-lab goals, and the public
demo site — points here. When a value needs to feel real, it should be true to
this ranch.

---

## The ranch

**Sweetwater Land & Cattle Co., LLC** — a fourth-generation family ranch near the
Wind River foothills, operating across **~34,000 deeded + leased acres** and running
**~1,000 mother cows** plus a band of sheep.

Founded in **1911 by the Hageman family**; incorporated as an **LLC in the 1990s**
for succession and liability. Day-to-day operations are run by a **general manager**
and a small year-round crew, supplemented by seasonal hands for branding, haying, and
shipping. Still family-owned at the top; run like a business underneath — that tension
(family legacy vs. spreadsheet) is the ranch's character.

## Revenue (diversified, but anchored in cattle)

- **Cow-calf production** — the core.
- **Irrigated hay** — alfalfa/grass off the meadows.
- **Guided elk & antelope hunts** — outfitting on the deeded ground.
- **State & federal grazing leases** — AUM stocking obligations to prove.
- **Mineral royalties** — a small number of oil & gas wells on the property.
- **Pipeline & utility easements** — rights-of-way crossing the land.
- **Conservation / habitat programs** — payments tied to grazing, water
  infrastructure, and wildlife migration.
- **Wind developer option** — several thousand acres of higher-elevation ground
  under option; no project built yet (a met tower is collecting resource data).

## Why it's instrumented

Labor is scarce, operating distances are brutal, and management increasingly depends
on **knowing what's happening across a very large property without driving every
pasture.** Remote water and weather telemetry, EID livestock tags, and other
monitoring systems are how the ranch stays ahead of loss.

---

## The four sensing worlds

The revenue mix means the ranch instruments four distinct domains, not one — which is
what makes cross-domain triage believable:

| World | What it watches | Representative sensors |
|---|---|---|
| **Production** | water, feed, animals — "nobody dies of thirst or hunger" | stock-tank level + freeze, well/pump flow, feed-bin & hay weight, EID tags, chute scale, activity/water-consumption |
| **Safety & regulatory** | wells, pipelines — "no spill, no fault, no fine" | wellhead pressure, tank-battery level, H₂S/gas, flowline pressure, pipeline right-of-way / cathodic protection |
| **Environmental / compliance** | habitat, range, water — "keep the payments, prove the stewardship" | wildlife-migration cameras, stream/riparian flow, soil moisture, range/vegetation health |
| **Development** | wind resource — "prove the site" | met tower: hub-height wind speed/direction, air density, temperature |

Plus the cross-cutting basics: **weather station(s)** (temp, humidity, wind, precip,
snow depth), **electric-fence energizer voltage** (fence-fault across miles of wire),
**gates & doors**, **bulk fuel** (diesel + propane), and **off-grid power** (solar
output, battery charge, generator status).

---

## The agent's mission (the real "goal")

Not "report every animal." The operating goal an agent serves on this ranch:

> **Keep ~1,000 head watered, fed, healthy, and accounted for across 34,000 acres —
> surface anything that threatens that (a dry or freezing tank, a sick or missing
> animal, a low feed or fuel reserve, a downed fence, a predator hit, a well or
> pipeline fault, a compliance gap) before it becomes a loss, and open the right work
> order for whoever's on shift.**

Every sensor and every service maps to a reason: water → *nobody dies of thirst*;
EID/location → *accounted for*; feed/fuel → *reserves before a storm*; fence/predator
→ *containment and loss*; wells/pipelines → *no spill, no fine*; habitat/range →
*keep the conservation payments*.

---

## Destination: the public demo site

The end state is a **public demo website** showing Sweetwater off to the world — the
ranch alive with hundreds of plausibly-flickering sensors and an agent triaging a
real-feeling operation. Two consequences that shape the build:

- **Sensors must be synthetic and DB-free.** A live demo can't depend on seeded rows,
  and stale or repeating values look dead. Reads are fresh, unpredictable, and true to
  the ranges this ranch would show.
- **The scenario has to read as authentic**, not maxed-out. Lean on the bread-and-butter
  (water, weather, fence, EID, feed, fuel); treat virtual-fence collars, calving tags,
  and the wind met tower as "this ranch is ahead of the curve" flourishes — present,
  but fewer.

## What's implemented today (sensor layer)

The Sensor API now generates **~160 sensors across 13 types**
(`apps/sensor-api/src/sensors/registry.ts`), weighted per the guidance above —
generous on the mundane, sparse on the exotic:

- **Production + basics (the bulk):** `water-level`, `temperature`, `humidity`,
  `feed-bin-weight`, `soil-moisture`, `gate`, `wind-speed`, `snow-depth`,
  `fence-voltage` (one per big allotment), `fuel-level` (diesel + propane),
  `battery-charge` (remote solar-fed tank sites).
- **Flourishes (1–2 each):** `wellhead-pressure` (safety/regulatory — the few wells),
  `stream-flow` (environmental — riparian gauges), and a single wind **met tower**
  (development).
- **Still aspirational** (not yet minted): H₂S/gas, cathodic protection, wildlife
  cameras, EID tags, chute scale.

Every sensor also carries a `locationName` and stylized-map `coordinates` (an {x,y}
point on a fictional 0–100 grid, from an in-code location catalog) so the demo map can
render straight off the API. **Guaranteed-flag troublemakers** for the agent to find:
`alkali-flat-water` + `red-canyon-water-2` (low tanks), `coyote-draw-gate` (offline),
`windmill-pasture-water` (degraded), `south-windbreak-temp` (bad reading),
`east-allotment-fence` (downed energizer), `home-place-diesel` (low fuel).
