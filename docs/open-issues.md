# Open issues

Everything still open on the Sweetwater agentic layer, written for a person who was not in the room.
Refreshed 2026-09-11 in the post-M9 review, replacing `docs/issues.md`. Nothing here is a failing test.
These are decisions nobody has made, checks we said we would run and have not, small fixes that are
ready, things deferred on purpose, and quirks of the ranch we cannot change from here.

Each issue has four parts: what is wrong, why it matters, what it would take, and who decides.
"Scott" means it needs his yes, usually because it touches the shared database or costs money. "A build
session" means the work is scoped and anyone picking up the repo can do it. "Nobody yet" means there is a
fork in the road and no one has chosen.

Numbers are stable. `#12` here is the same `#12` that `docs/JOURNEY.md` and `docs/cookbook.md` refer to.
New items in this review start at #24.

---

## Decisions waiting on Scott

### 5. Where the loop and the read API run

**What has not been done.** The monitoring loop and the read API both run on Scott's laptop. No host has
been chosen for them.

**Why it matters.** Until they run somewhere always-on, the ranch is only watched while the laptop is
awake, and the web window (#23) cannot be published because Vercel cannot reach a laptop. This one
decision blocks two others (#23 and FUTURE-1 below).

**What it would take.** A small always-on box is the honest answer for "constantly running" and matches
how the loop is written: one long-lived process with backoff and a spend ceiling that halts. A Lambda
container on a schedule would be cheaper when calm but would need the loop restructured first. Recommend
the box. A day to set up, plus a real API key (#6). No database change.

**Who decides.** Scott.

### 6. A real Anthropic API key

**What has not been done.** `ANTHROPIC_API_KEY` in `.env` is empty. The loop bills Opus through AWS
Bedrock using this machine's temporary session credentials, which expire after hours.

**Why it matters.** A run meant to last days will lose its credential mid-run. The loop survives that
(the affected incidents are held and retried), but it stops writing work orders until someone logs in
again, so an unattended run is not really unattended.

**What it would take.** One value set in `.env` on whatever host #5 picks. No code, no database. It does
mean the bill lands on the first-party Anthropic account rather than AWS.

**Who decides.** Scott.

### 7. Chaos is still armed on this machine

**What is wrong.** `.env` on this laptop has `CHAOS_ENABLED=1`, the switch that lets the fault
simulator run. Confirmed still at 1 in this review. Every doc says it belongs at 0 unless a demo is being
driven.

**Why it matters.** With chaos armed, every sensor reading is suspect by design. That is right for a
demo and ruins any cost or accuracy measurement taken with it on. The tests protect themselves, and every
measurement so far set it to 0 explicitly, but the trap is one forgotten flag away.

**What it would take.** Change one line in `.env`. Zero cost.

**Who decides.** Scott, since it is his machine and his demo setting.

### 8. Whether a single tick should skip the debounce

**What is wrong.** Since the debounce landed, an incident opens only when a sensor reads bad on two
consecutive sweeps. So `python main.py --once` against a fresh ledger opens nothing, because everything it
sees is on its first sighting. That is correct for the loop and surprising at a keyboard.

**Why it matters.** Someone running one tick to see the system work sees "opened 0" and thinks it is
broken. The knob exists (`INCIDENT_CONFIRM_SWEEPS=1` opens on first sight), but nothing tells the person
at the keyboard about it.

**What it would take.** A decision, then a sentence in the README. If `--once` should default to first
sight, that is a few lines in `main.py`. No database change.

**Who decides.** Scott. Recommend keeping the debounce and adding the sentence, so `--once` and the loop
behave the same way.

### 21. The ranch map has no data source. Needs a yes on migration `0008`

**What is wrong.** The web window has a map panel that is a placeholder saying why it is empty. Sensor
coordinates live in the ranch's own catalog, and the rule that keeps the window cheap and safe is that only
the orchestrator talks to the ranch. The read API never does, and neither does a browser. So the window
has nothing to draw from.

**Why it matters.** A map is the panel a person on shift would look at first. Without it the window is a
feed and some counts.

**What it would take.** Three small pieces and one yes. A new table `sw_ops.catalog_snapshots` in the
shared Supabase database (the DDL is written below). The loop writes one row when the catalog changes,
which on a ranch that does not change is one row per process lifetime. A seventh API route,
`GET /ops/catalog`, serving the latest snapshot. Then the window draws from it. About a day, plus the yes.
The migration is the part that needs Scott, because every change to the shared database does.

```sql
CREATE TABLE sw_ops.catalog_snapshots (
    id            BIGSERIAL PRIMARY KEY,
    run_id        TEXT        NOT NULL,
    tick          INTEGER     NOT NULL,
    at            TIMESTAMPTZ NOT NULL,
    source        TEXT        NOT NULL,          -- 'mcp_resource' or 'sensor_api', the same catalog_source the tick line carries
    sensor_count  INTEGER     NOT NULL,
    digest        TEXT        NOT NULL,          -- sha256 of the canonical JSON, so an unchanged catalog writes no row
    sensors       JSONB       NOT NULL           -- [{sensor_id, sensor_type, location, unit, lat, lon}], the catalog as read
);
CREATE UNIQUE INDEX uq_catalog_snapshots_digest ON sw_ops.catalog_snapshots (digest);
CREATE INDEX ix_catalog_snapshots_at ON sw_ops.catalog_snapshots (at DESC);
```

**Who decides.** Scott, for the migration. The field names for coordinates are confirmed on the wire when
the first snapshot is written, never copied from the other repo.

### 17. A dead cow loses her pasture, and the demo cannot put her back

**What is wrong.** When the chaos simulator marks a cow deceased, the Farm API clears her pasture. The
`chaos restore` command sets her status back to active but leaves her in no pasture. Her record then says
"pasture unrecorded," her herd-mates are unknown to the work order, and the deceased order had to ask the
manager where she was found.

**Why it matters.** Only for demos that run the coyote kill more than once on the same animals. After the
first kill the cohort is a little less realistic.

**What it would take.** The kill event records the pasture, and `restore` puts her back with one more
guarded write. Half a day in `chaos.py`. Touches the deployed Farm API, not the database. The chaos
injury note stays on her record forever because the Care API is append-only; the 24-hour window handles it.

**Who decides.** Scott, on whether the demo needs the cohort to survive a kill intact. Not urgent.

---

## Owed verifications

### 23. The web window has never been loaded from the internet

**What has not been done.** The plan's last check is "load the Vercel URL and watch three ticks land
without a refresh." The window is built and verified against a local dev server on this machine only. It
was not faked with a tunnel.

**Why it matters.** Until it runs on Vercel we cannot claim the window is deployable, only that it works
on a laptop.

**What it would take.** #5 first, so the API lives somewhere Vercel can reach. Then two environment
variables set in the Vercel project (server-side, never exposed to the browser) and one browser tab left
open for fifteen minutes. An hour once #5 lands. No database change. Vercel's free tier is enough.

**Who decides.** Waits on #5. Then a build session.

### 12. The feed page does not carry the weather, so the cheap model keeps saying "I do not know"

**What is wrong.** The feed standing orders say weather is what turns a low bin from routine into
urgent, and ask the model to say which. The evidence page for a feed incident carries the bins and nothing
about weather. The local model honestly answered "insufficient information" on five of six feed incidents
and escalated to Opus each time. Opus wrote around the gap. Both were right. The page is what is wrong.

**Why it matters.** This is the single change that would let the cheap local model earn its job. As
measured at M7 the cascade saved one call in twelve and was switched off. Until the page is fixed, every
work order goes to Opus and the measurement in `docs/model-routing.md` stays where it is.

**What it would take.** The ranch has wind, temperature, and snow-depth sensors and the sweep already
reads them. Put the current readings from the nearest of each under a "conditions now" heading on the
feed page. No extra network calls, no database change. Then re-run the M7 measurement, three ticks with
the cascade and comparison on, which costs about $1 in Opus shadows. A day.

**Who decides.** A build session. It is `evidence.py`'s change, not the model's.

### The cascade re-measure, after #12

**What has not been done.** `docs/model-routing.md` has one pending row: the per-incident work order was
measured on the local model and not adopted. The verdict names #12 as the change that would earn the
next attempt.

**Why it matters.** Without it, the ledger row that says "Opus writes every work order" is the final word,
and the local model that was built and tested sits switched off.

**What it would take.** #12, then the same three ticks with the same columns. About $1.

**Who decides.** A build session, after #12.

### 14. The Bedrock price is assumed, not read

**What is wrong.** The price table bills Opus on Bedrock at the same rate as the first-party API, because
the public Bedrock pricing page did not render an Opus 5 row when it was checked.

**Why it matters.** Every `cost_usd` on a tick line and the spend ceiling that halts the loop are only as
right as that one row. If Bedrock charges more, the ceiling lets the loop spend more than it says.

**What it would take.** Read the AWS bill for 2026-09-11 (about $0.75 of Opus that day was the M7
measurement) and correct one row in `routing.PRICE_TABLE` if it differs. An hour.

**Who decides.** Scott, since it is his AWS bill. The edit is a build session's.

---

## Small fixes, ready to pick up

### 22. A tool the ranch does not have is reported as an outage

**What is wrong.** When someone approves a write naming a tool the ranch does not know, the ranch says
"tool not found" and the receipt records it as a transport failure, the same code used when the ranch is
down. A missing tool is a refusal, not an outage. "Try again later" is the wrong reading when the honest
one is "never."

**Why it matters.** Anyone reading receipts, in the window or the table, would go looking for an outage
that did not happen.

**What it would take.** Check the tool name against the list of 19 deployed tools before opening a
connection, and record `refused_unknown_tool`. Ten lines in `gate.py` and one test. No database change.

**Who decides.** A build session. Left out of M9 on purpose because the Python gate was frozen for that
phase.

### 4. The chaos miss check forgets across restarts

**What is wrong.** The loop reports when a simulated fault heals without its sensor ever being read
(a fault born and gone between two sweeps). It only knows about faults it injected itself, in memory. A
fault armed by a previous run, or by hand at the command line, is never reported as missed.

**Why it matters.** Only for demos that span a restart. In those, a missed fault goes unreported.

**What it would take.** One column on `sw_ops.chaos_events` recording when the sweep last saw each fault,
written by the sweep and read at expiry. Small, but it is a migration on the shared database, so it needs
Scott's yes. A few hours.

**Who decides.** Scott for the migration, then a build session. Can wait.

### 20. The chaos simulator's audit receipts are not in the table

**What is wrong.** From M8, every write the agents propose and every human decision on it lands in the
`sw_ops.audit_receipts` table, and the log file is a copy. The chaos simulator's own writes (an animal
marked deceased, or a write it refused because the safety switch was off) still go only to the log file.

**Why it matters.** A reader of the table would believe it holds every receipt. It holds every receipt
the human gate wrote and none of the simulator's, so a refused simulator write is invisible there.

**What it would take.** Pass the database session the tick already has into the chaos writer and insert
the pair beside its two log lines. An afternoon. No migration, the table exists.

**Who decides.** A build session.

### 18. One rail is named for sensors and now grades cows too

**What is wrong.** A quality check on work orders called `sensor_not_named` fires when the prose never
names the sensor behind the number. Since the herd sweep it also runs on animal work orders, where it
checks the animal id. The check is right; the name is wrong for a cow.

**Why it matters.** Only clarity. The name shows up in logs and tick lines.

**What it would take.** Rename it to `subject_not_named` in one place and everywhere the docs and the
ledger say the old name. An hour. Kept until the window decides what it shows a human, so it is renamed
once.

**Who decides.** A build session.

### 13. Turning thinking on would fail against Opus 5

**What is wrong.** The code that would turn a model's reasoning effort up sends a parameter
(`budget_tokens`) that Opus 5 rejects. Nothing uses it: every call runs with thinking off, on purpose,
because code decides severity and the model only writes prose. So no call has ever hit this.

**Why it matters.** The first job that wants thinking on will fail with a 400 error until one function is
updated to the current API shape (`{"type": "adaptive"}` plus an effort setting).

**What it would take.** One function in `llm_client.py`, an hour, when a job first needs it. Not before.

**Who decides.** A build session, when the need arrives.

### 27. Two libraries disagree about their versions

**What is wrong.** Found in this review. Every `pytest` run prints a warning from LangGraph: the installed
`langgraph` and `langgraph-checkpoint-postgres` versions are marked incompatible and it asks for an
upgrade. All 433 tests pass regardless.

**Why it matters.** The checkpointer is what holds a paused write across a restart. A version mismatch
there is exactly the kind of thing that works until it does not. Also, the checkpointer's database tables
are created by our own migration from the library's list, so a library upgrade is an alembic revision
Scott has to see, never a silent change.

**What it would take.** Read both changelogs, upgrade the pins in `requirements.txt`, check whether the
library's table list changed (if it did, that is migration `0008` or `0009` and needs a yes), and re-run
the gate. Half a day.

**Who decides.** A build session, with Scott's yes if a migration falls out.

---

## Deferred on purpose

### 16. A serious note on a healthy cow with no open task is invisible

**What is wrong.** The herd sweep reads each animal's observations only for animals whose state changed:
a non-active status, a pending care task, or an incident already open. Observations can only be read one
animal at a time, and reading all 1,195 every five minutes would be 344,000 requests a day. So a "high"
injury note written against a cow whose status is still active, with no care task, opens nothing until
something else puts her in that set.

**Why it matters.** The coyote kill is caught through the status change. A "cow down, still marked
active" note is not.

**What it would take.** Either read a rotating slice of the herd each tick so every animal is checked
every N ticks, or ask the upstream to turn a high note into a care task, which is a conversation with the
frozen repo. Neither is version one.

**Who decides.** Nobody yet. Written down so it is a known limit, not a surprise.

### FUTURE-1. Putting it in containers

**What has not been done.** There is no Dockerfile. `docker-compose.yml` is a placeholder. Dockerizing
was milestone 10 and was renamed FUTURE-1 and deferred on 2026-09-11.

**Why it matters.** It does not, until #5 picks a host that wants a container. Everything the system
depends on is already deployed elsewhere or in Supabase, so there is nothing local to stand up.

**What it would take.** Two Dockerfiles (the loop and the API) and a compose file. A day. Only if the host
wants it.

**Who decides.** Falls out of #5.

### Multi-tenancy

**What has not been done.** "Stand up the next ranch in a morning" implies each client gets its own
isolated `sw_ops`. Nothing supports that today.

**Why it matters.** Only if there is a second ranch.

**What it would take.** A schema per tenant, or a tenant column on every table, and the API and window
taught which one they are looking at. Weeks, not days. Not version one.

**Who decides.** Nobody yet.

---

## Known artifacts we cannot fix from here

### 9. One orphan observation on the deployed Care API

An observation (`0328d7e2-d271-4410-907c-a84020c2c8c7`) exists against an animal that does not exist
(`zz-does-not-exist-0000`). It was created at M5 by a probe that expected a validation error and got a
success instead. Observations are append-only upstream and that repo is frozen, so it stays. It is
invisible to every real animal and to every query the agents make. Written down so it is a known artifact,
not a mystery row.

### 26. The ranch's dice, and three other quirks of the upstream

The deployed Sensor API invents a fresh reading on every call, unanchored to the last one, and its history
endpoint is invented separately from its latest-reading endpoint. That is why healthy sensors churn ten to
twenty findings a tick, why the debounce exists, and why the evidence page labels history as shape and
trend only. Three more, all on the Farm and Care APIs: the `status` filter on the animal list cannot find
a non-active animal; the animal list fails with HTTP 500 when more than six pages are requested at once;
and marking an animal deceased clears her pasture. Every one is worked around in code and recorded in
`docs/STATE.md`. None is a bug we can file, and every future measurement has to be read knowing them.

### 19. Care task titles carry em dashes

Three care tasks on the deployed Care API have titles like "Recheck pinkeye eye — remove patch." That is
the ranch's seed data, so a summary quoting the title carries the dash and so does the herd page. It is
data, not our prose. The house rule against em dashes applies to the standing orders and the briefs and
stays there.

---

## Done in this review, kept as the record

### 24. The docs were written incrementally and there were too many of them

**What was wrong.** About 6,950 lines across 19 markdown files, every one written at a phase boundary and
never read across. `Plan.md` and `architecture.md` both carried the diagram, the tier table, and the
milestone list. `STATE.md` was 544 lines and is the file every session is told to read in full before
doing anything, so its length was a per-session cost, not a one-time one. `JOURNEY.md` was 1,671 lines
and `cookbook.md` 1,199. Three transcript files sat in `docs/` beside the design docs, and an empty
`docs/decisions/` folder promised ADRs that were written into `STATE.md` instead.

**Why it mattered.** A new session spent its first ten minutes reading, and read the same fact in three
places, sometimes in three versions. When two copies disagree, a reader cannot tell which one the code
follows.

**What was done.** With Scott's go in this review, the set was consolidated to the target below. The
merge map records every file, where its content went, and the line count before and after. Nothing was
deleted without its content landing somewhere; git history holds every original.

| File | Before | Where the content went | After |
| --- | --- | --- | --- |
| `README.md` | 126 | kept: how to run it. Docs table rewritten for the new set | 127 |
| `CLAUDE.md` (root) | 215 | kept: what this is, the one rule, the ritual, the commands, the signposts. The seven explanatory paragraphs moved to the nested files and `STATE.md` where they already lived (#25) | 104 |
| `src/agent/CLAUDE.md` | 305 | kept, minus a stale paragraph that said escalation was not built and two history asides | 297 |
| `src/tools/CLAUDE.md` | 209 | kept | 209 |
| `src/models/CLAUDE.md` | 129 | kept | 129 |
| `src/api/CLAUDE.md` | 69 | kept | 69 |
| `tests/CLAUDE.md` | 172 | kept, transcript paths updated | 172 |
| `web/README.md` | 100 | kept | 100 |
| `docs/STATE.md` | 544 | kept: where the build stands, the module table, the 37 decisions, the environment, the live ranch facts, the demo recipe, and one list of traps. The seven "what Mn actually produces" histories went; what was durable in them became a knobs table, a cost paragraph, and one list of fourteen traps | 241 |
| `docs/Plan.md` | 455 | kept, and absorbed `architecture.md` and `logging.md` in place of its own older copies of the diagram, the tool table, and the log schemas. Its pre-M7 model-routing essay became a pointer to the ledger | 769 |
| `docs/architecture.md` | 154 | into `Plan.md`. Deleted | 0 |
| `docs/logging.md` | 362 | into `Plan.md`. Deleted | 0 |
| `docs/JOURNEY.md` | 1,671 | kept, trimmed where a defect was retold in full and the cookbook already holds it. Each such defect is now one line and a cookbook number (M0 to M5); M6 to M9 already were | 1,464 |
| `docs/cookbook.md` | 1,199 | kept. Three pairs that were the same lesson twice merged: #23 into #13, #44 into #32, #41 into #18. The numbers stay in the index and point at the merged entry. 45 entries, 48 numbers | 1,159 |
| `docs/model-routing.md` | 305 | kept as is, the ledger | 305 |
| `docs/issues.md` | 368 | this file. Deleted | 0 |
| `docs/open-issues.md` | 0 | new | 492 |
| `docs/sweetwater-ranch.md` | 115 | kept, the canon | 115 |
| `docs/transcripts/no-brief-transcript.md`, `docs/transcripts/with-brief-transcript.md`, `docs/transcripts/m7-compare-transcript.md` | 348 | moved to `docs/transcripts/`, unchanged | 348 |
| `docs/decisions/` | 0 | removed. The decisions are the numbered list in `STATE.md` | |
| **Total** | **6,946** | | **6,200** |

The "after" column is the real count at the merge commit. The total fell by about 750 lines rather than the
1,350 estimated, because `Plan.md` grew by 314 absorbing two files that only overlapped it in part. The cost that
mattered fell by more: the file every session reads in full went from 544 lines to 241.

**Who decided.** Scott, in this review.

### 25. The root `CLAUDE.md` was 215 lines against its own target of 80

**What was wrong.** `Plan.md`'s table says the root file carries what this is, the one rule, the
commands, and a signpost to each nested file, target under 80 lines. It was 215, and it got there the same
way the previous project's got to 644 before it was broken up: each phase added a paragraph about the
thing it built, and the paragraph was also written into the nested file and `STATE.md`.

**The sort.** Every paragraph in the root file and the three largest nested files, into three piles.

Root `CLAUDE.md`, 215 lines:

| Pile | Lines | What |
| --- | --- | --- |
| A session needs this before its first action | 68 | What this is (8). Starting a session and the four don'ts (17). The one rule (4). How milestones close (20). Two rules that decide most arguments (8). Conventions (11) |
| A pointer, can be one line | 44 | The upstream is frozen (12, the sharp form is in `STATE.md` and `src/tools/CLAUDE.md`). Tech stack (9, the plan's table has it). The gate paragraph (8, `src/agent/CLAUDE.md`). The read API paragraph (4, `src/api/CLAUDE.md`). The window paragraph (9, `web/README.md`). The venv line (2) |
| Belongs in `STATE.md`, a nested file, or `Plan.md` | 77 | Exit codes (5, `README.md` has them). The spend ceiling (5, `src/agent/CLAUDE.md`). `--once` costs money and the M7 and M7A cost facts (15, `STATE.md` and `model-routing.md`). Backoff (6, `src/agent/CLAUDE.md`). `SW_OPS_TARGET` (4, `STATE.md` environment). `CHAOS_ENABLED` (5, `STATE.md` demo recipe). The signpost table (17, stays, it is the point of the file). The Commands block (26, stays, every line is re-run at the ritual) |

The Commands block and the signpost table stay by instruction. Applying the sort: pile A stays, pile B
becomes one line each, pile C leaves except the two that stay. Result: about 90 lines. Ten over the
target as estimated. Actual after the edit: **104 lines**, of which the Commands block is 26 and the signpost table
17, so the prose a session reads is 61 lines. The two blocks Scott kept are what puts it over 80.

`src/agent/CLAUDE.md`, 305 lines: 270 are rules a session needs before touching the loop, the rails, or
the gate. 20 are history (the M4 loop measurements, the "not built, deliberately so" escalation paragraph
that the next paragraph contradicts). 15 are pointers. Applied: the stale escalation paragraph goes.

`src/tools/CLAUDE.md`, 209 lines: 195 rules, 10 history (M5's wrong-host story, kept because the table
under it is the contract), 4 pointers. Applied: nothing moves.

`src/models/CLAUDE.md`, 129 lines: 110 rules, 12 measurements a session needs before a local call, 7
pointers. Applied: nothing moves.

**Who decided.** Scott, in this review.

### Also closed in this review

- `docs/STATE.md` said a healthy free tick is 3.0 to 3.6 seconds. Since the herd sweep (M7A) it is about
  23 seconds, and at a 20-second demo cadence every tick overruns and says so. Corrected. 2026-09-11.
- The window's map placeholder named `docs/issues.md` on screen. Repointed. 2026-09-11.
- `docs/decisions/` was an empty folder promising ADRs. Removed; the decisions live in `STATE.md`. 2026-09-11.
- `docs/issues.md` had two items numbered 10. The artifact became #26. 2026-09-11.

---

## By design, not open. Easy to mistake for gaps

- `herd_health` owns animals and nothing else: the four animal categories and never a sensor incident. It
  cannot read a sensor, so a dry tank and a dead cow stay in two work orders and the supervisor joins them.
- `chaos` has zero tools and no brief. It is a test harness, not a responder, and writes over REST.
- The herd stage never fails the tick. A Farm or Care outage is `herd_error` on the line and zero animals
  answered, by design. A free tick is about 23 seconds because of it, and that is measured, not slow.
- `GATE_LANDED` is True from M6, and that made writes proposable, not callable. A model never calls a
  write tool. A human performs an approved proposal.
- An approve in the window performs the write on the deployed ranch. That is the gate doing its job, not
  a missing safety switch. The window asks once before sending it. A demo that wants to exercise approve
  plants a tool the ranch does not have (cookbook #47).
- Tier 1 exists from M7 and ships off (`TIER1_ENABLED=0`). Every model job is Opus until a ledger row says
  otherwise. That is the measured result, not a stub.
- A calm tick costs exactly $0.00, and the shift report is assembled in code below two sensing worlds.
- `--once` is exempt from the spend ceiling because a human is at the keyboard.
- `writes_pending` on a tick line is null on a tick that proposed nothing. The window reads the pending
  count from the gate route instead (cookbook #48).
- The window's browser never calls the API directly and never sees `OPS_API_TOKEN`. Same origin, so
  `API_CORS_ORIGINS` staying empty is correct.
- `src/models/embeddings.py` is an empty seam. Each SOP is a handful of rules and loading the whole file
  beats retrieving over it. Fill it in only when the corpus outgrows a prompt.
- `.claude/rules/` is empty. Nothing has needed a path-scoped rule yet.
- Prod is the default ledger on purpose. A default that quietly writes somewhere harmless is a default
  that ships.

---

## Closed

One line each. These are done being written about; the detail is in `docs/JOURNEY.md` under the phase.

- **#1** `herd_health` discovering the coyote kill through its own tools. Closed at M7A, 2026-09-11.
- **#2** A chaos storm front fusing into one shift report. Closed at M7A, 2026-09-11.
- **#3** The held set surviving a restart (migration `0005`). Closed at M6, 2026-09-11.
- **#10** Two processes appending to `audit.jsonl` (receipts became a table, migration `0007`). Closed at M8, 2026-09-11.
- **#11** The gate verified on `create_observation`, not just `restock_feed`. Closed at M7A, 2026-09-11.
- **#15** The first live pause on a real proposal. Closed at M7A, 2026-09-11, with #11.
- **#24** Too many docs. Consolidated in this review, 2026-09-11. The map above is the record.
- **#25** Root `CLAUDE.md` over its target. Sorted and trimmed in this review, 2026-09-11.
