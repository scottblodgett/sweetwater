# Architecture

## What this repo is, and what it is not

**Is:** one orchestrator running continuously, driving five sub-agents against a live
ranch.

**Is not:** the ranch. The four REST APIs and the MCP server are already deployed on
Lambda, live in `scott-jasper/mcp-farm`, and are **frozen and read-only** from here. 19
flat-named tools plus the `ranch://sensors/map` resource are the entire surface. This
repo adds nothing to them.

That boundary is the most important fact in the design. The ranch is an independent
external product; this is a client of it that happens to be smart.

## The shape

```
                    main.py
                       │
        ┌──────────────▼─────────────────────────────────────────┐
        │  executor.py    CONTINUOUS TICK LOOP                    │
        │                                                         │
        │  every tick:   chaos maybe-fires ──┐                     │
        │                sweep ~160 sensors  │  free, 0 tokens     │
        │                triage (code)       │  free, 0 tokens     │
        │                reconcile sw_ops    │  free, 0 tokens     │
        │                route NEW incidents │  free, 0 tokens     │
        │                fan out ────────────┼──┐   <- only spend  │
        │                synthesize          │  │                  │
        │                gate on writes      │  │                  │
        └────────────────────────────────────┼──┼──────────────────┘
                                             │  │ asyncio.gather (bounded)
   ┌──────────┬───────────┬──────────────┬───┘  │
 chaos     water_feed  herd_health  infrastructure  compliance
   │          └───────────┴──────────────┴───────────┘
   │                          │ MCP Streamable HTTP
   │              ┌───────────▼─────────────┐
   │              │  DEPLOYED MCP SERVER    │  Lambda Function URL
   │              │  19 tools + ranch://map │  (frozen, unchanged)
   │              └───────────┬─────────────┘
   │                 Farm / Feed / Sensor / Care APIs
   │                          ▲
   └──────────────────────────┘  chaos writes real animal events here;
                                 sensor faults go to the sw_ops overlay
```

## Four of seven stages cost nothing

160 sensors are far too many to hand a model every tick, and a model asked to *find* the
problem burns its budget navigating instead. So the free pass narrows the ranch to what
is actually wrong, and **only newly-opened incidents ever reach an LLM.**

- **`GET /sensors?limit=500` once** for the catalog, or better, the `ranch://sensors/map`
  resource. That endpoint's query schema is pagination only (no `type` filter), so
  filtering happens in Python. The map-not-paging pattern arrives here by necessity, and
  is the right shape anyway: an agent paging a collection cannot distinguish an empty page
  from the end of the list.
- **Live reads are one GET per sensor**, because `GET /sensors/:id` synthesizes a fresh
  value on every call. ~160 requests per tick, bounded by `SWEEP_CONCURRENCY`. Unbounded
  at 160 is a wall of Lambda cold starts and reads to the other side as a load test.
- **`triage.py` owns severity**, in code, from per-type thresholds, with a warning tier
  between nominal and critical and a `warn_once` on any unrecognized type.
- **`memory.py` reconciles into `sw_ops`**, keyed on sensor plus category, so a persisting
  fault is `ongoing` and never re-alarmed.

## The five agents and their tool slices

Flat tool names mean each allowlist is an explicit literal set, enforced in code and
counted by a test. `create_react_agent` receives a **filtered** list.

| Agent | Tools | Owns |
| --- | --- | --- |
| `water_feed` | `list_sensors` `read_sensor` `get_sensor_readings` `list_feed_products` `list_inventory` `consume_feed`* `restock_feed`* | nobody dies of thirst or hunger; reserves before a storm |
| `herd_health` | `list_animals` `get_animal` `list_observations` `create_observation`* `list_care_tasks` `update_care_task`* | sick, down, dead, or missing animals; the care write path |
| `infrastructure` | `list_sensors` `read_sensor` `get_sensor_readings` `list_pastures` `list_shelters` | containment, power, fuel, no spill no fine |
| `compliance` | `list_sensors` `read_sensor` `get_sensor_readings` `list_pastures` `list_animals` | AUM stocking, habitat, keep the payments |
| `chaos` | **empty at M3**, by construction. Its surface is the Care API and the four placement writes, and it arrives with it at M5 | breaks the ranch on purpose |

`*` = write. Never called by a model. From M6 a responder may **propose** one in its work order
(`proposed_write`), the proposal pauses in the gate, and a human performs it or refuses it.

**There are eight write tools on the deployed surface, not four.** The four marked above are the
ones inside a responder's slice. The other four - `assign_to_pasture`, `remove_from_pasture`,
`assign_to_shelter`, `remove_from_shelter` - move animals and sit in `UNASSIGNED_TOOLS`. They
are in `WRITE_TOOLS` from M3 regardless, so `assert_callable` already refuses them.

**M5 landed and left all four unassigned, which is a correction to what this file predicted.**
Chaos was expected to claim them; it does not, and should not. Chaos writes animal events
**direct over REST** (`PATCH /animals/:animalId` on the Farm API, `POST /animals/:animalId/observations`
on the Care API), because it is a test harness rather than a responder: it needs no judgment,
no tool loop, and no model. Its slice is asserted at **zero tools** by M3's own count rail.
Routing it through the MCP write tools would put it behind the M6 gate for no benefit and would
make the one component whose job is to break things the hardest one to run. `CHAOS_ALLOW_WRITES`
is its gate, and `CHAOS_ANIMAL_COHORT` is its blast radius.

**How the gate works (M6).** A model still drives no tool loop; it judges one assembled packet
in one call. What M6 added is one optional field on the work order, `proposed_write`, naming a
write tool from the agent's slice with its arguments, and three code checks on it (shape, tool,
grounding of every id and quantity against the page). A proposal that survives pauses in a
LangGraph `interrupt()` checkpointed in `sw_ops`, one tiny graph per proposal, so the pause
outlives the process and the tick does not wait for it. A human resumes it from
`python -m src.agent.gate` with approve or reject, the write is performed through
`mcp_client.call_tool` carrying an `Approval`, and both halves leave an `audit.jsonl` line
correlated by `audit_id`. Three boundaries remain, and they are not redundant:

- `tools_for(agent)` is the **declaration**, writes included, and it is what the count tests
  assert against.
- `bound_tools_for(agent)` / `proposable_tools_for(agent)` are what a model may be handed and may
  propose: empty of writes while `GATE_LANDED` is False.
- `assert_callable(tool, approval=...)` is the belt behind both, raising `WriteGateError` from
  inside `mcp_client.call_tool` for any write without a human's `Approval`. The filtered lists only
  protect the paths that remember to use them; a helper written in a hurry is a path that might not.

**`GATE_LANDED` was flipped by M6 on 2026-09-11 and by nothing else.** It made writes proposable,
not callable. Detail: `src/agent/CLAUDE.md` (the gate) and `src/tools/CLAUDE.md` (the allowlists).

**The line worth defending: `herd_health` cannot read a sensor, and nothing but
`water_feed` can touch feed.** That is what makes the supervisor real rather than
decorative. Water-feed reports the Alkali Flat tank dry. Herd-health reports three cows in
that pasture with no observation in eight days. Neither can see the other's evidence, and
the supervisor is the only thing that can fuse them into one work order.

Being honest about the seam: three agents legitimately share the sensor read tools. The
isolation that matters is the **brief**, the **SOP set**, and **which sensor types each
agent is pointed at**, not tool-name exclusivity. Carving that further would mean editing
a frozen server.

## Where state lives

| Store | Holds | Owned by |
| --- | --- | --- |
| `sw_ops` schema (Supabase) | incidents, chaos events, shift reports, the LangGraph checkpointer | this repo |
| `farm` / `feed` / `animal_care` schemas | the ranch | the other repo. **Never touched from here.** |
| Sensor readings | nowhere. Synthesized per call. | nobody |

The only path to ranch data is HTTP through the deployed APIs. Cross-service IDs are
plain strings, never foreign keys, and orphans are allowed on purpose.

## Milestones

M0 skeleton + logging + handshake · M1 the free pass · M2 one agent, Opus only · M3 the
four responders · M4 the continuous loop · M5 chaos · M6 gate and validation · M7 model
routing · M8 the read API · M9 the window · M10 dockerize and close the docs.

Progress and divergence: `docs/JOURNEY.md`.
