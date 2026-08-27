# Batch-0 dual-UART support cohort (shadow evidence)

This is a source-bound, evaluation-only ORFS cohort for the R2G authority
firewall. It uses the packaged OpenROAD/Yosys tree at
`/data2/quewk/r2g-repro/OpenROAD-flow-scripts` and one fixed `2.8 ns` SDC
contract for two source-disjoint RTL lineages: `uart` and `uart-no-param`.

Both before/after pairs completed synthesis, route, finish, independent source
identity equivalence, strict signoff, PPA, and DEF graph extraction. They were
imported only into campaign-local staging. Both physical deltas are harmful:
lowering `CORE_UTILIZATION` from 50 to 40 increases area by `3119.8 um2`,
increases power by `0.00005159 W`, and worsens WNS by `0.127763 ns`.

The independent authority receipt therefore records:

```text
cross_lineage_te = PASS
obligation_coverage = PASS
harmful_rate = FAIL
rollback_verified = NOT_ESTABLISHED
registry_verified = NOT_ESTABLISHED
conformal_coverage = NOT_ESTABLISHED
decision = DENY_CANONICAL_IMPORT
promotion_attempted = false
```

Canonical memory remained unchanged. This result establishes replication and a
real harmful physical effect; it is not a promotion result and must not be
used as positive utility evidence.

The full external observation chain and ORFS run tree were intentionally kept
under `/tmp/tehm-orfs/orfs-batch0-uart-dual-support-r1`; the hashes below are
the durable binding for audit. Re-run the bounded lane with
`memory/evaluation/orfs_batch0_rtl_manifest_support_uart_dual_v1.json`, then
rebuild the authority receipt from the resulting observation chain.
