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
python main.py          # the continuous loop     (stub until M4, exits 3)
python main.py --api    # read API only           (stub until M8, exits 3)
```

`--once` is real from M1 and **costs money from M2**: its last two stages assemble an
evidence packet and hand it to Opus, once per newly-opened incident. Roughly 24k to 58k
tokens on a first run against a quiet ledger, falling as incidents become `ongoing`.

An unbuilt mode exits **3** and names the milestone that brings it, so "not written yet"
never looks like "broken."

No Docker is needed until M10: the upstreams are already deployed and agent state lives in
Supabase, so there is nothing local to stand up. A local Postgres is needed only to run
the store tests, which must never point at Supabase.

## Verifying a build

```bash
pytest                        # rails; passes offline, never touches Supabase
ruff check .
mypy src main.py              # strict
python main.py --handshake    # the live check: 19 tools, 160 sensors, 32 locations
```

The handshake exits **0** on success and **1** on failure, and writes a line to
`logs/tick.jsonl` either way. That last part is deliberate: a run that produces no line is
indistinguishable from a process that never started.

## Watching it work

```bash
tail -f logs/tick.jsonl | jq -r '[.tick,(.opened//0),(.ongoing//0),(.resolved//0),(.input_tokens//0),(.output_tokens//0)]|@tsv'
jq -r 'select(.finish_reason | IN("stop","end_turn","tool_use","stop_sequence") | not)' logs/agent.jsonl
```

The second one should be empty. It names the healthy **set** rather than one healthy value
because the two providers disagree: Anthropic says `tool_use` and `end_turn`, Ollama says
`stop`. This file previously shipped `select(.finish_reason!="stop")`, which flagged every
healthy Opus call as a config bug.

Three streams: `tick.jsonl` (the heartbeat), `agent.jsonl` (the instrument),
`audit.jsonl` (the receipt). Schemas in [docs/logging.md](docs/logging.md).

## Docs

| | |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | the shape, the free/expensive split, the tool slices |
| [docs/sweetwater-ranch.md](docs/sweetwater-ranch.md) | the scenario canon |
| [docs/model-routing.md](docs/model-routing.md) | local by default, Opus where it earns it (a ledger) |
| [docs/logging.md](docs/logging.md) | the three log streams |
| [docs/JOURNEY.md](docs/JOURNEY.md) | what actually happened, and where it diverged |
| [CLAUDE.md](CLAUDE.md) | orientation for AI assistants; nested files per package |

## Two ideas the whole thing rests on

**Code owns what a machine consumes; the model owns what a human judges.** Severity is
decided in code, always. A model handed a verdict and asked to justify it fabricates the
justification.

**The cheap path is a cheaper architecture, not just a cheaper model.** Evidence packets
are assembled in code so a model judges one page instead of driving a tool loop. Local
models write well and navigate badly.
