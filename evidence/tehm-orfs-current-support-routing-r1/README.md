# Current exact-toolchain routing support cohort (2026-08-27)

This is a bounded, staging-only support audit on the packaged ORFS tree
(`OpenROAD 26Q3-1510-g6cb3f2b704`, `Yosys 0.68`, `sky130hs`).  The two selected
lineages are independent RTL sources (`selector_crc16` and `selector_uart16`).
Both used `ROUTING_CAPACITY_RECOVERY default→0.05`; both arms in each pair have
equivalence, synthesis/route/finish, timing, DRC, LVS, RCX, aggregate strict
signoff, complete DEF graph, toolchain binding, artifact digest, input binding,
and timing contract checks passing.  Both physical deltas are zero, so their
utility is `NEUTRAL` (non-harmful), not an improvement claim.

The FIFO `DENSITY_RELIEF 50→40` replay is retained as a negative control.  It
also has a complete oracle, but utility is `HARMFUL` (area `+7868.3 µm²`, power
`+0.000226 W`) and it is excluded from support selection.

The machine-readable audit is [`support_cohort_audit.json`](support_cohort_audit.json).
It is reproducible with:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=memory \
python3 memory/scripts/audit_orfs_support_cohort.py \
  /tmp/tehm-orfs/selector-crc-routing-r1 \
  /tmp/tehm-orfs/selector-uart-routing-r1 \
  --negative-root /tmp/tehm-orfs/selector-fifo-current-r2 \
  --output /tmp/tehm-orfs/support-cohort-routing-audit.json
```

The two-lineage support cohort establishes only `obligation_coverage=PASS` and
`harmful_rate=PASS`.  `rollback_verified`, `registry_verified`,
`cross_lineage_te`, and `conformal_coverage` remain `NOT_ESTABLISHED`; therefore
the decision is `DENY_CANONICAL_IMPORT`, `promotion_attempted=false`, and
canonical memory is unchanged.  These rows remain campaign-local staging
evidence and are not production runtime inputs.
