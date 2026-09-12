# M10 transcript: the loop that fetched nothing, and the report that stood

Pinned from `logs/m10-a` and `logs/m10-b` on 2026-09-12, the two live runs behind the M10 rows in `docs/model-routing.md`. Test ledger, chaos seed 1, `TIER1_ENABLED=1`; run A with `TIER_COMPARE=1`, run B without and after the `linked` enum. T1 is `gemma4:e4b` on Ollama; T2 is `us.anthropic.claude-opus-5` on Bedrock. The per-run directories are gitignored; this file is what a reader needs to check the rows without them. Extracted by hand from the transcripts (`LOG_TRANSCRIPTS=1`), kept verbatim where quoted.

## The two runs, one line per tick

```
run A  tick  opened  local  escalated  reasons                                            loops  outcomes                    report        cost_usd (line)
       1     0       0      0                                                             0                                  code          0.00
       2     13      7      7          insufficient x5, proposed_write, report:rejected   5      answered 2, no_tool_calls 3  T2 rewrite    0.48
       3     6       4      3          insufficient x2, report:rejected                   3      answered 1, error 1, ntc 1   T2 rewrite    0.19
       4     1       1      0                                                             0                                  code          0.00
       5     1       1      0                                                             0                                  code          0.00
       6     4       2      3          insufficient x2, report:rejected                   2      no_tool_calls 2              T2 rewrite    0.16
       7     0       0      0                                                             0                                  code          0.00
       8     2       0      2          insufficient x2                                    2      no_tool_calls 2              code          0.11
       9     4       2      3          insufficient x2, report:rejected                   3      answered 2, no_tool_calls 1  T2 rewrite    0.17
       lines $1.11; shadows in compare.jsonl $1.01; agent.jsonl total $2.12. Ticks 6 to 9 are the runaway loop (cookbook #49)

run B  1     0       0      0                                                             0                                  code          0.00
       2     16      7      9          insufficient x9                                    9      answered 1, no_tool_calls 8  T1, clean     0.53
       3     4       0      4          insufficient x4                                    4      answered 1, no_tool_calls 3  T1, clean     0.27
       4     1       1      0                                                             0                                  code          0.00
       5     1       1      0                                                             0                                  code          0.00
       agent.jsonl total $0.80, no shadows
```

## One investigation, in full: `coyote-draw-gate:sensor_offline`, run A tick 2

The judge's `unknowns`: `Duration the coyote-draw-gate sensor has been dark.` Tools offered (the `infrastructure` read slice, all 19 loaded by the adapter and 14 withheld): `get_sensor_readings`, `list_pastures`, `list_sensors`, `list_shelters`, `read_sensor`.

```
turn 1  stop  tool_calls=1  in/out 3200/9
  call list_sensors {}                        -> 36,449 chars in 490 ms, cut to 4,000 by the interceptor
turn 2  stop  tool_calls=1  in/out 5039/15
  call list_sensors {"limit": 500}            -> 58,145 chars in 525 ms, cut to 4,000
turn 3  stop  tool_calls=1  in/out 6884/21
  call read_sensor {"sensorId": "coyote-draw-gate"}          -> 368 chars (the incident's own sensor, which the brief said not to read)
turn 4  stop  tool_calls=1  in/out 7091/24
  call get_sensor_readings {"sensorId": "coyote-draw-gate"}  -> {"data": [], "meta": {"count": 0, ...}}  (an offline sensor has no series)
turn 5  stop  tool_calls=0  in/out 7168/420
  closing: "Duration the coyote-draw-gate sensor has been dark: not available"
outcome answered, 4 steps, 38 s, 29,382 tokens in; facts block 9,122 chars; re-judged at Tier 1: insufficient_information still true; escalated to Opus on the enriched page
```

The loop did what it could and reported honestly. The fact it was sent for is not on any endpoint: the readings series for an offline sensor is empty. That is the shape of most of the 28 loops (cookbook #51).

## A loop that never called a tool: `cow-0777:care_overdue`, run A tick 2

One turn, `finish_reason=stop`, `tool_calls=0`, 59 tokens out, the whole text:

```
get_animal{animalId:<|"|>cow-0777<|"|>} list_observations{animalId:<|"|>cow-0777<|"|>} list_care_tasks{animalId:<|"|>cow-0777<|"|>,status:<|"|>pending<|"|>}
```

Three sensible calls, written as text with `<|"|>` where quotes belong, which Ollama did not parse into `tool_calls`. Outcome `no_tool_calls`, nothing appended, escalated as before M10. 19 of 28 loops ended this way. `east-allotment-fence:fence_down` did the same with a different spelling: `read_sensor(sensorId='east-allotment-fence') get_sensor_readings(sensorId='east-allotment-fence') read_sensor(sensorId='east-allotment-battery')`.

## The local supervisor before and after the `linked` enum

**Run A, tick 2, T1, rejected on `invented_incident`.** `linked` as it came back, one of three items:

```
"Home Place is experiencing compounding risk: the *home-place-temp:freeze_risk* necessitates immediate water action, and the water action requires checking the feed source, which feeds into the general infrastructure status."
```

Four of four local reports in run A did this and the rail caught every one; Opus rewrote every one.

**Run B, tick 3, T1, rail-clean, 4 orders on the page, 3,292 tokens in, 702 out.** Verbatim:

```
H: FOREMAN'S SITREP: TODAY'S FOCUS IS ON FRAGILITY AND LOSS OF CONTAINMENT. WATER IS THE #1 PRIORITY. WIND IS A THREAT TO EVERYTHING. FUEL IS A BIG UNCERTAINTY.
S: The immediate, high-priority concern is the potential for both Home Place and North Meadow to lose access to potable water due to simultaneous freezes and critically low tank levels. The underlying issue at both sites appears to be a failure in the supply side, given the low tank readings at multiple points.
P:
 - Water and Feed: Get crew to Home Place immediately. The combination of the 4.6 gal tank with the near-freezing temperature reading, coupled with the questionable propane/diesel reserves (home-place-propane reads 8.5%, home-place-diesel at 4.6%), requires immediate fuel verification and water hauling for the 2 head. Also, flag the implausible temperature sensor reading (home-place-temp) to Infrastructure, as this makes the freeze risk unmonitored until repaired. Check the Home Place well/supply line source while you are there.
 - Water and Feed: Next stop is North Meadow. Two tanks are critically low (north-meadow-water at 16.4 gal, trough-01 at 15.7 gal) while the temperature is below freezing (-17.3 F). Like Home Place, this suggests a supply issue. Check the feeding well/supply for North Meadow first. Treat the North Meadow temperature sensor (north-meadow-temp) as unreliable because of its extreme swings, meaning a physical check on both tanks is mandatory on the next pass.
 - Infrastructure: The 71 mph wind at met-tower-wind is an active threat. Assume all fence faults arriving in the next few hours are due to this wind; ride the fence lines downwind of the tower first to mitigate physical damage. Check for loose roofing and panels at the Home Place and remote sites while passing through.
 - Infrastructure: On the way, check the Federal Well #2. It's showing hard swings (149.2 psi to 318 psi) and is below its 200 psi warning line. Do not send a crew; instead, call the operator of record immediately. Inform them about the unstable readings and ask them to investigate loss of containment on the flowline. While calling, note what the ground looks like along the flowline right-of-way downhill.
linked: ['home-place-temp:freeze_risk', 'north-meadow-temp:freeze_risk', 'met-tower-wind:high_wind', 'federal-well-2-pressure:wellhead_underpressure']
esc: ['GM needs to know: The combined, high-risk profile of Home Place (empty tank, low fuel, unstable temp sensor) and North Meadow (two empty tanks, low fuel, unstable temp sensor) suggests a systemic failure in the water supply lines, not just the local tanks.', 'The Met Tower wind is a high certainty event causing imminent physical damage, which elevates the need for immediate preventative fence checks.']
```

What a rail cannot see, on this page: the headline is a shout in capitals rather than the one line the schema asks for, and "16.4 gal" and "15.7 gal" are called critically low where the page's own line for a tank is 2 gal. What it got right: every key in `linked` is on the page, every number is on the page (`unverified_number` did not fire), the wind is read against the fences, and the well goes to the operator of record rather than the crew. The run B tick 2 report, on 16 orders, had a paragraph for a headline, bold labels on its priorities, and called a temperature incident a tank level; both keys it linked were real and at one location.

## The #12 replay, `feed-bin-03`, $0

Same page three times with the conditions block stripped and three times with it, Tier 1 only (`logs/m10_measure_feed.json`).

| | before | after |
| --- | --- | --- |
| `insufficient_information` | 2 of 3 | 0 of 3 |
| weather or fuel reasoned about in the prose | 2 of 3 | 3 of 3 |
| unknowns named | head count; "need weather data"; fuel for the loader | head count |
| input tokens | 2,993 | 3,158 |

The block as rendered: `wind speed (met-tower-wind, at Wind Met Tower): 38.6 mph`, `air temperature (alkali-flat-temp, at Alkali Flat): 59.6 F`, `snow depth (weather-station-snow, at Weather Station): 3.5 in`, `bulk fuel level (home-place-diesel, at Home Place): 4.2%`. The lowest bin that morning read 36.5 lbs, which is critical, and the script pinned the incident at warning so FEED-02's forecast question applied; the local model said so in one answer ("I must echo the given severity of WARNING"), which is the echo rule working.
