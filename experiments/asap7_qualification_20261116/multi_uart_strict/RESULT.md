# ASAP7 UART: full DRC/LVS pass

The unmodified ORFS UART completes the full RTL-to-GDS flow and passes the unchanged official ASAP7 Calibre decks: **all 334 DRC checks, zero violations; full transistor LVS CORRECT**. RTL simulation, final gate simulation, and reset-based sequential formal equivalence also pass.

| Metric | Result |
|---|---:|
| Design | 8-bit full-duplex UART, runtime 16-bit baud prescale |
| Library / target clock | ASAP7 RVT FF / 1.000 ns |
| Logical cells / flip-flops | 626 / 79 |
| Retained transistor devices | 12,999 MOS |
| Die / core area | 262.440 / 197.122 µm² |
| Standard cell area / utilization | 80.5545 µm² / 40.8654% |
| Setup / hold worst slack | +0.709895 / +0.0381759 ns |
| Setup / hold total negative slack | 0 / 0 |
| Estimated total power | 0.649777 mW |
| EDA tool elapsed sum / peak RSS | 70.28 s / 1112.88 MiB |
| Official DRC | 334 checks, 0 results, 6.57 s |
| Full transistor LVS | CORRECT, 7.05 s |

The final experiment changes one implementation component relative to `multi_uart`: the isolated OpenROAD binary enforces the full via-in-pin constraint instead of the side-edge exception. Die/core size, 1 ns target, RTL, PDN, asymmetric technology LEF and standard-cell edge-window LEF are unchanged. The initial ten official DRC violations are eliminated without changing any DRC/LVS rule or transistor reference. The before/after `source.cdl` SHA-256 is identical: `274c9d145fb3f3ec13a3c89b670520d2d870f90fb43fe8a0e299ebd3fca3e3bb`.

The isolated binary is `reference_diagnosis/patched_openroad`, version `26Q3-318-g6b9d7fb806`, SHA-256 `89886cb61fa37ef9e57092565368be576db0e0a135b7e26d0b36bd3ac343257a`. The installed production binary is unchanged.

The retained MIT-licensed UART sources are copied unmodified from ORFS `designs/src/uart`. Both RTL and final gate simulation decode five TX frames, receive five valid RX frames, check three backpressure cases, a bad stop bit, and an overrun. Final gate simulation uses the real pinned Liberty models with no logic black boxes. See `sim/simulation.log` and `sim/gate_probe_simulation.log`.

`formal/final_verification/verification.json` proves 81/81 points with two-step induction and a reset-only base case. All 79 state bits and 15 output bits are covered; all initial register states and subsequent inputs are arbitrary. Only two internal, inactive data-register observations are zero-masked while their bit counters are zero. Existing primitive cells and direct connections are checked unchanged by `masked_prepared.observation_integrity.json`; the actual data paths and every external output retain their original behavior. Inverting the final TX output produces the expected counterexample. This is a synchronous digital Liberty proof, not an analog or direct PEX-function simulation.

`reports/reference_validation.json` independently audits all 12,999 retained MOS, unchanged D/G/S/B connections and model/L/W/nfin parameters, 40 combinational cell types over 442 exhaustive input vectors, the used sequential cell’s exact transistor topology, and zero-device tap/filler omission. The audit passes.

`signoff/` contains the complete Calibre logs, runsets, checksums, extracted transistor netlist and raw reports from the final GDS. The DRC input is only renamed to the deck's expected top; the LVS copy adds the required pin-label datatype. Non-text geometry and instance transforms are verified identical (18,379 records, SHA-256 `b4a8381380874d9837e994d3ae2edc378af5fae78ec0e4266d146dfeea640092`). LVS property resolution maximum is 1024, with no property-limit warning.

Timing values above convert the native report's ps into ns. Power is a flow estimate. EDA tool elapsed time is the sum of all 19 exact `Elapsed time` records (70.28 s); peak RSS is the largest reported KiB value divided by 1024 (1112.88 MiB). Total reported user+system CPU time is 101.67 s. These exclude wrapper bookkeeping and external Calibre and are not end-to-end wall-clock time. The integer-truncated ORFS summary table is not used for these measurements. Shared optional journal writes encountered long filesystem waits after completed route and finish stages; those waits and the termination of optional journal writers are separately recorded in `reports/journal_wait.json`. All actual EDA stage logs and final outputs are retained. The wrapper subsequently completed normally, archived all results and repeated the final checks successfully. The earlier identical-geometry signature evidence is also retained in `signoff_probe/`; binary GDS hashes differ after regeneration because of the GDS timestamp fields.

The initial ten-violation run remains in `multi_uart`; the larger-core/corner20 diagnostic run remains in `multi_uart_corner20`. Neither failed DRC result has been overwritten.
