"""The five briefs, one per sub-agent. Arrives at M3.

A sub-agent inherits nothing. Whatever the supervisor knows and does not put in the brief
is not in the room, and the whole architecture rests on that fact rather than asserting it:
M3 runs one worker with no brief, captures it flailing, then passes the brief and captures
it working, and commits the two side by side.

The isolation that actually matters lives here and in the SOP set, not in tool-name
exclusivity. Three of the four responders legitimately share the sensor read tools.
"""
