"""The wire shapes. Arrives at M8.

Pydantic v2 models for `Finding`, `Incident`, `ShiftReport`, `GateDecision`, and
`ChaosEvent`, plus the `{data, meta}` envelope.

Cross-service IDs are **plain strings, never foreign keys**. Orphans are allowed on purpose
and no schema here adds an existence check against another service.
"""
