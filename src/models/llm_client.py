"""The provider registry, and the receipts. Arrives at M2, Tier 2 only.

Four things this module exists to get right, all of them paid for once already and all of
them written up in `src/models/CLAUDE.md`:

  * **`ChatOllama` from `langchain-ollama`, never `ChatOpenAI` pointed at the shim.** The
    native `/api/chat` endpoint is the only one that honors `num_ctx`; the shim ignores it
    silently, which presents as "small models are too weak."
  * **`num_ctx` set explicitly to 16384.** Never a default.
  * **`reasoning_effort` is an explicit per-call argument, never an ambient env var.** A
    knob that silently changes verdicts belongs where the actor is built.
  * **`finish_reason` is logged on every call, before validation runs.** `"length"` means
    the model never got to answer and `"stop"` means it answered badly. They present
    identically in the output text and one is a config bug.
"""
