# ASAP7 UART qualification

The unmodified ORFS UART completes synthesis, placement, CTS, routing and final GDS. The first implementation passes transistor LVS, RTL and final gate simulation, and reset-based sequential formal equivalence. The full 334-check official DRC run reports 10 violations, so this first implementation is preserved as a diagnostic result, not a DRC-clean design.

| Item | Initial implementation |
|---|---:|
| Design | 8-bit UART with independent AXI-stream TX/RX and runtime 16-bit baud prescale |
| Platform / libraries | ASAP7, RVT, FF |
| Clock | 1.000 ns |
| Die / core area | 262.440 / 197.122 µm² |
| Standard cell area / utilization | 80.5545 µm² / 40.8654% |
| Logical cells / sequential cells | 626 / 79 |
| Setup worst slack | +0.710612 ns |
| Hold worst slack | +0.0381014 ns |
| Estimated power | 0.649972 mW |
| EDA tool elapsed sum / peak RSS | 65.77 s / 1117.11 MiB |
| Full DRC | 334 checks, 10 results: 2 V0.M1.AUX.3 + 8 M1.S.6 |
| Full transistor LVS | CORRECT, 12,999 MOS |
| Calibre DRC / LVS elapsed | 5.57 s / 6.08 s |

The area counter in `reports/ppa.json` includes physical taps in its `stdcell_count`; the logical count above comes from the checked full reference audit (547 combinational and 79 sequential instances). Power is the flow's estimate, not measured silicon power. The original report contains timing in ps; values here are converted to ns.

Validation uses the retained MIT-licensed RTL under `rtl/`. The same testbench decodes five TX frames, receives five valid RX frames, checks three backpressure cases, one bad stop bit and one overrun, first against RTL and then against `6_final.v` with the actual Liberty behavior models. Both runs pass. Logs are in `sim/`.

`formal/final_verification/verification.json` records 81/81 proved points with two-step induction and a reset-only base case. All 79 register bits and 15 output bits are covered. Initial register states are arbitrary; all RTL initialization attributes are removed. Two internal data-register observations are masked to zero while their bit counter is zero, because their previous data are inactive until the next transaction. Actual data paths and every external output remain unchanged. These observation points are proved, not assumed. All inputs other than the first reset are unconstrained. Inverting the final TX output produces the expected formal counterexample.

The separate `reports/reference_validation.json` audits every retained transistor connection and L/W/nfin parameter, all used combinational reference functions, sequential transistor topology, and the three zero-device tap/filler types. Digital formal equivalence uses Liberty behavior and makes no analog or direct PEX sequential-function claim.

The DRC markers identify two NAND3 output pin-access landings that extend into a native V0 edge and four OA22 output access corners (two markers per location). Coordinates and real hierarchical shapes are in `signoff/marker_geometry.json`. The independent `multi_uart_corner20` variant preserves a second real run: larger core plus 20 nm corner spacing removes M1.S.6, but reports 2 V0.M1.AUX.3 and 23 M1.S.2; its LVS is also CORRECT. The isolated strict-via implementation in `multi_uart_strict` subsequently passes all 334 DRC checks with zero results and full transistor LVS CORRECT; the official DRC/LVS rules remain unchanged. See that project’s `RESULT.md` for the final qualified implementation.
