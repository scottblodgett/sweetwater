"""Job to tier to model, and the escalation predicate. Arrives at M7.

Not to be confused with the routing table in `src/agent/agent.py`, which answers a
different question: that one decides which of five agents owns an incident, this one
decides which model runs a job.

Two tiers. Tier 1 is local (`gemma4:e4b` via Ollama) and default, because nothing it is
asked to do classifies: code already ranked severity and a human reads the output. Tier 2
is Opus, for real tool-driving investigation, cross-domain fusion, and anything proposing
a write through the gate.

Escalate when any of these holds, and log which one fired: the Tier-1 judge returned
`insufficient_information`, triage marked the incident critical, two or more sensing worlds
opened incidents in the same tick, or a `Finding` proposes a write.

**The all-clear rail lands before the first job moves down**, not after. A Tier-1 model may
never produce an all-clear on an incident code has already flagged.
"""
