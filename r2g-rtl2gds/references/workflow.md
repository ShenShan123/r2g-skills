# EDA Workflow (ORFS)

## Phase 0: Environment Check

1. Source the environment: `source /opt/openroad_tools_env.sh`
2. Run `scripts/flow/check_env.sh`.
3. Verify at minimum that `python3`, `yosys`, `iverilog`/`verilator`, `vvp`, and `openroad` are available.
4. Verify ORFS exists at `/opt/EDA4AI/OpenROAD-flow-scripts/flow/`.
5. If any tools are missing, stop early and report exactly which ones are absent.

## Phase 1: Intake

1. Save raw user requirements to `input/raw-spec.md`.
2. Convert them into `input/normalized-spec.yaml`.
3. Record missing fields and document assumptions.

## Phase 2: RTL Drafting

1. Generate `rtl/design.v`.
2. Keep module names aligned with `top_module`.
3. Record design intent and shortcuts in `reports/rtl-notes.md`.

## Phase 3: Verification

1. Generate `tb/testbench.v`.
2. Run lint checks first.
3. Run simulation and require an explicit pass signal in logs.
4. Save VCD waveforms if possible.

## Phase 4: Synthesis

1. Create `synth/synth.ys` synthesis script.
2. Run Yosys and collect `synth/stat.rpt`.
3. Save synthesized netlist and logs.

## Phase 5: Backend (ORFS)

1. Build `constraints/config.mk` and `constraints/constraint.sdc` for ORFS.
2. Use `assets/config-template.mk` and `assets/constraint-template.sdc` as starting points.
3. Run `scripts/flow/run_orfs.sh <project-dir> [platform]`.
4. Default platform is `nangate45`.
5. Collect results with `scripts/reports/collect_orfs_results.py`.

## Phase 5b: Timing Gate (Tiered WNS + TNS)

After backend completes, extract PPA and run the timing gate:

1. Run `scripts/extract/extract_ppa.py <project-dir> reports/ppa.json`.
2. Run `scripts/reports/check_timing.py <project-dir>`.
3. The script classifies WNS and TNS independently and takes the **worse** as the combined tier.
4. Read `reports/timing_check.json` and act on the `tier` field:
   - **clean** (WNS >= 0, TNS >= 0): Proceed to Phase 6.
   - **minor** (WNS >= -2.0 AND TNS >= -10.0): Auto-fix. Update `clk_period` in constraint.sdc to `suggested_clock_period`, re-run Phase 4+5, then re-check.
   - **moderate** (WNS >= -5.0 AND TNS >= -100.0, not clean/minor): **Stop. Present numbered options to user.** Wait for decision.
   - **severe** (WNS < -5.0 OR TNS < -100.0): **Stop. Present options with strong warning.** Recommend "stop and restructure RTL".
   - **unconstrained** (WNS > 1e+30): **Stop. SDC config error.** Do NOT proceed.
5. Check `wns_tier` and `tns_tier` in the JSON to explain which metric triggered escalation.

### config.mk required fields:
- `DESIGN_NAME` - must match top module name
- `PLATFORM` - target PDK platform (default: nangate45)
- `VERILOG_FILES` - absolute path to RTL file(s)
- `SDC_FILE` - absolute path to constraint.sdc
- `CORE_UTILIZATION` - target utilization (default: 30%)

### constraint.sdc required content:
- `current_design` matching DESIGN_NAME
- `create_clock` with correct port name and period
- `set_input_delay` and `set_output_delay`

## Phase 6: Signoff Checks (DRC / LVS / RCX)

After a successful backend run, run signoff checks in order:

### 6a. DRC (Design Rule Check)
```bash
scripts/flow/run_drc.sh <project-dir> [platform]
```
- Uses ORFS `make drc` target → KLayout with platform `.lydrc` rules
- Outputs: `drc/6_drc.lyrdb` (violation database, XML), `drc/6_drc_count.rpt` (count), `drc/6_drc.log`
- Extract: `scripts/extract/extract_drc.py <project-root> reports/drc.json`
- Result JSON contains: `status` (clean/fail), `total_violations`, `categories` (per-rule breakdown)

### 6b. LVS (Layout vs Schematic)
```bash
scripts/flow/run_lvs.sh <project-dir> [platform]
```
- Uses ORFS `make lvs` target → KLayout with platform `.lylvs` rules + CDL netlist
- **Gracefully skips** if platform has no LVS rules (e.g., asap7)
- Outputs: `lvs/6_lvs.lvsdb`, `lvs/6_lvs.log`, `lvs/6_final.cdl`
- Extract: `scripts/extract/extract_lvs.py <project-root> reports/lvs.json`
- Result JSON contains: `status` (clean/fail/skipped), `mismatch_count`, `lvsdb` details

### 6c. RCX (Parasitic Extraction via OpenRCX)
```bash
scripts/flow/run_rcx.sh <project-dir> [platform]
```
- Generates Tcl script (`rcx/run_rcx.tcl`) with OpenRCX commands:
  - `read_db` → load `6_final.odb`
  - `define_process_corner` → set extraction corner
  - `extract_parasitics` → run RC extraction with `rcx_patterns.rules`
  - `write_spef` → output SPEF file
- Runs via `openroad -no_splash -exit rcx/run_rcx.tcl`
- Outputs: `rcx/6_final.spef`, `rcx/rcx.log`, `rcx/run_rcx.tcl`
- Extract: `scripts/extract/extract_rcx.py <project-root> reports/rcx.json`
- Result JSON contains: `status` (complete/empty/skipped), `net_count`, `total_cap_ff`, `total_res_ohm`, SPEF header metadata

### Platform Support

| Platform | DRC | LVS | RCX |
|----------|-----|-----|-----|
| nangate45 | Yes | Yes | Yes |
| sky130hd | Yes | Yes | Yes |
| sky130hs | No | No | Yes |
| asap7 | Yes | No | Yes |
| gf180 | No | No | Yes |
| ihp-sg13g2 | Yes | Yes | Yes |

## Phase 7: Report Extraction

Extract all metrics into JSON for dashboard integration.
Note: `extract_ppa.py` already ran in Phase 5b. Re-run only if backend was re-run after Phase 5b.

```bash
# PPA was already extracted in Phase 5b; re-extract only if backend was re-run:
# scripts/extract/extract_ppa.py <project-root> reports/ppa.json
scripts/extract/extract_drc.py <project-root> reports/drc.json
scripts/extract/extract_lvs.py <project-root> reports/lvs.json
scripts/extract/extract_rcx.py <project-root> reports/rcx.json
scripts/extract/extract_progress.py <project-root> reports/progress.json
scripts/reports/build_diagnosis.py <project-root> reports/diagnosis.json
```

## Phase 8: Summary & Dashboard

Always produce the following:

- Overall status (pass/fail per stage including signoff)
- Artifact paths (GDS, SPEF, reports)
- Signoff results:
  - DRC: violation count and categories
  - LVS: match/mismatch/skipped
  - RCX: net count, total capacitance (fF), total resistance (Ohm)
- Assumptions made
- Blockers encountered
- Next action recommendation

Generate and serve dashboard:
```bash
scripts/dashboard/generate_multi_project_dashboard.py <design-cases-dir>
scripts/dashboard/serve_multi_project_dashboard.py 8765 <design-cases-dir>
```
