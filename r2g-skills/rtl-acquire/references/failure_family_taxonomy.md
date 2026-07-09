# Failure Family Taxonomy

This file defines the normalized failure families used by the skill.

Purpose:

- group superficially different logs into the same repair family
- keep repair-policy learning interpretable
- make retry/exclude decisions less dependent on exact log strings

## Core Families

- `missing_module`
  - missing bundled RTL dependency or helper module
  - first action: locate bundle files or generate a constrained stub when safe

- `include_missing`
  - missing include/header resolution
  - first action: discover include directories and retry synthesis

- `frontend_parse_error`
  - parser/front-end incompatibility in `.sv`, `.vhd`, or mixed-source projects
  - first action: frontend-specific fallback (`sv2v`, `vhd2vl`, or alternate frontend)

- `memory_heavy`
  - synthesis exceeds memory budget or triggers `SYNTH_MEMORY_MAX_BITS`
  - first action: memory fallback and `resource_tier=high`

- `macro_blackbox`
  - macro/SRAM/technology-dependent unresolved blocks
  - default action: reject for main dataset unless a lossless substitute exists

- `helper_collision`
  - synthetic helper/module naming collisions such as `$abstract\\dff`
  - default action: exclude low-value cases, retry only when design value is high

- `graph_empty`
  - graph conversion produced no usable nodes
  - default action: reject unless synthesis/netlist export is clearly repairable

- `mapping_drift`
  - gate types or labels drift outside the current mapping space
  - first action: repair mapping and re-convert graph

- `top_mismatch`
  - expected top is wrong or parameterization invalid
  - first action: repair top metadata or parameter set

- `duplicate_or_low_value`
  - design is technically processable but adds little dataset value
  - default action: exclude or downgrade priority

## Notes

- Exact fingerprints still matter for recurrence statistics.
- Families are the layer that should drive repair policy defaults.
- When a new recurring failure appears, add it here before overfitting to one exact log.
