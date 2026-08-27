# TEHM authority V4 negative baseline

This directory is a persistent copy of the staging-only V4 authority bundle.
It records the current negative result for the broad
`DENSITY_RELIEF / CORE_UTILIZATION 50->40` rule.

- 8 support lineages, 8 full-oracle observations, and 8 graph contexts.
- Raw Pareto harmful rate: `7/8 = 0.875`; the existing harmful gate remains
  `<= 0`, so the rule is not production eligible.
- Authority decision: `DENY_CANONICAL_IMPORT`; `promotion_attempted=false`.
- The only supported registry row is `target_scope=route`, `candidate`,
  `status_version=2`. The historical unsupported `orfs:sky130hs:route` row is
  absent from this next-authority staging snapshot.
- Canonical memory was not modified. The canonical snapshot remains bound by
  SHA-256 `bd64290d6bdf4db59376325ca38b781b7c98b51dc12b3fb10377eb3c1d8ac89f`.

`orfs-next-full/` contains the two additional `and32` and `toggle32` full
ORFS phase reports. `failures/` retains compact report/log evidence for known
incomplete or failed cases; raw ORFS work directories remain on scratch by
policy.

The next experiment must use a separately frozen typed utility contract and a
lineage-disjoint prospective split. This bundle must not be treated as
promotion authority or as evidence that the broad rule is safe.
