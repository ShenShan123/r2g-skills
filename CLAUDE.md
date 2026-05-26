# Agent-with-OpenROAD

AI-driven open-source EDA skill that takes a natural-language hardware spec and produces GDSII through OpenROAD-flow-scripts (ORFS), with full signoff checks (DRC, LVS, parasitic extraction).

## Project Layout

```
r2g-rtl2gds/      # The skill definition
  SKILL.md               # Skill metadata, workflow, hard rules
  scripts/               # 30 stateless Python/Shell CLIs, grouped by role:
    flow/                  #   stage runners (run_lint.sh, run_orfs.sh, run_drc.sh, …)
    extract/               #   parse tool output into JSON (extract_ppa.py, …)
    project/               #   init/normalize/validate project & spec
    reports/               #   timing gate, diagnosis, collection, history
    dashboard/             #   render GDS, generate/serve multi-project dashboard
  knowledge/             # Self-contained knowledge-store subsystem (seed data + Python code)
  references/            # Workflow guide, failure patterns, spec template, ORFS playbook, PPA guide
  assets/                # config.mk / constraint.sdc templates + simple-arbiter example
  tests/                 # pytest suite for knowledge-store scripts
tools/                   # Repo-level operator tooling (batch_run.sh, …)
design_cases/            # Working directory for all design runs (gitignored)
  <design-name>/         # One directory per design project
  _dashboard/            # Auto-generated HTML dashboard
  _batch/                # Batch-run results & summaries
```

### Knowledge Store (inside the skill)

`r2g-rtl2gds/knowledge/` is a self-contained subsystem that bundles
seed data and the Python code that reads/writes it:

```
r2g-rtl2gds/knowledge/
  README.md                # What the store contains and how it is used
  schema.sql               # SQLite DDL (tracked)
  families.json            # design_name → design_family mapping + patterns (tracked)
  knowledge_db.py          # Shared SQLite / family-inference helpers (imported by the others)
  ingest_run.py            # Ingest one design_cases/<project> dir into runs.sqlite
  learn_heuristics.py      # Derive empirical per-family bounds
  mine_rules.py            # Surface repeated failure signatures as a review queue
  query_knowledge.py       # Read-only API + CLI over heuristics.json
  suggest_config.py        # Design-aware ORFS parameter recommender (uses heuristics)
  runs.sqlite              # runtime, gitignored
  heuristics.json          # runtime, gitignored
  failure_candidates.json  # runtime, gitignored
```

Populated by `knowledge/ingest_run.py`, derived by `knowledge/learn_heuristics.py`
and `knowledge/mine_rules.py`, consumed by `knowledge/suggest_config.py` and
`knowledge/query_knowledge.py`. Phase 2 only — no version DAG yet (deferred).

## EDA Toolchain (Pre-installed)

```
source /opt/openroad_tools_env.sh   # MUST run before any EDA commands
```

| Tool | Path | Purpose |
|------|------|---------|
| OpenROAD | `/usr/bin/openroad` | Place & route, OpenRCX parasitic extraction |
| Yosys | `/opt/pdk_klayout_openroad/oss-cad-suite/bin/yosys` | Synthesis |
| iverilog/vvp | oss-cad-suite | Simulation |
| verilator | oss-cad-suite | Lint / fast simulation |
| KLayout | `/usr/bin/klayout` | GDS visualization, DRC, LVS |
| Magic | `/usr/bin/magic` | DRC, SPICE extraction (sky130) |
| Netgen | `/usr/bin/netgen-lvs` | LVS comparison (sky130) |
| OpenSTA | `/usr/local/bin/opensta` | Static timing analysis |
| ORFS | `/opt/EDA4AI/OpenROAD-flow-scripts/flow/` | Full RTL-to-GDS flow |

Available ORFS platforms: `nangate45` (default/fastest), `sky130hd`, `sky130hs`, `asap7`, `gf180`, `ihp-sg13g2`.

Sky130 PDK for Magic/Netgen: `/opt/pdks/sky130A/` (tech file + netgen setup.tcl).

## Flow Execution Order (Strict)

1. **Spec** — normalize natural-language spec → `input/normalized-spec.yaml`
2. **RTL** — generate Verilog → `rtl/design.v`
3. **Testbench** — generate testbench → `tb/testbench.v`
4. **Lint** — `scripts/flow/run_lint.sh` (must pass before simulation)
5. **Simulation** — `scripts/flow/run_sim.sh` (must pass before synthesis)
6. **Synthesis** — `scripts/flow/run_synth.sh` (must pass before backend)
7. **Backend** — `scripts/flow/run_orfs.sh <project-dir> [platform]` (ORFS place & route → GDS)
8. **Timing Gate** — `scripts/reports/check_timing.py <project-dir>` (checks both WNS and TNS; minor: auto-fix; moderate/severe: **stop and present options to user**)
9. **DRC** — `scripts/flow/run_drc.sh <project-dir> [platform]` (KLayout design rule check on GDS)
10. **LVS** — `scripts/flow/run_lvs.sh <project-dir> [platform]` (KLayout layout-vs-schematic)
11. **RCX** — `scripts/flow/run_rcx.sh <project-dir> [platform]` (OpenRCX parasitic extraction → SPEF)
12. **Reports** — `extract/extract_ppa.py`, `extract/extract_drc.py`, `extract/extract_lvs.py`, `extract/extract_rcx.py`, `reports/build_diagnosis.py`, `dashboard/generate_multi_project_dashboard.py`

**Never skip a failed stage.** Diagnose first using `references/failure-patterns.md`.

## Script Inventory

All paths below are relative to `r2g-rtl2gds/`. Everything under
`scripts/` is stateless tooling; `knowledge/` is the knowledge-store subsystem
(data + code) described in the previous section.

### `scripts/flow/` — Stage Runners (Shell)
| Script | Purpose | Inputs | Key Outputs |
|--------|---------|--------|-------------|
| `flow/check_env.sh` | Verify tool availability | — | stdout report |
| `flow/run_lint.sh` | Syntax validation (verilator/iverilog) | `<rtl-file> <log-file>` | `lint.log` with `lint_ok` marker |
| `flow/run_sim.sh` | Testbench simulation | `<rtl-file> <tb-file> <work-dir>` | `sim.log` with `simulation_ok` marker, `output.vcd` |
| `flow/run_synth.sh` | Yosys synthesis | `<rtl-file> <top-module> <work-dir>` | `synth_output.v`, `synth.log` |
| `flow/run_orfs.sh` | ORFS place & route (stage-by-stage) | `<project-dir> [platform]` | GDS, DEF, ODB in `backend/RUN_*/`, `stage_log.jsonl` |
| `flow/run_drc.sh` | KLayout DRC | `<project-dir> [platform]` | `drc/6_drc.lyrdb`, `drc/6_drc_count.rpt` |
| `flow/run_magic_drc.sh` | Magic DRC (sky130 only) | `<project-dir> [platform]` | `drc/magic_drc.rpt`, `drc/magic_drc_result.json` |
| `flow/run_lvs.sh` | KLayout LVS | `<project-dir> [platform]` | `lvs/6_lvs.lvsdb`, `lvs/6_lvs.log` |
| `flow/run_netgen_lvs.sh` | Netgen LVS (sky130 only) | `<project-dir> [platform]` | `lvs/netgen_lvs.rpt`, `lvs/netgen_lvs_result.json` |
| `flow/run_rcx.sh` | OpenRCX parasitic extraction | `<project-dir> [platform]` | `rcx/6_final.spef`, `rcx/rcx.log` |

### `scripts/extract/` — Parse Tool Output → JSON
| Script | Purpose | Inputs | Output |
|--------|---------|--------|--------|
| `extract/extract_ppa.py` | Parse PPA metrics from `6_report.json` | `<project-root> <output.json>` | `ppa.json` (area, timing, power, geometry) |
| `extract/extract_drc.py` | Parse DRC results | `<project-root> <output.json>` | `drc.json` (violation counts, categories) |
| `extract/extract_lvs.py` | Parse LVS results (XML + text format) | `<project-root> <output.json>` | `lvs.json` (match/mismatch status) |
| `extract/extract_rcx.py` | Parse SPEF parasitics | `<project-root> <output.json>` | `rcx.json` (net count, total cap/res) |
| `extract/extract_progress.py` | Parse ORFS stage progress | `<project-root> <output.json>` | `progress.json` |

### `scripts/project/` — Project Setup & Spec
| Script | Purpose | Inputs | Output |
|--------|---------|--------|--------|
| `project/init_project.py` | Initialize project directory structure | `<design-name> [base-dir]` | `design_cases/<design>/` tree |
| `project/normalize_spec.py` | Convert free-form spec → YAML | `<raw-spec.md> <out.yaml>` | `input/normalized-spec.yaml` |
| `project/validate_config.py` | SDC↔RTL port cross-check, param range validation | `<project-dir>` | stdout warnings |

### `scripts/reports/` — Diagnosis, Gates, History
| Script | Purpose | Inputs | Output |
|--------|---------|--------|--------|
| `reports/check_timing.py` | Tiered post-backend WNS+TNS gate (auto-fix minor, present options for moderate/severe) | `<project-dir> [--wns-threshold <ns>] [--tns-threshold <ns>]` | `timing_check.json` |
| `reports/build_diagnosis.py` | Multi-issue detection & suggestions | `<project-root> <output.json>` | `diagnosis.json` |
| `reports/build_run_history.py` | Multi-run comparison | `<project-root> <output.json>` | `run-history.json` |
| `reports/build_run_compare.py` | Baseline vs current delta | `<project-root> <base-json> <output.json>` | `run-compare.json` |
| `reports/collect_orfs_results.py` | Gather backend artifacts | `<project-dir>` | — |
| `reports/collect_reports.py` | List all generated artifacts | `<project-dir>` | — |
| `reports/list_artifacts.py` | Enumerate all output files | `<project-dir>` | stdout |
| `reports/summarize_run.py` | Run status summary | `<project-dir>` | stdout |
| `reports/write_success_summary.py` | Markdown run summary | `<project-dir>` | `reports/summary.md` |

### `knowledge/` — Knowledge-Store Subsystem (not under `scripts/`)
Lives alongside its seed data at `r2g-rtl2gds/knowledge/` so code and schema stay in one place.

| Script | Purpose | Inputs | Output |
|--------|---------|--------|--------|
| `knowledge/ingest_run.py` | Ingest one design_cases/<project> directory into the knowledge store | `<project-dir> [--db <path>]` | `knowledge/runs.sqlite` (upsert) |
| `knowledge/learn_heuristics.py` | Derive empirical per-family bounds from runs.sqlite | `[--db <path>] [--out <path>]` | `knowledge/heuristics.json` |
| `knowledge/query_knowledge.py` | Read-only API + CLI over heuristics.json | `family <name> \| list` | stdout JSON |
| `knowledge/mine_rules.py` | Surface repeated failure signatures as a review queue | `[--min-occurrences N]` | `knowledge/failure_candidates.json` |
| `knowledge/suggest_config.py` | Design-aware parameter recommender (uses learned heuristics) | `<project-dir> [output.json]` | Recommended ORFS parameters |

Internal module: `knowledge/knowledge_db.py` — shared SQLite/family-inference helpers imported by the five scripts above (not a standalone CLI).

### `scripts/dashboard/` — Multi-Project Dashboard
| Script | Purpose |
|--------|---------|
| `dashboard/render_gds_preview.py` | Generate GDS PNG via KLayout |
| `dashboard/generate_multi_project_dashboard.py` | Static HTML dashboard with signoff badges |
| `dashboard/serve_multi_project_dashboard.py` | HTTP server with auto-regeneration |

## ORFS Backend Key Points

- Pass `DESIGN_CONFIG` as a **make argument** (not env var) — the Makefile has a hardcoded default that would override it:
  ```bash
  make DESIGN_CONFIG=/path/to/config.mk
  ```
- ORFS stores results under a `base/` variant subdirectory: `results/<platform>/<design>/base/`
- For very small designs (< 10 cells), use explicit `DIE_AREA` / `CORE_AREA` in config.mk to avoid PDN grid errors
- config.mk must use **absolute paths** for `VERILOG_FILES` and `SDC_FILE`
- **SCRIPTS_DIR env collision**: ORFS Makefile uses `SCRIPTS_DIR` internally. Always `unset SCRIPTS_DIR` before invoking make, or external tools may break ORFS.
- **PIPESTATUS capture**: When piping `timeout ... make | tee`, use `set +e +o pipefail` around the pipeline and capture `${PIPESTATUS[0]}` immediately. Never use `|| true` after a pipeline — it clobbers PIPESTATUS and masks all failures.
- **CDL_FILE override**: ORFS includes design config.mk before platform config.mk. The platform sets its own `CDL_FILE`, overriding the design's. For macro designs, use `override export CDL_FILE = ...` to prevent this.
- **Macro designs** need: (1) Verilog stubs as real implementations (not `(* blackbox *)`), (2) CDL stubs for all macro types, (3) `macro_placement.tcl` using `find_macros`, (4) `override export CDL_FILE`, (5) `GDS_ALLOW_EMPTY = fakeram.*`
- **Process group kills for timeouts**: Use `setsid timeout ...` to ensure timeout kills the entire process tree, not just the direct child. Without this, grandchild processes (klayout, openroad) survive as zombies consuming memory.

## Signoff Checks

### DRC (Design Rule Check)

**KLayout DRC** (default, all platforms with rules):
- Uses ORFS `make drc` target which invokes KLayout with platform `.lydrc` rules
- Requires `6_final.gds` from a successful backend run
- Outputs: `drc/6_drc.lyrdb` (violation database), `drc/6_drc_count.rpt` (count), `drc/6_drc.log`
- Parse with `extract_drc.py` → `reports/drc.json`

**Magic DRC** (sky130hd, sky130hs only):
- Uses Magic's built-in DRC engine with `/opt/pdks/sky130A/libs.tech/magic/sky130A.tech`
- Runs in batch mode: `magic -dnull -noconsole -T <tech> <tcl_script>`
- Outputs: `drc/magic_drc.rpt`, `drc/magic_drc_count.rpt`, `drc/magic_drc_result.json`

### LVS (Layout vs Schematic)

**KLayout LVS** (default):
- Uses ORFS `make lvs` target which invokes KLayout with platform `.lylvs` rules
- Compares GDS layout against CDL netlist
- Requires platform LVS rule file — **gracefully skips** platforms without rules (e.g., asap7)
- Outputs: `lvs/6_lvs.lvsdb`, `lvs/6_lvs.log`
- Parse with `extract_lvs.py` → `reports/lvs.json`
- nangate45 LVS rules (`FreePDK45.lylvs`): adapted from FreePDK45_for_KLayout — device names `PMOS_VTL`/`NMOS_VTL`, `lv_pgate = pgate`, `connect_implicit("VDD"/"VSS")` for bulk merging, `schematic.purge` for unused pins, `connect_global(nwell/pwell)` for supply globals. Rule file: `/opt/EDA4AI/OpenROAD-flow-scripts/flow/platforms/nangate45/lvs/FreePDK45.lylvs`

**Netgen LVS** (sky130hd, sky130hs only):
- Two-step flow: (1) Magic extracts SPICE netlist from GDS, (2) Netgen compares against Verilog netlist
- Uses `/opt/pdks/sky130A/libs.tech/netgen/sky130A_setup.tcl` for device matching
- Outputs: `lvs/extracted.spice`, `lvs/netgen_lvs.rpt`, `lvs/netgen_lvs_result.json`

### RCX (RC Parasitic Extraction via OpenRCX)
- Uses OpenROAD's `extract_parasitics` command with platform `rcx_patterns.rules`
- Reads `6_final.odb`, writes SPEF (Standard Parasitic Exchange Format)
- Generates Tcl script (`rcx/run_rcx.tcl`) and runs via `openroad -no_splash -exit`
- Outputs: `rcx/6_final.spef`, `rcx/rcx.log`
- Parse with `extract_rcx.py` → `reports/rcx.json` (net count, total capacitance, total resistance)

### Platform Support Matrix

| Platform | KLayout DRC | KLayout LVS | Magic DRC | Netgen LVS | RCX |
|----------|-------------|-------------|-----------|------------|-----|
| nangate45 | Yes | Yes | No | No | Yes |
| sky130hd | Yes | Yes | Yes | Yes | Yes |
| sky130hs | Yes | Yes | Yes | Yes | Yes |
| asap7 | Yes | No | No | No | Yes |
| gf180 | Yes | Yes | No | No | Yes |
| ihp-sg13g2 | Yes | Yes | No | No | Yes |

## config.mk Format

```makefile
export DESIGN_NAME = <must match RTL module name exactly>
export PLATFORM    = nangate45
export VERILOG_FILES = /absolute/path/to/design.v
export SDC_FILE      = /absolute/path/to/constraint.sdc
export CORE_UTILIZATION = 30          # or use DIE_AREA/CORE_AREA for small designs
export PLACE_DENSITY_LB_ADDON = 0.20
```

## constraint.sdc Format

```tcl
current_design <top_module>
set clk_port_name clk          # must match RTL port name
set clk_period 10.0            # nanoseconds
create_clock -name core_clock -period $clk_period [get_ports $clk_port_name]
set_input_delay  [expr $clk_period * 0.2] -clock core_clock [all_inputs -no_clocks]
set_output_delay [expr $clk_period * 0.2] -clock core_clock [all_outputs]
```

## Common Pitfalls

- **PDN error on small designs**: "Insufficient width to add straps" → set `DIE_AREA = 0 0 50 50` and `CORE_AREA = 2 2 48 48`
- **PDN-0179 on large SYNTH_HIERARCHICAL designs**: `SYNTH_HIERARCHICAL=1` + `ABC_AREA=1` increases cell count, exceeding die area for PDN grid. `run_orfs.sh` now detects this and suggests fixes. For bp_multi_top, die area was increased from 1800x1800 to 2000x2000
- **Wrong design runs**: ORFS Makefile defaults to `gcd` → always pass `DESIGN_CONFIG=` as make arg
- **Clock port mismatch**: SDC `clk_port_name` must exactly match the RTL port name
- **DESIGN_NAME mismatch**: config.mk `DESIGN_NAME` must exactly match `module <name>` in Verilog
- **DRC requires GDS**: `run_drc.sh` needs `6_final.gds` from a successful ORFS run
- **LVS rule tuning**: nangate45 LVS rules were adapted for ORFS (device names `PMOS_VTL`/`NMOS_VTL`, gate layers, `connect_implicit` for VDD/VSS bulk merging). If LVS fails on a new platform, check device model names match between CDL and LVS rule file
- **Magic/Netgen only for sky130**: `run_magic_drc.sh` and `run_netgen_lvs.sh` only support sky130hd and sky130hs
- **Magic DRC requires PDK**: sky130A tech file must exist at `/opt/pdks/sky130A/libs.tech/magic/sky130A.tech`
- **RCX requires backend completion**: `run_rcx.sh` needs `6_final.odb` from a successful ORFS run
- **CDL_FILE silently overridden**: Platform config.mk overwrites design's CDL_FILE. Use `override export CDL_FILE` for macro designs
- **SYNTH_HIERARCHICAL + blackbox**: Yosys CELLMATCH pass fails on `(* blackbox *)` modules. Use actual wrapper implementations instead
- **macro_placement.tcl**: Use `find_macros` not `all_macros`. Call `global_placement` before `macro_placement`
- **LVS timeout for large designs**: KLayout LVS on >100K cell designs takes 30-60+ min. `run_lvs.sh` now auto-scales timeout based on cell count
- **Zombie processes after timeout**: Use `setsid timeout` to kill entire process group, preventing orphaned klayout/openroad processes
- **RCX Tcl script**: `run_rcx.sh` generates `rcx/run_rcx.tcl` with `define_process_corner`, `extract_parasitics`, `write_spef`
- **Severe WNS/TNS not caught**: Always run `check_timing.py` after `extract_ppa.py` and before signoff. It checks both WNS and TNS — a design with small WNS but large TNS (many violating paths) is equally problematic. Minor violations (WNS >= -2.0 AND TNS >= -10.0) are auto-fixed. Moderate/severe stop the flow with numbered options for the user.
- **Wrong top module (HLS/multi-module)**: `design_meta.json` may pick a tiny leaf module for HLS-generated or VTR benchmark files. `tools/setup_rtl_designs.py` now auto-detects the correct top via `validate_top_module()`. `tools/fix_orfs_failures.py` also detects this when PDN/density failures occur on multi-module RTL. Known cases: `koios_lenet` (→`myproject`, clock `ap_clk`), `large_mac1` (→`mac1`), `large_mac2` (→`mac2`).
- **Zero-logic designs**: Pure combinational designs like `assign out = in` synthesize to near-zero cells and cannot go through P&R. Mark as "trivial/skip" — do not retry.
- **HLS megadesigns (100K+ lines)**: Vivado-HLS / Bambu output (e.g., `koios_lenet` at 227K lines, 117 modules) scales poorly through Yosys. Synth alone can take >4h, ABC mapping >1h. Budget `ORFS_TIMEOUT=28800` (8h), run `SYNTH_HIERARCHICAL=0`, `ABC_AREA=0`; or feed a pre-synthesized gate-level netlist. Mark as "megadesign/long-run".
- **Don't lower `SYNTH_MEMORY_MAX_BITS` to fix a place-stage timeout**: If synth succeeded with `MAX_BITS=131072` and place timed out, the fix is a longer place timeout + lower density, NOT shrinking the memory budget. Dropping `MAX_BITS` below the largest inferred memory (e.g., 4096x11 = 45K-bit FIFO RAMs) causes Yosys to reject the memory entirely and fails synth, making the flow worse. `apply_timeout_fix` in `tools/fix_orfs_failures.py` is stage-aware and never drops the memory budget — it only adjusts density/utilization and signals the caller to raise `ORFS_TIMEOUT`.
- **Per-stage timeouts, not total**: `ORFS_TIMEOUT` applies to EACH stage in `run_orfs.sh` (synth, floorplan, place, cts, route, finish). Read `backend/RUN_*/stage_log.jsonl` to see which stage timed out before choosing a fix. Same-number fixes differ: synth timeout → raise timeout (or check for HLS megadesign); place timeout → raise timeout + drop density; route timeout → `FROM_STAGE=route` + `ROUTING_LAYER_ADJUSTMENT=0.10`.

## Config Tuning Quick Reference

| Design Type | CORE_UTILIZATION | PLACE_DENSITY_LB_ADDON | Safety Flags |
|-------------|-----------------|------------------------|-------------|
| Simple logic (UART, SPI) | 20-40% | 0.15-0.25 | — |
| Medium (AES, SHA, FIR) | 15-30% | 0.15-0.25 | — |
| Bus-heavy (crossbar, wb_conmax) | 10-15% | 0.15-0.25 | — |
| Large + macros (swerv, bp) | 30-40% | 0.20-0.45 | `SKIP_CTS_REPAIR_TIMING=1`, `SKIP_LAST_GASP=1` |

**Hard rules:**
- Never set `PLACE_DENSITY_LB_ADDON` below 0.10
- Never run two configs with the same DESIGN_NAME and same FLOW_VARIANT simultaneously
- When one config of a design family crashes, apply the workaround to ALL configs of that family

For detailed debugging narratives and root cause analyses, see `references/lessons-learned.md`.

## Validated Results (70 designs, 7 families, nangate45)

Tested 2026-04-01, updated 2026-04-03 with LVS auto-timeout scaling, PDN-0179 fix for bp_multi_top, and FROM_STAGE resume fix.

| Family | Configs | ORFS | LVS | RCX | All-Pass | Notes |
|--------|---------|------|-----|-----|----------|-------|
| aes_xcrypt (aes128_core) | 10 | 10/10 | 10/10 | 10/10 | **10/10** | Medium design, ~35 min/config |
| ibex (ibex_core) | 10 | 10/10 | 10/10 | 10/10 | **10/10** | Large design, ~20 min/config |
| riscv32i (riscv_top) | 10 | 10/10 | 10/10 | 10/10 | **10/10** | Macro design (fakeram45_256x32), CDL fix |
| tinyRocket (RocketTile) | 10 | 10/10 | 10/10 | 10/10 | **10/10** | Macro design (fakeram45_64x32, 1024x32), CDL fix |
| vga_enh_top | 10 | 10/10 | 10/10 | 10/10 | **10/10** | Large design with memory inference, ~60 min/config |
| swerv (swerv_wrapper) | 10 | 10/10 | 10/10 | 10/10 | **10/10** | Macro design (~145K cells). LVS ~56 min, auto-scaled to 4200s timeout |
| bp_multi_top (black_parrot) | 10 | 10/10 | 10/10 | 10/10 | **10/10** | Largest design (~200K cells). Die area increased to 2000x2000 for SYNTH_HIERARCHICAL+ABC_AREA configs; LVS auto-scaled to 7200s |

**Key takeaways:**
- **ORFS + LVS + RCX: 100% pass rate** for all 70 designs across 7 families
- LVS auto-timeout scaling: `run_lvs.sh` auto-detects cell count from `6_report.json` and scales timeout: >250K → 28800s (8h), >175K → 14400s (4h), >100K → 7200s, else 3600s. black_parrot with SYNTH_HIERARCHICAL+ABC_AREA produces ~282K cells; LVS takes 4-6 hours solo. Never run concurrent LVS for >100K cell designs.
- bp_multi_top 4 ORFS failures fixed: increased DIE_AREA from 1800x1800 to 2000x2000 for `SYNTH_HIERARCHICAL=1` + `ABC_AREA=1` configs (cfg3, cfg4, cfg6, cfg10) that exceeded PDN grid capacity
- `run_orfs.sh` now detects PDN-0179 errors and suggests fixes (increase die area, reduce density, remove SYNTH_HIERARCHICAL/ABC_AREA)

## run_orfs.sh Stage-by-Stage Execution

`run_orfs.sh` runs ORFS stages individually (`synth → floorplan → place → cts → route → finish`) with per-stage timing in `stage_log.jsonl`.

- `FROM_STAGE=<stage>`: Resume from a specific stage (skip earlier stages; clean_all is skipped to preserve prior stage results)
- `ORFS_STAGES`: Override stage list (default: `synth floorplan place cts route finish`)
- On routing failure, prints a recovery hint suggesting `ROUTING_LAYER_ADJUSTMENT=0.10`
- On floorplan PDN-0179 failure, prints diagnostic hints (increase die area, reduce density, remove SYNTH_HIERARCHICAL/ABC_AREA)

## Timeout Protection

| Script | Env Var | Default | Large designs (>100K cells) |
|--------|---------|---------|----------------------------|
| `run_orfs.sh` | `ORFS_TIMEOUT` | 7200s (2h) | Per-stage; total can be 6x |
| `run_drc.sh` | `DRC_TIMEOUT` | 3600s (1h) | Usually sufficient |
| `run_lvs.sh` | `LVS_TIMEOUT` | Auto-scaled | Auto-detects cell count: >100K→7200s, else 3600s |
| `run_rcx.sh` | `RCX_TIMEOUT` | 3600s (1h) | Usually sufficient |
| `run_magic_drc.sh` | `MAGIC_TIMEOUT` | 3600s (1h) | Usually sufficient |
| `run_netgen_lvs.sh` | `NETGEN_TIMEOUT` | 3600s (1h) | Usually sufficient |

**KLayout LVS timeout for large macro designs**: KLayout LVS scales poorly with cell count. `run_lvs.sh` now auto-scales the timeout by reading `finish__design__instance__count` from `6_report.json`:
- **<100K cells** (aes, ibex, riscv32i, tinyRocket, vga_enh_top): LVS completes in <30 min. Auto-scaled to 3600s. **100% pass rate validated.**
- **>100K cells** (swerv ~145K, bp_multi_top ~200K): LVS CPU time ~50-60 min, but wall time can exceed 90 min under concurrent load. Auto-scaled to 7200s. **Validated under concurrent load.**
- ORFS and RCX complete within default timeouts for **all** designs, including the largest.
- User can still override with explicit `LVS_TIMEOUT=<seconds>` environment variable.

```bash
# Limit to 4 CPU cores and 4-hour timeout
ORFS_MAX_CPUS=4 ORFS_TIMEOUT=14400 scripts/flow/run_orfs.sh <project-dir> [platform]
# LVS timeout auto-scales based on cell count; explicit override still works:
LVS_TIMEOUT=7200 scripts/flow/run_lvs.sh <project-dir> [platform]
```

## Batch Run Timeout Guidance

When running multiple configs sequentially per DESIGN_NAME (e.g., in a batch script with flock):
- Outer timeout must be `ORFS_TIMEOUT * 6` (6 sequential stages) not `ORFS_TIMEOUT + small_margin`
- LVS timeout auto-scales: >100K cells → 7200s, else 3600s. Can override with `LVS_TIMEOUT=<seconds>`
- Use `setsid timeout` (not bare `timeout`) to ensure the entire process group is killed, preventing zombie klayout/openroad processes that leak memory
- **Never run multiple LVS jobs concurrently for >100K cell designs.** Each KLayout LVS process consumes 3-5GB RAM. Running 2-3 simultaneously causes 2-3x wall-time inflation through memory contention (e.g., swerv LVS: 49 min standalone → 120+ min under 3x concurrency). Run LVS serially for large designs

## Development Guidelines

- Prefer editing existing scripts over creating new ones
- All scripts are in `r2g-rtl2gds/scripts/` — use them instead of ad-hoc shell commands
- The skill supports single-clock flows including macro designs (fakeram45). Escalate to user before attempting CDC, multi-clock, or DFT
- Macro designs (riscv32i, tinyRocket, swerv, bp_multi_top) are validated through ORFS+LVS+RCX on nangate45
- Dashboard is static HTML at `design_cases/_dashboard/index.html`, served via `scripts/dashboard/serve_multi_project_dashboard.py 8765`
- Dashboard embeds GDS preview images (base64), detailed geometry info, and signoff check badges (DRC/LVS/RCX) with color-coded status
- `extract_ppa.py` reads timing/power from ORFS `6_report.json` (authoritative source), with flow.log regex as fallback only when `6_report.json` is absent. Outputs `summary` (PPA) and `geometry` (detailed layout metrics) into `ppa.json`
- `extract_drc.py` parses KLayout lyrdb XML for violation categories and counts
- `extract_lvs.py` handles both XML and non-XML `#%lvsdb-klayout` text format. Checks negative patterns ("don't match") before positive ("match") to avoid substring false matches. Log status takes priority over lvsdb mismatch count
- `extract_rcx.py` parses SPEF for net count, total capacitance (fF), and total resistance (Ohm)
- `build_diagnosis.py` reads `6_drc_count.rpt` for authoritative DRC violation count. Checks utilization overflow keywords on same line (not independently). Only checks make errors in last 50 lines of flow.log

## Extraction Script Design Rules

These rules were learned from debugging extraction scripts against 363 designs. Full details in `references/lessons-learned.md`.

- Always check negative patterns before positive when the positive is a substring of the negative (e.g., check "mismatch" before "match")
- Always prefer structured data (JSON/report files) over regex-parsing log files
- Never use generic substring matching on combined multi-stage logs for violation detection
