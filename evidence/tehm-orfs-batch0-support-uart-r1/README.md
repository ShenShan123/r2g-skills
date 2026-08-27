# ORFS Batch-0 support UART evidence (2026-08-26)

This report records one bounded, source-disjoint support pair executed with the
packaged OpenROAD/Yosys tree at `/data2/quewk/r2g-repro/OpenROAD-flow-scripts`.
The parameterized UART pair used a fixed 2.8 ns SDC contract copied from the
ORFS AES template and rebound to `uart`/`clk`; no SDC or RTL file was changed
after prepare. Both `CORE_UTILIZATION=50` and `40` arms completed synthesis,
route, finish, equivalence, strict signoff, PPA extraction, and DEF graph
extraction. The resulting observation is `ELIGIBLE_POSITIVE` and was imported
to an isolated staging DB as one training row.

The pair is not a promotion result. Its physical utility is explicitly
`HARMFUL`: lowering utilization increased die area by 3119.8 µm² and power by
0.000052 W, while WNS decreased by 0.127763 ns. The staging import therefore
does not imply that a DENSITY_RELIEF rule is useful or production eligible.
The independently generated rule-authority receipt derives measurements from
the preserved observation chain. It records `obligation_coverage=PASS`,
`cross_lineage_te=FAIL`, and `harmful_rate=FAIL` (the measured utility verdict
is `HARMFUL`); `rollback_verified`, `registry_verified`, and
`conformal_coverage` remain `NOT_ESTABLISHED`. It returns
`DENY_CANONICAL_IMPORT`. Canonical memory remained unchanged throughout.

Machine-readable details: [`batch0_support_uart_report.json`](batch0_support_uart_report.json).
