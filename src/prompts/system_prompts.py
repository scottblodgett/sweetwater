"""The rules every sub-agent brief inherits. Arrives at M2.

Three of them are already decided and are not up for negotiation by a prompt:

  * **Severity is not yours.** `src/tools/triage.py` already ranked it in code. A model
    handed a verdict and asked to justify it fabricates the justification.
  * **Cite your SOP.** A work order that names no rule is an opinion.
  * **No invented premises.** Name the real sensor and quote the real reading, or say you
    cannot. A confident all-clear is the single worst output a ranch monitor can produce.

Deliberately unwritten until M2 rather than drafted now: the text has to be written
against a real evidence packet from `src/tools/evidence.py`, and a prompt authored against
an imagined one reads fine and grounds nothing.
"""
