# Experiment 2/3 Repair-Needed RTL Mining

## Goal

Build a sufficiently large, provenance-complete pool of Verilog/SystemVerilog RTL that
is cleanly synthesizable but reproducibly fails the frozen Sky130HD physical task at
100 MHz, and that can be recovered by at least one preregistered legal intervention.
The mining proxy only decides screening order. It never assigns the formal label.
This is a targeted challenge-cohort construction procedure, not an estimator of the
natural prevalence of repair-needed RTL. Low-risk candidates are retained as
`not_screened_low_risk` and do not consume ORFS or enter any result denominator.

## Formal Label

A fixture is `repair-needed` only when all of the following hold:

1. The source identity, commit, top module, compilation closure, license evidence,
   clock and 100 MHz target are frozen.
2. Default ORFS at 25% target core utilization reproduces the same non-environment
   physical failure in two independent namespaces.
3. A preregistered legal repair reaches strict clean without changing RTL, clock,
   signoff checks, platform or other protected task inputs.
4. DRC, LVS, route, timing, antenna, RCX and provenance all pass. A frontend,
   synthesis or environment failure is not a repair-needed physical-design case.

## Fast Screening Funnel

### Stage A: seconds per candidate

- Reuse RTL-acquire qualification for license, source closure, top and clock.
- Run synth-only once and collect mapped cells, cell area and top-level port bits.
- Prioritize bus fabrics, arbiters, protocol bridges, memory controllers and MACs.
- Compute `io_pressure = top_level_port_bits / sqrt(synthesis_area_um2)`.
- Add a target-difficulty band when STA is available: candidates whose synthesis Fmax
  is near, but not orders of magnitude below, 100 MHz are more likely to be legally
  recoverable timing cases.
- Apply a preregistered score threshold and source/size quota. Only candidates above
  that boundary proceed to ORFS; the rest remain unlabelled rather than being called
  clean.

### Stage B: floorplan and placement only

- Check pin-capacity errors, placement overflow, density and RUDY congestion.
- Stop immediately when the baseline is clean through this stage or the failure is
  clearly an unsupported frontend/environment issue.
- Repeat only candidates with an observed physical symptom.

### Stage C: route only for survivors

- Run global route first and inspect congestion/overflow.
- Run detailed route and full strict signoff only after a candidate remains plausible.
- This avoids spending hours of KLayout/Netgen time on obvious clean or impossible RTL.

### Stage D: formal admission

- Reproduce the exact baseline failure.
- Apply the same preregistered bounded action set to every candidate.
- Require strict clean and save the full provenance-bound evidence package.

OpenROAD explicitly supports fast RUDY-based routability estimation and a more costly
global-route-based mode. ORFS also exposes stage-level execution and reports. These
facilities make the staged funnel preferable to blind full-flow screening:

- https://openroad.readthedocs.io/en/latest/main/src/gpl/README.html
- https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts/blob/master/docs/tutorials/FlowTutorial.md

CircuitNet likewise treats cell density, RUDY, pin configuration and congestion as
early routability features. This supports adding pin-density/RUDY summaries after
floorplan rather than relying only on RTL size:

- https://circuitnet.github.io/intro/overview.html

## Source Strata

Results must be reported separately for these source strata:

1. **Experiment 1 prospective source:** synth-qualified outputs from Experiment 1.
   Risk ranking selects a challenge subset; no prevalence claim is made for all 76.
2. **Independent real-world challenge pool:** externally sourced protocol, interconnect,
   processor and controller RTL. This tests transfer beyond the Experiment 1 pool.
3. **Benchmark challenge pool:** curated suites such as LogikBench. Human-authored and
   AI-generated designs must be labeled separately; this pool improves coverage but
   must not be presented as a natural prevalence sample.

The pinned LogikBench revision contains 250 self-contained Verilog benchmarks with
source and license metadata, and publishes Sky130 synthesis/STA baselines. It is useful
for cheap candidate ordering before R2G qualification and strict ORFS screening:

- https://github.com/zeroasiccorp/logikbench

## Confirmed Pool on 2026-08-09

| RTL | Size | Repeated default failure at 100 MHz | Legal feasibility | Status |
|---|---|---|---|---|
| `eth_mac_mii` | medium | 36 DRC violations | strict clean at util 17% | admitted |
| `can_fifo` | medium | 10 DRC violations | strict clean at util 12% | admitted |
| `nt35510_apb_adapter_v1_0` | small | 2 DRC and 1 route violation | strict clean at util 17% | admitted |
| `ultraembedded_sdram_axi` | medium | 60 DRC violations | strict clean at util 17% | admitted |
| `mor1kx_ctrl_prontoespresso` | medium | 98 DRC and 2 route violations | strict clean at util 17% | admitted |

The latter three cases were found by the expanded Experiment 1 screen. Their repeated
baseline metrics were identical, while the independent feasibility runs cleared DRC,
route, LVS, timing, antenna and RCX.

Across the currently completed development records, 52 unique fixtures have at least
one formal screen and five are admitted. This mixed, adaptively selected development
sample is useful for capacity planning only; `5/52` is not an estimate of natural
repair-needed prevalence.

## High-Value Near Misses

| RTL | Observed baseline problem | Why it remains useful |
|---|---|---|
| `forencich_axil_interconnect` | pin-placement failure | 1,218 I/O bits exceed the util-17 floorplan capacity |
| `forencich_axi_interconnect` | pin-placement failure | highest measured I/O pressure in the current inventory |
| `eth_mac_1g` | pin-placement failure | util-8 exploratory run is strict-clean; rerun under a preregistered expanded policy |
| `emaczero_axil_arb2` | placement failure, then LVS mismatch | physical flow recovers but strict-clean feasibility is not yet established |

An exploratory util-8 check was used only to decide whether a broader floorplan action
should be preregistered in the next protocol revision. `eth_mac_1g` reached strict clean
at 100 MHz with util 8% (zero route, DRC, LVS and antenna violations and clean setup/hold
timing). This cannot retroactively change the current util-17 admission result, but it
makes the design a high-confidence candidate for a fresh, preregistered screen.

## External Shortlist

The first LogikBench shortlist contains only eight high-priority candidates and is
intentionally source-labeled. The other 242 are not scheduled for ORFS:

| Candidate | Source class | Published Sky130 synth signal | Screening rationale |
|---|---|---:|---|
| `ethmac` | human-authored | Fmax 96.9 MHz | near the 100 MHz boundary; likely timing-sensitive |
| `umicross` | human-authored | Fmax 80.14 MHz | wide 8x8 UMI crossbar; routing and timing stress |
| `axiram` | human-authored | Fmax 85.28 MHz | AXI and memory-control structure near the target band |
| `apbregs` | human-authored | Fmax 78.70 MHz | control/register fabric near the target band |
| `gearbox66` | AI-generated | Fmax 81.25 MHz | moderate sequential datapath near the target band |
| `jesd204b` | AI-generated | Fmax 61.74 MHz | wide serial-link datapath; harder timing case |
| `huffman` | AI-generated | Fmax 69.24 MHz | compact but nontrivial timing candidate |
| `chiplink` | AI-generated | Fmax 153.24 MHz | retained for lane/pin congestion despite easier synth timing |

The repository is pinned locally at commit
`62fc37ec58451c7d8813d7d3e3e1d7a1377c9082`. Every candidate still has to pass R2G
source-closure qualification before any physical result is considered.

## Current Experiment Artifacts

- Existing-inventory risk ranking:
  `docs/experiments/signoff/experiment2_repair_candidate_ranking_v22.json`
- Human-readable ranking:
  `docs/experiments/signoff/experiment2_repair_candidate_ranking_v22.md`
- Risk-ranked validation cohort:
  `docs/experiments/signoff/experiment2_risk_ranked_cohort_v23.json`
- Risk-ranked campaign:
  `/home/yangao/r2g_exp2_risk_ranked_screen_2026_08_09_v23_run02`

The production Agent is not modified by this mining work. The ranking tool and campaign
files are experiment infrastructure; formal Experiment 2/3 conclusions use only the
frozen validator and the admission rule above.
