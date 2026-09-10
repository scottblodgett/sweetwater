"""The five tool slices, enforced in code. Arrives at M3.

The deployed MCP server exposes 19 flat tool names with no namespaces, so each slice is an
explicit name set. `create_react_agent` receives a **filtered** list: isolation that lives
in a prompt is a request, and a request is not a boundary.

The line worth defending: `herd_health` cannot read a sensor, and nothing but `water_feed`
can touch feed. That is what makes the supervisor real rather than decorative.

A test counts each set rather than asserting its contents, so widening a slice to fix a
symptom fails loudly. See `tests/CLAUDE.md` for what that rail proves.
"""
