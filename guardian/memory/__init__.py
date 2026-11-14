"""Guardian memory.

Two zones (docs/GUARDIAN_SPEC_V5.md, "Memory" and "Value integrity and memory protection"):

* Code-owned records, stored under `(user_id, "guardian", <kind>)`: approved values,
  proposals, the chat log, notes, the inbox and review records. No agent file tool is
  mounted on this part of the store. Approved values are written by exactly one
  function, `proposals.decide`, to apply a user's decision on a pending proposal.
* Reflector-owned files, stored under `(user_id, "reflector_files")`: brief.md,
  hypotheses.md and session summaries.

Modules: `formats` (the values.md entry format), `values` (the protected store),
`proposals` (records and the write path), `checks` (quote and evidence checks),
`records` (chat log), `keys` (store namespaces).
"""
