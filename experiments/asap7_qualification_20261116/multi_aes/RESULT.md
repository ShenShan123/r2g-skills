# ASAP7 AES-128 Qualification Results

The complete AES-128 encryption core finishes synthesis, placement, CTS, detailed routing, and GDS export. RTL and final gate netlists each pass **35/35 vectors**; transistor LVS is **CORRECT**. Full official DRC did not finish within the original 600 s budget, so this design is not counted as fully signed off. Formal equivalence is **NOT PROVED**, without a functional counterexample.

| Check | Outcome | Evidence |
|---|---|---|
| RTL | Complete ORFS aes_cipher_top, key expansion, round constants, S-box; original licenses, no logic blackboxes | [Provenance](source_manifest.json), [RTL](rtl/) |
| Lint | Compiles; four original unused-variable warnings, no Verilator errors | [Icarus](reports/lint.log), [Verilator](reports/verilator_lint.log) |
| RTL function | 35/35 PASS | [Log](reports/rtl_sim.log), [vectors](tb/vectors.json) |
| Final gate function | Same 35 vectors PASS; zero-delay Liberty functional models | [Log](reports/gate_sim.log), [model sources](tb/liberty_sources.json) |
| Implementation | Complete; final OpenROAD detailed-route violations=0 | [Route log](backend/RUN_2026-11-16_06-29-18_2033585_0242/logs/5_2_route.log) |
| Transistor LVS | CORRECT; property budget 65536, no exceeded warning | [Report](../calibre_evidence/multi_aes_property65536/lvs/mask.lvs.rep), [receipt](../calibre_evidence/multi_aes_property65536/lvs/invocation.json) |
| Reference audit | PASS; all 264619 MOS bindings, models, L/W/nfin preserved | [Audit](reports/reference_validation.json) |
| Full official DRC | Original five evidence runs each hit 600 s; no complete verdict | [Original receipt](../calibre_evidence/multi_aes/drc/invocation.json), [last candidate](../calibre_evidence/multi_aes_feol_hier/drc/invocation.json) |
| RTL↔gate formal | NOT PROVED within depth/time budgets; not a counterexample | [Attempts](reports/functional_validation.json), [log](reports/formal_observed.log) |

Vectors include [NIST FIPS 197 Appendix C.1](https://nvlpubs.nist.gov/nistpubs/fips/fips.197.pdf), all-zero, all-one, and 32 random cases seeded 20261116. OpenSSL AES-128-ECB independently computes expected ciphertext. This does not cover SDF simulation or exhaustive formal input-space proof.

Configuration: RVT FF Liberty, **1 ns / 1000 ps**, **64.8 × 64.8 µm die**, **62.64 × 62.64 µm core**, four threads. Reuses accepted GCD experimental TECH_LEF, SC_LEF, M1 via-in-pin, and sparse M3 PDN. Shared platform files are unchanged. [Configuration](constraints/config.mk), [PDN](constraints/pdn.tcl), [SDC](constraints/constraint.sdc).

| Metric | Value |
|---|---:|
| Standard-cell area | 1519.34 µm² |
| Standard cells / total instances | 13989 / 33659 |
| Core utilization | 38.72% |
| Setup / hold slack | +352.094 / +32.8625 ps |
| Setup / hold TNS | 0 / 0 |
| OpenSTA estimated power | 50.1289 mW |
| ORFS elapsed sum, 19 records | 863.83 s / 14 min 23.83 s |
| Wrapper wall time | 882.10 s / 14 min 42.10 s |
| Wrapper user / system CPU | 2733.69 / 32.36 s |
| Flow peak RSS | 2514.64 MiB, about 2.46 GiB |
| Final LVS wall time | 16.08 s |

[PPA](reports/ppa.json) comes from final 6_report.json with native ps timing. Power is a Liberty/STA model estimate. Stage and wrapper wall times are separate accounting bases; implementation excludes later function/formal/Calibre/geometry checks. [Resources](reports/flow_resources.json), [raw timing](reports/flow_time.txt).

Original DRC trials retain traditional flat execution of hierarchical GDS plus four representations: flattened, FIN union, FIN/GATE union, and retained hierarchy with FIN/GATE union. All four have empty per-layer Region XOR. The last also preserves all 81 cells and instance references/transforms. The same official deck, 334-check scope, and ALL result limit are retained. Each original run timed out after OPS COMPLETE=86 OF 1010; this is operation progress, not completed-rule count. A timeout is neither a clean verdict nor proof of a violation. [Last geometry proof](../multi_aes_feol_hier_input/geometry_verification.json), [original input check](../multi_aes_input/geometry_verification.json), [last log](../calibre_evidence/multi_aes_feol_hier/drc/run.log). Later partial findings are in the parent performance reports.

The initial property-budget-1024 LVS was CORRECT with an exceeded warning. Rechecking the same GDS/CDL and rules at 65536 remains CORRECT and removes the warning. The independent audit checks 264619 MOS, exhaustive input truth tables for 62 combinational types, and transistor topology for two sequential types. Omitted tap/filler cells are empty in both reference and extraction; all other connections and top ports are preserved.

[Final GDS](backend/RUN_2026-11-16_06-29-18_2033585_0242/results/6_final.gds), [final netlist](backend/RUN_2026-11-16_06-29-18_2033585_0242/results/6_final.v), [machine-readable result](RESULT.json), [evidence manifest](evidence_manifest.json).

## Additional preserved references

- [nist.fips.197.pdf](https://nvlpubs.nist.gov/nistpubs/fips/nist.fips.197.pdf)
