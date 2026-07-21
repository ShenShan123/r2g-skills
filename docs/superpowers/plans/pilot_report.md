# R2G V1 Fixed Pilot Execution Report

- Agent commit: `3117f0e3c00ba528f6029fd9a7d569e37ac3b9dd`
- Platform: `nangate45`
- Gate cells: **32/49** (65.3%)
- Execution coverage: **49/49** (100.0%)
- Pass rate among attempted cells: **65.3%**
- End-to-end positive fixtures: **0/4** (0.0%)

## Gate Summary

| Gate | Passed | Total | Rate |
|---|---:|---:|---:|
| ENV | 4 | 4 | 100.0% |
| ACQ | 5 | 5 | 100.0% |
| SYNTH | 4 | 4 | 100.0% |
| RTL2FLOW | 5 | 5 | 100.0% |
| CONSTRAINT | 0 | 4 | 0.0% |
| FLOW | 3 | 4 | 75.0% |
| SIGNOFF | 0 | 4 | 0.0% |
| LEARNING | 5 | 5 | 100.0% |
| FLOW2GRAPH | 1 | 5 | 20.0% |
| GRAPH | 2 | 4 | 50.0% |
| PUBLISH | 3 | 5 | 60.0% |

## Gate Cells

| Fixture | Kind | Gate | Verdict | Summary |
|---|---|---|---|---|
| gcd_baseline | positive | ENV | **pass** | required flow and graph tools resolved |
| gcd_baseline | positive | ACQ | **pass** | repository revision and 1 RTL digests match |
| gcd_baseline | positive | SYNTH | **pass** | synth-only succeeded with 438 cells |
| gcd_baseline | positive | RTL2FLOW | **pass** | source-verified full-flow project is valid |
| gcd_baseline | positive | CONSTRAINT | **fail** | Fmax/SDC constraint is not qualified: fmax='ok' winner=1.0243910000000003 stamped=1.02439 |
| gcd_baseline | positive | FLOW | **pass** | all six ORFS stages completed in one run |
| gcd_baseline | positive | SIGNOFF | **fail** | strict signoff failed |
| gcd_baseline | positive | LEARNING | **pass** | terminal full-flow outcome is present and consistent |
| gcd_baseline | positive | FLOW2GRAPH | **fail** | selected DEF, signoff reports, and graph gate are not bound to the same clean run |
| gcd_baseline | positive | GRAPH | **pass** | all five graph views passed independent verification |
| gcd_baseline | positive | PUBLISH | **pass** | one complete graph generation is published atomically |
| wbuart32_axiluart | positive | ENV | **pass** | required flow and graph tools resolved |
| wbuart32_axiluart | positive | ACQ | **pass** | repository revision and 7 RTL digests match |
| wbuart32_axiluart | positive | SYNTH | **pass** | synth-only succeeded with 3533 cells |
| wbuart32_axiluart | positive | RTL2FLOW | **pass** | source-verified full-flow project is valid |
| wbuart32_axiluart | positive | CONSTRAINT | **fail** | Fmax/SDC constraint is not qualified: fmax='ok' winner=1.0502364 stamped=1.05024 |
| wbuart32_axiluart | positive | FLOW | **pass** | all six ORFS stages completed in one run |
| wbuart32_axiluart | positive | SIGNOFF | **fail** | strict signoff failed |
| wbuart32_axiluart | positive | LEARNING | **pass** | terminal full-flow outcome is present and consistent |
| wbuart32_axiluart | positive | FLOW2GRAPH | **fail** | selected DEF, signoff reports, and graph gate are not bound to the same clean run |
| wbuart32_axiluart | positive | GRAPH | **pass** | all five graph views passed independent verification |
| wbuart32_axiluart | positive | PUBLISH | **pass** | one complete graph generation is published atomically |
| verilog_i2c_master_axil | positive | ENV | **pass** | required flow and graph tools resolved |
| verilog_i2c_master_axil | positive | ACQ | **pass** | repository revision and 3 RTL digests match |
| verilog_i2c_master_axil | positive | SYNTH | **pass** | synth-only succeeded with 4635 cells |
| verilog_i2c_master_axil | positive | RTL2FLOW | **pass** | source-verified full-flow project is valid |
| verilog_i2c_master_axil | positive | CONSTRAINT | **fail** | Fmax/SDC constraint is not qualified: fmax='ok' winner=0.912609 stamped=0.912609 |
| verilog_i2c_master_axil | positive | FLOW | **fail** | backend run is incomplete; failed/missing stages=['synth', 'floorplan', 'place', 'cts'] |
| verilog_i2c_master_axil | positive | SIGNOFF | **fail** | strict signoff failed |
| verilog_i2c_master_axil | positive | LEARNING | **pass** | terminal full-flow outcome is present and consistent |
| verilog_i2c_master_axil | positive | FLOW2GRAPH | **fail** | selected DEF, signoff reports, and graph gate are not bound to the same clean run |
| verilog_i2c_master_axil | positive | GRAPH | **fail** | graph generation is incomplete, unversioned, or rejected by the independent verifier |
| verilog_i2c_master_axil | positive | PUBLISH | **fail** | published graph generation is inconsistent or has staging residue |
| secworks_sha256 | positive | ENV | **pass** | required flow and graph tools resolved |
| secworks_sha256 | positive | ACQ | **pass** | repository revision and 4 RTL digests match |
| secworks_sha256 | positive | SYNTH | **pass** | synth-only succeeded with 11345 cells |
| secworks_sha256 | positive | RTL2FLOW | **pass** | source-verified full-flow project is valid |
| secworks_sha256 | positive | CONSTRAINT | **fail** | Fmax/SDC constraint is not qualified: fmax='ok' winner=2.8707901 stamped=2.87079 |
| secworks_sha256 | positive | FLOW | **pass** | all six ORFS stages completed in one run |
| secworks_sha256 | positive | SIGNOFF | **fail** | strict signoff failed |
| secworks_sha256 | positive | LEARNING | **pass** | terminal full-flow outcome is present and consistent |
| secworks_sha256 | positive | FLOW2GRAPH | **fail** | selected DEF, signoff reports, and graph gate are not bound to the same clean run |
| secworks_sha256 | positive | GRAPH | **fail** | graph generation is incomplete, unversioned, or rejected by the independent verifier |
| secworks_sha256 | positive | PUBLISH | **fail** | published graph generation is inconsistent or has staging residue |
| trust_and_publication_guards | negative | ACQ | **pass** | all injected unsafe states were rejected |
| trust_and_publication_guards | negative | RTL2FLOW | **pass** | all injected unsafe states were rejected |
| trust_and_publication_guards | negative | FLOW2GRAPH | **pass** | all injected unsafe states were rejected |
| trust_and_publication_guards | negative | PUBLISH | **pass** | all injected unsafe states were rejected |
| ab_causality_and_lifecycle_guards | negative | LEARNING | **pass** | all injected unsafe states were rejected |

## Non-Passing Evidence

### gcd_baseline / CONSTRAINT / fail

Fmax/SDC constraint is not qualified: fmax='ok' winner=1.0243910000000003 stamped=1.02439

### gcd_baseline / SIGNOFF / fail

strict signoff failed

- `LVS is not strictly clean: status='skipped' mismatches=None`
- `routing is not clean: status=None violations=None`
- `RC extraction is incomplete: status=None`
- `timing is not clean: tier=None wns=None tns=None`
- `signoff_gate status is 'pass_with_caveats', expected 'pass'`

### gcd_baseline / FLOW2GRAPH / fail

selected DEF, signoff reports, and graph gate are not bound to the same clean run

### wbuart32_axiluart / CONSTRAINT / fail

Fmax/SDC constraint is not qualified: fmax='ok' winner=1.0502364 stamped=1.05024

### wbuart32_axiluart / SIGNOFF / fail

strict signoff failed

- `LVS is not strictly clean: status='skipped' mismatches=None`
- `routing is not clean: status=None violations=None`
- `RC extraction is incomplete: status=None`
- `timing is not clean: tier=None wns=None tns=None`
- `signoff_gate status is 'pass_with_caveats', expected 'pass'`

### wbuart32_axiluart / FLOW2GRAPH / fail

selected DEF, signoff reports, and graph gate are not bound to the same clean run

### verilog_i2c_master_axil / CONSTRAINT / fail

Fmax/SDC constraint is not qualified: fmax='ok' winner=0.912609 stamped=0.912609

### verilog_i2c_master_axil / FLOW / fail

backend run is incomplete; failed/missing stages=['synth', 'floorplan', 'place', 'cts']

- `/home/yangao/r2g_v1_pilot_2026_07_21_run01/projects/pilot_verilog_i2c_master_axil_052256d2/backend/RUN_2026-07-21_03-39-04_54979_2542`

### verilog_i2c_master_axil / SIGNOFF / fail

strict signoff failed

- `DRC is not strictly clean: status='fail' violations=4.0`
- `LVS is not strictly clean: status='skipped' mismatches=None`
- `routing is not clean: status=None violations=None`
- `RC extraction is incomplete: status=None`
- `timing is not clean: tier=None wns=None tns=None`
- `signoff_gate status is 'dirty', expected 'pass'`
- `antenna is not clean: status='fail' violations=4`

### verilog_i2c_master_axil / FLOW2GRAPH / fail

selected DEF, signoff reports, and graph gate are not bound to the same clean run

### verilog_i2c_master_axil / GRAPH / fail

graph generation is incomplete, unversioned, or rejected by the independent verifier

### verilog_i2c_master_axil / PUBLISH / fail

published graph generation is inconsistent or has staging residue

### secworks_sha256 / CONSTRAINT / fail

Fmax/SDC constraint is not qualified: fmax='ok' winner=2.8707901 stamped=2.87079

### secworks_sha256 / SIGNOFF / fail

strict signoff failed

- `DRC is not strictly clean: status='stuck' violations=None`
- `LVS is not strictly clean: status='skipped' mismatches=None`
- `routing is not clean: status=None violations=None`
- `RC extraction is incomplete: status=None`
- `timing is not clean: tier=None wns=None tns=None`
- `signoff_gate status is 'dirty', expected 'pass'`
- `antenna is not clean: status='unknown' violations=None`

### secworks_sha256 / FLOW2GRAPH / fail

selected DEF, signoff reports, and graph gate are not bound to the same clean run

### secworks_sha256 / GRAPH / fail

graph generation is incomplete, unversioned, or rejected by the independent verifier

### secworks_sha256 / PUBLISH / fail

published graph generation is inconsistent or has staging residue
