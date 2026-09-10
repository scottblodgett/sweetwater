"""Assemble the evidence packet in code, so a model judges one page. Arrives at M2.

The cheap path is a cheaper architecture, not just a cheaper model. The free pass already
knows exactly which sensor is bad and holds the ranch map, so telling a sub-agent to go
find out what is wrong pays a model to rediscover what code already knows.

The packet: the incident, that sensor's recent history, its sibling sensors at the same
location, the animals in that pasture, and the relevant SOP. One call, no tool loop. The
model is not navigating, it is judging a page, which is demonstrably the thing local models
are good at and open-ended tool loops are how a confident false all-clear gets produced.
"""
