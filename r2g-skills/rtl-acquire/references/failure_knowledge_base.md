# Failure Knowledge Base

This file captures recurring failure modes seen while expanding the Nangate45 raw graph dataset and the preferred repair strategy.

## 1. `SYNTH_MEMORY_MAX_BITS` failures

Typical log signature:

- `Error: Synthesized memory size 4096 exceeds SYNTH_MEMORY_MAX_BITS`

Primary cause:

- the design is memory-heavy and ORFS defaults to `4096`
- the candidate/variant name may no longer match the whitelist
- retry may reuse stale partial config/index artifacts

Repair path:

1. match memory-limit overrides on `base_design`, not only the fully suffixed variant name
2. write `export SYNTH_MEMORY_MAX_BITS = <n>` into generated config
3. also pass `SYNTH_MEMORY_MAX_BITS=<n>` as a make override
4. also inject `SYNTH_MEMORY_MAX_BITS` into the subprocess environment
5. regenerate a scoped retry CSV after the flow patch

Notes:

- area variants such as `foo__area1` must be normalized back to `foo`
- if the generated `config.mk` does not contain `SYNTH_MEMORY_MAX_BITS`, the whitelist did not match

Known high-value families that often need this:

- `wb2axip_*`
- `wbscope_*`
- `zipcpu_*`
- larger `verilog_axis_*`
- larger `verilog_ethernet_*`

## 2. Missing bundled RTL dependency

Typical log signature:

- `Module \`axis_async_fifo' referenced in module ... is not part of the design`
- other missing module / unresolved hierarchy errors

Primary cause:

- retry CSV lost `rtl_files`
- multi-file canonical bundle was collapsed back to a single `source_path`

Repair path:

1. rebuild retry candidates from canonical `design_meta.json`
2. preserve `rtl_files` and `include_dirs`
3. if the missing module cannot be found, auto-generate a blackbox-style stub module with inout ports and retry
4. avoid retrying bundle-heavy designs from a stripped CSV

## 3. Helper collision / `$abstract\\dff` redefinition

Typical log signature:

- `ERROR: Re-definition of module \`$abstract\\dff'`

Primary cause:

- helper files were generated twice and both were fed into Yosys

Current policy:

- for low-value ISCAS helper-collision cases, skip
- do not spend high-value retry budget on these unless the benchmark family matters to the dataset objective

## 4. Partial index after retry

Typical symptom:

- `index.csv` suddenly has fewer rows than the output root actually contains

Primary cause:

- scoped retry overwrote the index with only retry-wave rows

Repair path:

1. rebuild the root index from actual directories using `rebuild_external_index_from_dirs.py`
2. only then regenerate merged manifest / summary

## 5. Mapping drift / legacy `UNKNOWN=95`

Typical symptom:

- `.v` contains real Nangate45 cells, but graphs still collapse them to label `95`

Repair path:

1. expand `mapping.txt` with dedicated labels
2. re-run graph repair for affected designs
3. refresh `cell_stats.json` and `graph_schema_version`

## 6. Net/stub label collision after mapping expansion

Typical symptom:

- gate labels overlap with special IDs previously reserved for `net` / `boundary_stub`

Repair path:

1. do not hardcode `net_id=96` / `boundary_stub_id=97`
2. derive them dynamically from `max_gate_label + 1/+2`
3. rebuild downstream partition/skeleton datasets after mapping expansion

## 7. Missing include/config header in bundle-aware SV projects

Typical log signature:

- `ERROR: Can't open include file \`config.sv'!`

Primary cause:

- candidate discovery preserved the main `.sv` source, but not the required include/config header
- `include_dirs` alone is not enough if the header file is outside the inferred bundle or was not copied into retry metadata

Repair path:

1. preserve both `include_dirs` and the required config/header files in `rtl_files`
2. if the project is front-end heavy or depends on generated headers, do not spend closed-loop budget on leaf submodules
3. prefer top-level bundle candidates over isolated pipeline/datapath leaf modules for these repos
4. auto-fix executor now searches the repo for the missing include and adds its directory to the retry candidate when possible

## 8. Long-tail canonicalize / memory-replacement stall in parametric LFSR designs

Typical symptom:

- status log loops on repeated `Warning: Replacing memory ... with list of registers`
- output root only reaches `src_manifest.txt` or partial synth artifacts
- parent orchestrator stays alive with `CPU=0` and no child process

Primary cause:

- highly parametric LFSR/mask logic expands into a long Yosys canonicalize + memory-replacement path
- the design is usually not valuable enough to justify blocking a medium-focused closed loop

Current policy:

- mark these as long-tail and add them to `failed_candidates_exclude.csv`
- do not let them block the main closed-loop round
- only revisit them in a separate scoped experiment if the family becomes strategically important

## 9. Non-synthesizable multi-edge event control

Typical log signature:

- `ERROR: Multiple edge sensitive events found for this signal!`

Primary cause:

- the RTL encodes mixed-edge or otherwise unsupported event control
- this is usually a semantic/frontend incompatibility, not a missing dependency

Current policy:

- treat as low-value deterministic exclude for the closed-loop expander
- do not spend retry budget on these unless the family becomes strategically important

## 10. Invalid named-port instantiation

Typical log signature:

- `ERROR: Module \`foo' referenced in module \`top' ... does not have a port named 'bar'.`

Primary cause:

- the benchmark is semantically invalid or intentionally crafted as a parser/regression failure
- this is not fixable by include recovery, stub injection, or frontend conversion

Current policy:

- classify as deterministic exclude
- keep as knowledge-base evidence, but do not keep retrying in the main closed loop

## 11. Output port connected to constants

Typical log signature:

- `ERROR: Output port ... is connected to constants: 1'0`

Primary cause:

- the RTL violates synthesizable connection semantics
- common in invalid or edge-case regression benchmarks

Current policy:

- classify as deterministic exclude
- avoid wasting retry budget on broad frontend/LLM patch attempts

## 12. Conflicting-driver / check-assert structural failures

Typical log signature:

- `Warning: multiple conflicting drivers for ...`
- `ERROR: Found 1 problems in 'check -assert'.`

Primary cause:

- real structural driver conflicts or unsupported DDR/ODDR-style constructions after lowering
- these are generally not repairable by lightweight deterministic transforms

Current policy:

- classify as deterministic exclude in the closed-loop expander
- revisit only if a benchmark family is strategically important enough to justify manual/LLM intervention

## 13. Repo search backend transient network failure

Typical log signature:

- `ssl.SSLEOFError: [SSL: UNEXPECTED_EOF_WHILE_READING]`
- `urllib.error.URLError: <urlopen error ...>`
- query failure from one backend such as Gitee while GitHub/GitLab may still be usable

Primary cause:

- remote search backend TLS/network instability
- rate limiting or transient backend outage
- not an RTL, synthesis, or graph-conversion failure

Current policy:

- do not fail the entire acquire stage because one backend query fails
- record `backend`, `keyword`, `page`, and error string in the candidate report
- continue searching other backends/keywords and retry the failed backend in later rounds only after useful progress is exhausted
- if one backend repeatedly fails and blocks throughput, run a scoped GitHub/GitLab-only acquisition wave instead of reducing synthesis/validation gates

## 14. Repo clone/download stall

Typical symptom:

- `clone_repo_manifest.py` is alive but only a single `git clone` child remains for many minutes
- parent `search_and_expand_until_target.py` shows no new logs after entering clone stage
- CPU usage stays near zero while no clone summary is written

Primary cause:

- large repository over slow network
- remote endpoint stalls mid-transfer
- shallow clone is still too slow for closed-loop expansion

Current policy:

- use a bounded clone/download timeout instead of letting one repository block a full round
- clean partial destination directories after clone failure or timeout
- keep the failed repo in the clone summary with the timeout reason so later acquisition can retry or demote it
- prefer many medium-quality repositories over a single long-tail monorepo when testing dataset expansion boundaries
- closed-loop search should skip repositories that already timed out in recent `*_clone_summary.csv` files, otherwise the highest-star slow repos repeatedly consume the front of every batch

## 15. ORFS flow-dir/root mismatch

Typical log signature:

- `can't open file '$HOME/work/openroad/OpenROAD-flow-scripts/util/inspect_30pt_schema.py'`
- expand command was launched with `--flow-dir $HOME/work/openroad/OpenROAD-flow-scripts`

Primary cause:

- `expand_external_benchmark_dataset.py` expects the ORFS `flow/` directory, not the repository root
- `N45_GE_FLOW_DIR` was set to `.../OpenROAD-flow-scripts` instead of `.../OpenROAD-flow-scripts/flow`

Current policy:

- set `N45_GE_FLOW_DIR=$HOME/work/openroad/OpenROAD-flow-scripts/flow`
- treat this as an environment/config failure, not an RTL candidate failure
- after fixing the path, rerun the expansion round from Acquire/Expand so clone summaries and candidate manifests stay consistent

## 16. FPGA vendor PLL primitive missing (`altpll`)

Typical log signature:

- `ERROR: Module \`\\altpll' referenced ... is not part of the design.`
- commonly appears in FPGA example top modules from USB/device repositories

Primary cause:

- RTL instantiates an Altera/Cyclone `altpll` vendor primitive that is not part of the open-source RTL bundle
- the useful protocol core is often still synthesizable if the clock-generation primitive is stubbed deterministically

Current policy:

- add a narrow deterministic `altpll` helper only when source text references `altpll` and no `module altpll` exists
- model it as a clock/pass-through helper: `clk = {5{inclk[0]}}`, `locked = 1'b1`
- treat this as a vendor primitive fallback, not proof of exact FPGA clocking semantics
- keep later publish validation/quality checks active; do not bypass duplicate, mapping, or publish gates

## 17. Closed-loop repo scoring CLI drift

Typical log signature:

- `score_download_repos.py: error: unrecognized arguments: --min-repo-success ... --max-fail-ratio ...`
- expansion, repair, validate, and publish stages complete, then the search loop exits before the next acquisition round

Primary cause:

- `search_and_expand_until_target.py` forwards repo-quality thresholds that older `score_download_repos.py` did not expose as CLI options

Current policy:

- keep the thresholds in the closed-loop search command
- make `score_download_repos.py` accept and apply the threshold arguments
- classify this as orchestration CLI drift, not candidate failure

## 18. ORFS per-design work-dir disk exhaustion

Typical log signature:

- `OSError: [Errno 28] No space left on device`
- `bash: line 1: echo: write error: No space left on device`
- failure occurs while writing ORFS `results/nangate45/<design>/base/clock_period.txt`, `index.csv`, or status JSON

Primary cause:

- closed-loop expansion leaves reproducible ORFS `results/`, `logs/`, `reports/`, and `objects/` directories for every attempted design
- these intermediates are not the canonical published dataset artifacts and can grow to many GB across rounds

Current policy:

- preserve canonical artifacts under `external_benchmarks_nangate45_expand/<design>/`
- after each candidate finishes, remove per-design ORFS work dirs under `flow/{results,logs,reports,objects}/nangate45/<design>`
- if the filesystem is already full, first clear existing ORFS Nangate45 work dirs before restarting the loop

<!-- AUTO-GENERATED FAILURE PATTERNS START -->
## Auto-Discovered Patterns

This section is refreshed automatically from `failure_knowledge_base_candidates.csv` after each expansion round.
It is intended to keep the formal knowledge base in sync with recurring failures without discarding the curated manual entries above.

### A1. `memory_limit`

- recurring_count: `28`
- signature: `synthesized memory size 4096 exceeds synth_memory_max_bits`
- top_families: `verilog-pcie:7; FPGA-USB-Device:3; secworks_sha512:3; FPGA-UART-RETRY:2`
- example_design: `core_audio_top`
- suggested_repair: raise or propagate SYNTH_MEMORY_MAX_BITS and retry scoped high-value designs

### A2. `undefined_macro`

- recurring_count: `22`
- signature: `unimplemented compiler directive or undefined macro`
- top_families: `vtr-verilog-to-routing-min:16; NanoCore:3; nano-cpu32k:2; catena-riscv32-fpga:1`
- example_design: `catena_riscv32_fpga_hw_src_lib_riscv32i_v6T_alu`
- suggested_repair: exclude placeholder/preprocessor-heavy RTL unless worth custom front-end handling

### A3. `missing_include`

- recurring_count: `8`
- signature: `can't open include file `config.sv'!`
- top_families: `riscv-simple-sv:8`
- example_design: `riscv_simple_sv_core_multicycle_multicycle_ctlpath`
- suggested_repair: preserve include_dirs and required config/header files in bundle-aware candidates or skip front-end-heavy designs

### A4. `missing_include`

- recurring_count: `6`
- signature: `can't open include file `common_cells/assertions.svh'!`
- top_families: `pulp_common_cells:4; pulp_axi:2`
- example_design: `pulp_axi_src_axi_demux_simple`
- suggested_repair: preserve include_dirs and required config/header files in bundle-aware candidates or skip front-end-heavy designs

### A5. `unclassified`

- recurring_count: `6`
- signature: `error: found 1 problems in 'check -assert'.`
- top_families: `vtr-verilog-to-routing-min:4; _tmp_cfg:1; picorv32_Xilinx:1`
- example_design: `apb_spi_core_rtl_spi_core`
- suggested_repair: manual review needed

### A6. `missing_include`

- recurring_count: `5`
- signature: `can't open include file `common_cells/registers.svh'!`
- top_families: `pulp_common_cells:3; cv32e40p:2`
- example_design: `cv32e40p_rtl_vendor_pulp_platform_common_cells_src_cdc_fifo_gray`
- suggested_repair: preserve include_dirs and required config/header files in bundle-aware candidates or skip front-end-heavy designs

### A7. `generic_synth_failed`

- recurring_count: `4`
- signature: `synthesis_failed`
- top_families: `wb2axip:2; DMA:1; RISC-V-RV32I:1`
- example_design: `DMA_final_code`
- suggested_repair: inspect synth.log and classify before retry

### A8. `graph_empty`

- recurring_count: `4`
- signature: `no graph nodes were created from mapped netlist`
- top_families: `vtr-verilog-to-routing-min:3; 8-bit-Microcontroller_Verilog:1`
- example_design: `8_bit_Microcontroller_Verilog_Microcontroller`
- suggested_repair: check mapped netlist validity and skip trivial/invalid designs

### A9. `unclassified`

- recurring_count: `4`
- signature: `error: multiple edge sensitive events found for this signal!`
- top_families: `vtr-verilog-to-routing-min:3; ultraembedded-cores:1`
- example_design: `ultraembedded_i2s`
- suggested_repair: manual review needed

### A10. `unclassified`

- recurring_count: `3`
- signature: `/home/yuany/work/_downloads/rv32i/plic.v:19: error: syntax error, unexpected tok_priority, expecting tok_id or '#' or '['`
- top_families: `RV32I:3`
- example_design: `RV32I_Gensys_top`
- suggested_repair: manual review needed

### A11. `unclassified`

- recurring_count: `3`
- signature: `unknown_failure`
- top_families: `unknown:3`
- example_design: `FPGA_USB_Device_RTL_usb_class_usb_camera_top`
- suggested_repair: manual review needed

### A12. `missing_include`

- recurring_count: `2`
- signature: `can't open include file `axi/assign.svh'!`
- top_families: `pulp_axi:2`
- example_design: `pulp_axi_src_axi_id_serialize`
- suggested_repair: preserve include_dirs and required config/header files in bundle-aware candidates or skip front-end-heavy designs

### A13. `missing_include`

- recurring_count: `2`
- signature: `can't open include file `axi/typedef.svh'!`
- top_families: `pulp_axi:2`
- example_design: `pulp_axi_src_axi_burst_splitter_gran`
- suggested_repair: preserve include_dirs and required config/header files in bundle-aware candidates or skip front-end-heavy designs

### A14. `missing_include`

- recurring_count: `2`
- signature: `can't open include file `prim_assert.sv'!`
- top_families: `ibex:1; RISCV-design:1`
- example_design: `ibex_rtl_ibex_id_stage`
- suggested_repair: preserve include_dirs and required config/header files in bundle-aware candidates or skip front-end-heavy designs

### A15. `missing_module`

- recurring_count: `2`
- signature: `module `\asyncfifo_4096x8' referenced in module `\uart_ctrl' in cell `\data_tx' is not part of the design`
- top_families: `_tmp_cfg:2`
- example_design: `NanoCore_src_peripherals_part_uart_part_UART_Ctrl`
- suggested_repair: preserve canonical rtl_files/include_dirs bundle and retry

### A16. `missing_module`

- recurring_count: `2`
- signature: `module `\dpsram' referenced in module `$paramod\cluster\num_cores=s32'00000000000000000000000000010000' in cell `\global_memory' is not part of the design`
- top_families: `_tmp_cfg:2`
- example_design: `PASC_rtl_core_axi_interface`
- suggested_repair: preserve canonical rtl_files/include_dirs bundle and retry

### A17. `missing_module`

- recurring_count: `2`
- signature: `module `\dual_port_ram' referenced in module `\memcmd_fifo' in cell `\ram_addr' is not part of the design`
- top_families: `verilog:2`
- example_design: `LU32PEEng`
- suggested_repair: preserve canonical rtl_files/include_dirs bundle and retry

### A18. `missing_module`

- recurring_count: `2`
- signature: `module `\eth_receivecontrol' referenced in module `\eth_maccontrol' in cell `\receivecontrol1' is not part of the design`
- top_families: `ethmac_freecores:2`
- example_design: `ethmac_freecores_rtl_verilog_eth_maccontrol`
- suggested_repair: preserve canonical rtl_files/include_dirs bundle and retry

### A19. `missing_module`

- recurring_count: `2`
- signature: `module `\omsp_alu' referenced in module `\omsp_execution_unit' in cell `\alu_0' is not part of the design`
- top_families: `openmsp430:1; _tmp_cfg:1`
- example_design: `openmsp430_fpga_altera_de0_nano_soc_rtl_verilog_openmsp430_openMSP430`
- suggested_repair: preserve canonical rtl_files/include_dirs bundle and retry

### A20. `unclassified`

- recurring_count: `2`
- signature: `/home/yuany/work/_downloads/openmsp430/core/rtl/verilog/openmsp430_defines.v:881: error: syntax error, unexpected tok_id`
- top_families: `openmsp430:2`
- example_design: `openmsp430_fpga_altera_de0_nano_soc_rtl_verilog_openMSP430_fpga`
- suggested_repair: manual review needed

### A21. `unclassified`

- recurring_count: `2`
- signature: `error: found 12 problems in 'check -assert'.`
- top_families: `_tmp_cfg:1; vtr-verilog-to-routing-min:1`
- example_design: `Verilog_Microcontroller_alarm_clock`
- suggested_repair: manual review needed

### A22. `unclassified`

- recurring_count: `2`
- signature: `error: module `and2' referenced in module `top' in cell `a1' does not have a port named 'in3'.`
- top_families: `vtr-verilog-to-routing-min:2`
- example_design: `vtr_verilog_to_routing_min_odin_ii_regression_test_benchmark_verilog_syntax_instantiating_by_name_invalid`
- suggested_repair: manual review needed
<!-- AUTO-GENERATED FAILURE PATTERNS END -->

<!-- CORE SIGNATURES START -->
## Core Failure Signatures

This section is refreshed automatically from `failure_signatures.json`.
It promotes high-frequency failure fingerprints to core entries.

### C1. `0eec83a7df10d46d9602be1f6f3e9455`

- count: `21`
- example_design: `vtr_verilog_to_routing_min_odin_ii_regression_test_benchmark_verilog_keywords_module_endmodule_repeated_module_failure`
- example_notes: `1. Executing Verilog-2005 frontend: /home/yuany/work/_downloads/vtr-verilog-to-routing-min/odin_ii/regression_test/benchmark/verilog/keywords/module_endmodule/repeated_module_failure.v | /home/yuany/w`

### C2. `c4ce932204dbac30a0794b984a9bef39`

- count: `17`
- example_design: `FPGA_UART_RETRY_RTL_uart2axi4`
- example_notes: `5. Executing HIERARCHY pass (managing design hierarchy). | 6. Executing AST frontend in derive mode using pre-parsed AST for module `\uart2axi4'. | 6.1. Analyzing design hierarchy.. | 6.2. Executing A`

### C3. `6b7449dc45eaa9f8f1091cbed89cd4dc`

- count: `12`
- example_design: `aricriscv_aricriscv_pipe_rtl_exe`
- example_notes: `1. Executing Verilog-2005 frontend: /home/yuany/work/data/external_benchmarks_nangate45_expand/_tmp_cfg/aricriscv_aricriscv_pipe_rtl_exe_sv2v.v | /home/yuany/work/data/external_benchmarks_nangate45_ex`

### C4. `da463d8e5a73b4896504b10eb5ad17b0`

- count: `10`
- example_design: `ACC_hw_roadmap_06_rom_test_1_romtest1`
- example_notes: `1. Executing Verilog-2005 frontend: /home/yuany/work/data/external_benchmarks_nangate45_expand/_tmp_cfg/ACC_hw_roadmap_06_rom_test_1_romtest1_sv2v.v | /home/yuany/work/data/external_benchmarks_nangate`

### C5. `6f80b99d6615d866c697dff78d5cfcff`

- count: `10`
- example_design: `ACC_hw_roadmap_11_ACC1_ACC1`
- example_notes: `1. Executing Verilog-2005 frontend: /home/yuany/work/data/external_benchmarks_nangate45_expand/_tmp_cfg/ACC_hw_roadmap_11_ACC1_ACC1_sv2v.v | /home/yuany/work/data/external_benchmarks_nangate45_expand/`

### C6. `b0cdcf0eba34f03bcb505f6077bd3803`

- count: `9`
- example_design: `AES128_RTLsynthesis_table`
- example_notes: `1. Executing Verilog-2005 frontend: /home/yuany/work/data/external_benchmarks_nangate45_expand/_tmp_cfg/AES128_RTLsynthesis_table_sv2v.v | /home/yuany/work/data/external_benchmarks_nangate45_expand/_t`

### C7. `673cf4c14b1e66225f07929e10d086e4`

- count: `9`
- example_design: `RV32i_Verilog_src_core_pulp_core_execution_unit_core_execution_unit`
- example_notes: `1. Executing Verilog-2005 frontend: /home/yuany/work/data/external_benchmarks_nangate45_expand/_tmp_cfg/RV32i_Verilog_src_core_pulp_core_execution_unit_core_execution_unit_sv2v.v | /home/yuany/work/da`

### C8. `6b82258a3adec5d138daa5d33a3daa6d`

- count: `8`
- example_design: `verilog_can_can_top`
- example_notes: `1. Executing Verilog-2005 frontend: /home/yuany/work/_downloads/verilog-can/can_top.v | Warning: Encountered `full_case' comment! Such legacy hot comments are supported by Yosys, but are not part of a`

### C9. `aa1355ff04f08343099aea59410d26a8`

- count: `8`
- example_design: `ACC_hw_roadmap_07_rom_test_2_romtest2`
- example_notes: `1. Executing Verilog-2005 frontend: /home/yuany/work/data/external_benchmarks_nangate45_expand/_tmp_cfg/ACC_hw_roadmap_07_rom_test_2_romtest2_sv2v.v | /home/yuany/work/data/external_benchmarks_nangate`

### C10. `bd640e0458cb83398b6e6c6c76e3d98a`

- count: `7`
- example_design: `Riscy_SoC_rtl_cpu_decode`
- example_notes: `1. Executing Verilog-2005 frontend: /home/yuany/work/data/external_benchmarks_nangate45_expand/_tmp_cfg/Riscy_SoC_rtl_cpu_decode_sv2v.v | /home/yuany/work/data/external_benchmarks_nangate45_expand/_tm`

### C11. `e2afb7a4ef25bf13774406697e90f87b`

- count: `7`
- example_design: `ACC_hw_roadmap_12_ACC2_ACC2`
- example_notes: `1. Executing Verilog-2005 frontend: /home/yuany/work/data/external_benchmarks_nangate45_expand/_tmp_cfg/ACC_hw_roadmap_12_ACC2_ACC2_sv2v.v | /home/yuany/work/data/external_benchmarks_nangate45_expand/`

### C12. `a4e943ee0b5f6bc3c4e80c83bdc0310c`

- count: `7`
- example_design: `AES128_RTLsynthesis_datapath_pads`
- example_notes: `1. Executing Verilog-2005 frontend: /home/yuany/work/data/external_benchmarks_nangate45_expand/_tmp_cfg/AES128_RTLsynthesis_datapath_pads_sv2v.v | /home/yuany/work/data/external_benchmarks_nangate45_e`

### C13. `88566c7fe1b2d4ef2726daa38f01e016`

- count: `7`
- example_design: `verilog_can_can_bsp`
- example_notes: `1. Executing Verilog-2005 frontend: /home/yuany/work/_downloads/verilog-can/can_bsp.v | Warning: Encountered `parallel_case' comment! Such legacy hot comments are supported by Yosys, but are not part `

### C14. `51dd92824d4a3d1a25b9b994a6ea4934`

- count: `6`
- example_design: `RV32i_Verilog_src_core_core`
- example_notes: `1. Executing Verilog-2005 frontend: /home/yuany/work/data/external_benchmarks_nangate45_expand/_tmp_cfg/RV32i_Verilog_src_core_core_sv2v.v | /home/yuany/work/data/external_benchmarks_nangate45_expand/`

### C15. `b44e14febe06ae20db8a40a87b926cad`

- count: `5`
- example_design: `ACC_hw_roadmap_05_click_counter2_click_counter2`
- example_notes: `1. Executing Verilog-2005 frontend: /home/yuany/work/data/external_benchmarks_nangate45_expand/_tmp_cfg/ACC_hw_roadmap_05_click_counter2_click_counter2_sv2v.v | /home/yuany/work/data/external_benchmar`

### C16. `1e8864c610dd3ecf0438144d5feaed56`

- count: `5`
- example_design: `SpinalTetris_design_SOC_tetris_top_rtl_tetris_top`
- example_notes: `1. Executing Verilog-2005 frontend: /home/yuany/work/data/external_benchmarks_nangate45_expand/_tmp_cfg/SpinalTetris_design_SOC_tetris_top_rtl_tetris_top_sv2v.v | /home/yuany/work/data/external_benchm`
<!-- CORE SIGNATURES END -->

<!-- LLM PATCH RULE CANDIDATES START -->
## LLM Patch Rule Candidates

This section is refreshed automatically from `llm_patch_rule_candidates.json`.
These entries are mined from validated successful LLM patch runs and should be treated as promotion candidates, not unconditional rules.

### L1. `replace_procedural_break_with_flag`

- support_count: `1`
- design: `R2FFT_hdl_butterflyCore`
- failure_class: ``
- next_best_action: ``
- symptom_regex_hint: `ERROR:.*break|unsupported.*break`
- suggested_repair: Replaced the unsupported `break` in `bfp_bitWidthDetector.sv` with a synthesizable priority-flag loop so the parser/frontend can accept the design without changing behavior.

### L2. `sanitize_macro_definition`

- support_count: `1`
- design: `adder_tree`
- failure_class: ``
- next_best_action: ``
- symptom_regex_hint: `ERROR:.*macro definition|Invalid name for macro definition`
- suggested_repair: Replaced the illegal template placeholders in `adder_tree.v` with the concrete known-good 3-level, 28-bit variant so the frontend can parse the file cleanly without changing the adder-tree structure.
<!-- LLM PATCH RULE CANDIDATES END -->

## Manual Strategy Notes

### M1. VTR invalid regression parser benchmarks
- Signature family: `vtr-verilog-to-routing-min/odin_ii/regression_test/benchmark/verilog/(keywords|preprocessor|syntax)` with parser failures.
- Observed on `automatic_recursive_task`, `multiple_defaults_failure`, `missing_endmodule_failure`, `while_loop`, and similar benchmark names intentionally probing parser edge cases.
- Policy: deterministic `exclude`; these are parser-regression artifacts, not dataset-quality repair targets.

### M2. Non-constant procedural loop bounds
- Signature: `2nd expression of procedural for-loop is not constant`, `procedural for-loop is not constant`.
- Observed on `fpu_floating_addition` and `i2c_eeprom_src_24FC512`.
- Policy: targeted `rewrite_nonconstant_procedural_loop`; this is the correct bucket for conservative frontend rewrites that preserve intent while removing unsupported loop conditions.

### M3. Unresolved simulation-only system tasks
- Signature: `Can't resolve task name \`$error'`, `Can't resolve task name \`$display'`.
- Observed on `verilog_axi_axi_vfifo` and related simulation-guarded RTL.
- Policy: `sanitize_simulation_system_tasks`; remove or neutralize simulation-only tasks before retry, then re-run normal synthesis checks.

### M4. Instance-array dependency under-collection
- Signature: `Module \`<name>' referenced in module ... is not part of the design` when the missing module exists elsewhere in the repo.
- Observed on `core-design` candidates using instance arrays such as `mux u_mux [31:0] (...)`.
- Root cause: candidate discovery must recognize parameterized instance arrays when building RTL bundles; otherwise helper RTL is omitted before Yosys.
- Policy: deterministic discovery fix; the dependency regex must support optional instance array dimensions before the port list, and the discovery scan-state schema must be bumped after changing dependency extraction.

### M5. Duplicate module definitions in generated RTL
- Signature: `ERROR: Re-definition of module` or `$abstract\<module>`.
- Observed on generated/flattened AES RTL bundles with repeated module definitions.
- Root cause: generated benchmark files can contain duplicate module blocks that are not meaningful standalone synthesis candidates.
- Policy: deterministic candidate filter; strip comments before module extraction and reject files with duplicate module definitions during candidate discovery rather than sending them into Yosys.
