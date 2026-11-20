# ASAP7 Multi-Design Qualification

2026-11-16, GMT+8.

**UART, AES-128, and PicoRV32 completed RTL→GDS, final gate-level functional testing, and full transistor LVS. UART also passed all 334 official DRC checks.** Together with GCD, two designs have complete zero-violation DRC and matching LVS. AES/Pico full DRC exceeded the 600 s per-run budget.

**Additional performance probe:** With identical merged AES input and full rules, -hyper completed 328/334 checks in 60 s and reported 157 expanded results from completed rules. Six checks remained unfinished; this is not the final result count. Reordered operation indexes cannot establish that the bottleneck has been cleared. See the [audit](drc_diagnosis/performance_audit/REPORT.md). Later individual-rule results are documented in the [second round](drc_diagnosis/acceleration_round2/REPORT.md).

| Design | Logic cells | Official DRC | Official LVS | Tool-stage elapsed sum | Peak RSS |
|---|---:|---|---|---:|---:|
| GCD, preceding run | 395 | 334 checks, zero violations | CORRECT | 65.48 s | 1079.56 MiB |
| UART, repaired | 626 | **334 checks, zero violations** | **CORRECT** | **70.28 s** | **1112.88 MiB** |
| AES-128 | 13291 | Incomplete; merged-input probe reported 157 partial results | **CORRECT** | 863.83 s | 2514.64 MiB |
| PicoRV32 | 11528 | 600 s timeout, no final verdict | **CORRECT** | 503.51 s | 2354.56 MiB |

Configuration: ASAP7 r1p7, 7.5-track RVT cells, FF timing corner, 1 ns clock, four implementation threads. Official DRC rules and LVS comparison requirements were not weakened. Logic counts exclude tap/filler/decap. Complete LVS retains all active and decoupling devices. UART/AES/Pico compare **12999 / 264619 / 319443 MOS**, respectively, against independent official-library references.

Resource accounting sums 19 EDA elapsed-time records and takes maximum RSS. It excludes compilation, wrapper overhead, journal/database waits, and Calibre signoff. AES/Pico implementation wrapper times are 882.10/520.91 s. UART shared-filesystem accounting waits remain in raw logs and are excluded from tool-stage time. Older integer-truncated stage totals are not used here.

## Functionality and timing

- **UART:** TX/RX frames, receive backpressure, invalid stop bits, and overflow pass. Final-netlist formal equivalence passes; an intentionally incorrect output is rejected.
- **AES-128:** Complete key expansion and encryption. RTL and final netlist pass 35 independent-reference vectors. Formal proof did not finish within this run's budget; simulation is not reported as a complete proof.
- **PicoRV32:** RV32I processor and native memory interface. RTL and final netlist pass a 40-instruction program in three memory wait-state modes, covering arithmetic, branches, memory, jumps, and trap termination. This is program simulation.

All three have nonnegative setup/hold slack at 1 ns. Setup margins are **+0.709895 / +0.352094 / +0.277954 ns**; standard-cell areas are **80.5545 / 1519.34 / 1478.65 µm²**. Native ps timing values are converted to ns.

## Validated adaptations and remaining scope

The first UART had ten official DRC violations. A local OpenROAD pin-access repair enforces Y enclosure at side boundaries when via-in-pin is explicitly requested. With unchanged RTL, die dimensions, and technology configuration, the rerun has zero complete DRC results. The source CDL SHA256 matches before and after repair; MOS connectivity and parameters are unchanged. An isolated CPU binary built from the same version was used; the shared installation was not replaced. See [patch](reference_diagnosis/strict_via_in_pin.patch), [build evidence](multi_analysis/strict_via_binary.json), and [UART result](multi_uart_strict/RESULT.md).

AES/Pico initial DRC attempts consumed 600 s at the same encrypted geometry block. Flattening and FIN/GATE normalization preserve mask geometry by per-layer XOR; the GCD control still passes all 334 checks. Rule-aligned log analysis places the bottleneck in an ACTIVE-width-related derived block, after the FIN check has completed. Large-design complete DRC remains outstanding. The merged-input AES -hyper probe reports V0.M1.AUX.3=56, M1.S.6=100, and M5.S.5=1; these are partial log results, not a final count, and markers have not been individually reviewed. Further checking can use saved final GDS files.

These results support isolated fourth-platform integration: two small designs have complete signoff, while large-design implementation, functional testing, and transistor LVS work. Full large-design DRC is required before broadening default support. UI and normal platform gates remain unchanged.

## Evidence

- [UART result](multi_uart_strict/RESULT.md), [DRC](multi_uart_strict/signoff/drc/drc_report), [LVS](multi_uart_strict/signoff/lvs/mask.lvs.rep)
- [AES result](multi_aes/RESULT.md), [original DRC timeout](calibre_evidence/multi_aes/drc/invocation.json), [LVS recheck](calibre_evidence/multi_aes_property65536/lvs/mask.lvs.rep)
- [PicoRV32 result](multi_picorv32/RESULT.md), [original DRC timeout](calibre_evidence/multi_picorv32/drc/invocation.json), [LVS recheck](calibre_evidence/multi_picorv32_property65536/lvs/mask.lvs.rep)
- [Machine-readable summary](multi_design_results.json), [evidence SHA256](multi_design_evidence_sha256.json), [summary script](summarize_multidesign.py)
- [GCD qualification and reproduction](REPORT.md)

AES/Pico LVS rechecks use a 65536 property-search budget to eliminate premature-search warnings. Rules, device tolerances, and inputs are unchanged. Early failures, timeouts, and diagnostic variants remain archived.
