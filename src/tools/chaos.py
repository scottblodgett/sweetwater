"""The fifth agent's hands: inject, heal, expire. Arrives at M5.

Two injection paths, and the asymmetry is forced rather than chosen:

  * **Sensor faults go to an overlay in `sw_ops`.** The deployed Sensor API is stateless
    and synthesizes every reading in code, so there is nowhere to write a fault into it,
    and its own injector deliberately refuses to register on Lambda. That guard is correct
    and stays. `src/tools/sensors.py` applies overlay rows on top of the honest live read
    before triage sees it: the deployed API stays truthful and this repo owns the lie, in
    one place, under test.
  * **Animal events are written for real** through the Care API, guarded by
    `CHAOS_ALLOW_WRITES` and confined to `CHAOS_ANIMAL_COHORT` so the rest of the herd
    stays pristine.

Seeded and self-healing. A `random.Random(CHAOS_SEED)` picks scenario, target, and timing
so a given seed replays the same demo, and every event carries a TTL whose expiry is what
produces `resolved` incidents. Chaos that cannot be reproduced is a flake, not a fixture.

The model is not in the load-bearing path: the PRNG picks what breaks, an optional LLM pass
writes the observation prose a human reads. Same ownership rule as severity.
"""
