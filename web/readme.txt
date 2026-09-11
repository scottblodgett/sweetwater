agent-lab-ui/ — THE DASHBOARD (watch the Phase II runs)

A localhost mission-control dashboard for watching the agent-lab Phase II
runs, because the scripts only print to a terminal and that hides the SHAPE
over time that is the whole Phase-II lesson (tokens climb in II.1, go flat in
II.2/II.4; the fact store grows; the gate pauses). It shows that shape live: a
token-gauge line chart, a scrolling verdict feed, a rails panel, a summary
card, and a human-approval gate with real approve/reject buttons. It can run a
rung live (pick a model — qwen2.5 -> qwen3.5 -> kimi -> opus, worst to best)
or replay a captured *.run.txt. The scripts stay pure terminal tools; they
just call a tiny shared emit() that prints one machine-readable "@@" line
alongside their unchanged human output, and the UI parses only those. All
server logic lives in exported functions so a later Next.js port is a wrapper
swap, not a rewrite. Standalone (its own npm install, not a workspace):
  cd agent-lab-ui && npm install && npm run dev   # -> http://localhost:4500
The design brief is ../docs/agent-lab-ui-plan.md; the full README (seam,
re-capture, architecture, file map) is README.md next to this file.
