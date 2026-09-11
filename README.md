# Sweetwater

The agentic layer for **Sweetwater Land & Cattle Co., LLC** - a fourth-generation Wyoming
cattle ranch running ~1,000 mother cows across ~34,000 deeded and leased acres, with
~160 sensors across four distinct sensing worlds.

One orchestrator runs continuously, driving **five sub-agents**:

| Agent | Job |
| --- | --- |
| `water_feed` | nobody dies of thirst or hunger; reserves before a storm |
| `herd_health` | sick, down, dead, or missing animals |
| `infrastructure` | containment, power, fuel, no spill no fine |
| `compliance` | AUM stocking, habitat, keep the payments |
| `chaos` | breaks the ranch on purpose, so the other four have real work |

The ranch itself is **already deployed** (four REST APIs plus an MCP server on Lambda,
`scott-jasper/mcp-farm`) and is frozen and read-only from here. This repo is the part that
thinks.

## Quick start

```bash
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1   # PowerShell; bash on Windows: source .venv/Scripts/activate
pip install -r requirements-dev.txt
cp .env.example .env          # fill in DATABASE_URL, and ANTHROPIC_API_KEY from M2 on
python main.py --handshake    # proves the deployed ranch is reachable
```

A good handshake reports **19 tools, 160 sensors, 32 locations** in about two seconds.

```bash
python main.py --once   # exactly one tick        (live: sweeps, triages, and spends)
python main.py          # the continuous loop     (live: every TICK_INTERVAL_SECONDS, halts at SPEND_CEILING_USD)
python main.py --no-spend  # either of the above, stopped at the end of the free pass. No bill
python main.py --api    # the read API, its own process (M8): reads sw_ops, never the ranch, never a model. Needs OPS_API_TOKEN
cd web && npm install && cp .env.example .env.local && npm run dev   # the window (M9): http://localhost:3000, against the API above
```

`--once` is real from M1 and **costs money from M2**: its last two stages assemble an
evidence packet and hand it to Opus, once per newly-opened incident. Roughly 24k to 58k
tokens on a first run against a quiet ledger, falling as incidents become `ongoing`.

The loop exits **0** on a clean drain (Ctrl+C once, SIGTERM, or SIGBREAK), **1** if forced or
broken, **2** on a config refusal, and **4** when the per-run spend ceiling halts it. The ceiling
defaults to $10 and has no unlimited setting. Exit 3 was "not built yet" and retired at M8, when the
last unbuilt mode landed.

`--api` serves `/health`, `/ops/incidents`, `/ops/report`, `/ops/stream` (SSE), and `/ops/gate` on
`API_HOST:API_PORT` (default `127.0.0.1:8000`), in the same `{data, meta}` envelope the four ranch
services use. Reads are open; `POST /ops/gate` approves a real write and needs a bearer token from
`OPS_API_TOKEN`. It reads the `sw_ops` ledger and nothing else, so a browser refresh can never spend a
token or touch the ranch. Detail: `src/api/CLAUDE.md`.

The window (`web/`) is a Next.js App Router app that reads that API and nothing else. Its route
handlers proxy every `/ops/*` call server-side, so the bearer token lives in `web/.env.local` and never
reaches a browser. One page: the token gauge off the SSE stream, the incident feed with filters, the
rails read off the latest tick line, the shift report, the gate with approve and reject, and a ranch-map
placeholder that says why it is empty. Verified against `next dev` on this machine; the Vercel deploy
waits on where the API runs (`docs/open-issues.md` #5, #23). Detail: `web/README.md`.

No Docker is needed; dockerizing is a deferred FUTURE-1 item, not part of the build: the upstreams are already deployed and agent state lives in
Supabase, so there is nothing local to stand up. A local Postgres is needed only to run
the store tests, which must never point at Supabase. Node 22 is needed only for the window.

The build ran M0 through M9 and every phase is landed; M9 was the last. What a session picks up
next is in `docs/state.md`'s Next row and `docs/open-issues.md`.

## Verifying a build

```bash
pytest                        # rails; passes offline, never touches Supabase
ruff check .
mypy src main.py              # strict
python main.py --handshake    # the live check: 19 tools, 160 sensors, 32 locations
cd web && npx tsc --noEmit && npm run lint && npm run build   # the window's gate, three commands
```

`pytest` **drops and re-migrates the local `sw_ops_test` schema**, so it cannot run while a demo or a
`SW_OPS_TARGET=test` loop is using that ledger. Two sessions cannot run it at once either.

The handshake exits **0** on success and **1** on failure, and writes a line to
`logs/tick.jsonl` either way. That last part is deliberate: a run that produces no line is
indistinguishable from a process that never started.

## Watching it work

```bash
tail -f logs/tick.jsonl | jq -r '[.tick,(.opened//0),(.ongoing//0),(.resolved//0),(.input_tokens//0),(.output_tokens//0)]|@tsv'
jq -r 'select(.finish_reason | IN("stop","end_turn","tool_use","stop_sequence") | not)' logs/agent.jsonl
```

The second one should be empty. It names the healthy **set** because the two providers spell their stop reasons
differently (cookbook #10).

Three streams: `tick.jsonl` (the heartbeat), `agent.jsonl` (the instrument),
`audit.jsonl` (the receipt). Schemas in [docs/plan.md](docs/plan.md), under Logging.

## Docs

Nine files, each answering one question a reader actually has.

| | |
| --- | --- |
| [docs/state.md](docs/state.md) | where the build stands right now: the module map, the decisions made, the verified environment, the live ranch facts, the demo recipe. The session-start briefing |
| [docs/plan.md](docs/plan.md) | what it is and why: the tree, the architecture, the tool slices, the chaos agent, the three log streams and the tables beside them, the milestones |
| [docs/sweetwater-ranch.md](docs/sweetwater-ranch.md) | the scenario canon, the one source the SOPs derive from |
| [docs/model-routing.md](docs/model-routing.md) | local by default, Opus where it earns it: the design and the ledger of what moved tiers, with proof |
| [docs/journey.md](docs/journey.md) | what actually happened, and where it diverged, written at every boundary |
| [docs/cookbook.md](docs/cookbook.md) | the lessons, ordered by the pain that produced each one; 48 stable numbers, 45 entries after three merges |
| [docs/open-issues.md](docs/open-issues.md) | everything still open, in plain language: what is wrong, why it matters, what it would take, who decides |
| [docs/transcripts/](docs/transcripts/) | the pinned fixtures: the no-brief pair (M3) and the M7 local-versus-Opus pairs |
| [web/README.md](web/README.md) | the window: the proxy, the panels, the three-command gate |
| [CLAUDE.md](CLAUDE.md) | orientation for AI assistants; nested files per package |

## Two ideas the whole thing rests on

**Code owns what a machine consumes; the model owns what a human judges.** Severity is
decided in code, always. A model handed a verdict and asked to justify it fabricates the
justification.

**The cheap path is a cheaper architecture, not just a cheaper model.** Evidence packets
are assembled in code so a model judges one page instead of driving a tool loop. Local
models write well and navigate badly.
