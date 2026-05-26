# Lessons Learned from Physical Design Debugging

## Batch Run Debugging (2026-03-26)

Debugged 20 failed designs out of 360 batch runs. All 20 fixed — **360/360 (100%) now produce GDS.** Root causes and fixes:

### 1. Placement Divergence (NesterovSolve Non-Convergence)
**Designs:** bp_multi_top_cfg3, vga_enh_top_cfg6
**Symptom:** `[NesterovSolve] Iter: 4000+ overflow: 0.25` — overflow oscillates and never drops below 0.10.
**Root cause:** `PLACE_DENSITY_LB_ADDON` set too low (0.01). The placer has no density headroom.
**Fix:** Raise `PLACE_DENSITY_LB_ADDON` to at least 0.15 (0.20-0.45 for macro-heavy designs).
**Hard rule:** Never set `PLACE_DENSITY_LB_ADDON` below 0.10 for any design. 0.10 is the minimum safe value (bp_multi_top_cfg4 passed at exactly 0.10; cfg3 diverged at 0.01).

### 2. OpenROAD SIGSEGV Crash in CTS Repair Timing
**Designs:** swerv_cfg4 (and preventively swerv_cfg5-10)
**Symptom:** `Signal 11 received` during `repair_timing` at CTS stage, stack trace shows `sta::ClkInfo::crprClkVertexId()`.
**Root cause:** OpenROAD bug triggered by complex clock trees in large macro designs (10k+ clock sinks).
**Fix:** Add `export SKIP_CTS_REPAIR_TIMING = 1` to config.mk.
**Hard rule:** All swerv/bp_multi_top-class designs (>50K instances with macros) must have `SKIP_CTS_REPAIR_TIMING=1` and `SKIP_LAST_GASP=1`.

### 3. Routing Congestion (GRT-0116)
**Designs:** wb_conmax_cfg10
**Symptom:** `[ERROR GRT-0116] Global routing finished with congestion` — 40K+ overflow.
**Root cause:** `CORE_UTILIZATION=25` too high for a bus-heavy crossbar design.
**Fix:** Reduced to `CORE_UTILIZATION=15`.
**Hard rule:** Bus-heavy interconnect designs (crossbar, arbiter matrices) need utilization ≤ 15%.

### 4. FLOW_VARIANT Directory Collision (Stale Runs)
**Designs:** 15 designs (swerv_cfg6-10, bp_multi_top_cfg4-10, vga_enh_top_cfg7-9)
**Symptom:** `mv: cannot stat '...base/X.tmp.log'` or wrong DESIGN_NAME in ORFS paths.
**Root cause:** Old `run_orfs.sh` used `FLOW_VARIANT=base` for all configs sharing a DESIGN_NAME.
**Fix:** `run_orfs.sh` now derives `FLOW_VARIANT` from project directory basename. Re-running resolved all 15.
**Hard rule:** Never run two configs with the same DESIGN_NAME and same FLOW_VARIANT simultaneously.

### 5. Stalled/Killed Backend Runs
**Designs:** swerv_cfg5
**Symptom:** flow.log ends mid-stage with no error (process killed by OOM or timeout).
**Fix:** `run_orfs.sh` now supports `ORFS_TIMEOUT` (default 2h) and `ORFS_MAX_CPUS` env vars.

### 6. Proactive Safety Flag Injection
**Designs:** bp_multi_top_cfg5/8/10, vga_enh_top_cfg9, swerv_cfg6-10
**Lesson:** Any design sharing the same RTL family as a SIGSEGV-crashing design must get the same safety flags. When swerv_cfg4 crashed in CTS repair_timing, all swerv_cfg5-10 and all bp_multi_top configs needed `SKIP_CTS_REPAIR_TIMING=1` added proactively — even if they hadn't been run yet.
**Hard rule:** When one config of a design family crashes, apply the workaround to ALL configs of that family before re-running.

### 7. PLACE_DENSITY Range for Macro Designs
**Designs:** bp_multi_top_cfg4 (PD=0.45/LB=0.10), cfg5 (PD=0.40/LB=0.15), cfg6 (PD=0.35/LB=0.15)
**Lesson:** For black_parrot (macro-heavy), `PLACE_DENSITY` can range from 0.35 to 0.60 as long as `PLACE_DENSITY_LB_ADDON ≥ 0.10`. The LB_ADDON is the critical variable, not the base density. All three configs converged successfully after raising LB_ADDON from dangerous values (0.04-0.08) to safe values (0.10-0.15).

## Extraction Script Debugging (2026-03-28 / 2026-03-30)

Audited extraction scripts against 363 designs (33 families × ~10 configs). Found and fixed 6 bugs across `extract_lvs.py`, `extract_ppa.py`, and `build_diagnosis.py`. Validated fixes against 92 LVS configs, 50 PPA configs, and 67 diagnosis configs with zero regressions.

### extract_lvs.py — False LVS-Clean on Mismatched Designs
**Affected:** riscv32i_cfg1-10 (all 10 configs falsely reported "clean")
**Root causes (3 interacting bugs):**
1. KLayout lvsdb uses `#%lvsdb-klayout` text format (not XML). The XML parser threw `ParseError` and the fallback checked for "match" — which is a substring of "mismatch", so mismatched designs were reported as clean.
2. Log parsing checked "netlists match" before "netlists don't match" — the positive pattern matched first.
3. Status logic used `mismatch_count == 0` as clean even when log explicitly said mismatch.
**Fix:** Check "mismatch"/"don't match" before "match" in both lvsdb and log parsers. Log status takes priority over mismatch count.
**Hard rule:** Always check negative patterns before positive when the positive is a substring of the negative.

### extract_ppa.py — Bogus TNS=100.0 from Flow.log Regex
**Affected:** Every design showed `setup_tns=100.0`
**Root cause:** Regex `tns\s+([-\d.]+)` matched ORFS command string `repair_tns 100` instead of actual timing data.
**Fix:** Read timing/power from `6_report.json` (ORFS authoritative report) and overwrite flow.log-parsed values. Flow.log regex is deliberately preserved as fallback for incomplete runs without `6_report.json`.
**Hard rule:** Always prefer structured data (JSON) over regex-parsing log files.

### build_diagnosis.py — 79% False Positive Rate (Round 1)
**Affected:** 276/348 designs falsely flagged as `placement_utilization_overflow`, 72/348 as `make_error`
**Root causes:**
1. Utilization check matched "utilization" and "overflow" as independent keywords anywhere in the combined text. Every ORFS flow.log contains both words in unrelated contexts (NesterovSolve overflow metrics + utilization reports).
2. Make error check matched "error" anywhere in flow.log. ORFS logs contain many harmless "error" lines (e.g., "No errors found", "error count: 0").
**Fix:** Require utilization+overflow keywords on the same line. Only check make errors in last 50 lines of flow.log section.

### build_diagnosis.py — DRC Detection Failures (Round 2)
**Affected:** 5 bp_multi_top configs falsely flagged as `drc_antenna`; fifo_cfg1 DRC violations (56) missed
**Root causes:**
1. Antenna DRC regex `(\d+)\s*violation` matched DRT routing iteration lines ("Completing 80% with 19827 violations") and independently found "antenna" from "Repair antennas..." elsewhere in the log.
2. DRC skip condition `'0 violations' in lower` matched DRT routing lines ("Completing 10% with 0 violations"), causing the DRC check to skip even when `6_drc_count.rpt` showed real violations.
3. `6_drc_count.rpt` was never read by the diagnosis engine.
**Fix:** Removed unreliable antenna text matching. Added `6_drc_count.rpt` to collected logs. DRC detection now reads the authoritative count directly from the report file.
**Hard rule:** Never use generic substring matching on combined multi-stage logs for violation detection. Use structured data sources (report files, specific log line patterns like `[INFO ANT-xxxx]`).

## Full-Pipeline Debugging (ORFS + LVS + RCX) — 2026-04-01

Ran 70 failure design cases through ORFS → LVS → RCX. Validated 7 design families (10 configs each). Final results: 5/7 families 100% all-pass; 2 families limited only by KLayout LVS timeout.

### 8. PIPESTATUS Clobbering in Shell Scripts
**Affected:** All 6 shell scripts (run_orfs.sh, run_lvs.sh, run_rcx.sh, run_drc.sh, run_magic_drc.sh, run_netgen_lvs.sh)
**Symptom:** Stage failures silently reported as success. ORFS_STATUS/LVS_STATUS always 0.
**Root cause:** `|| true` after `timeout ... | tee` pipeline resets PIPESTATUS. Under `set -euo pipefail`, `${PIPESTATUS[0]}` always returns 0 because `|| true` is the last command evaluated.
**Fix:** Wrap pipeline with `set +e +o pipefail` / `set -e -o pipefail` around PIPESTATUS capture.
**Hard rule:** Never use `|| true` after a pipeline when you need PIPESTATUS. Temporarily disable errexit instead.

### 9. SCRIPTS_DIR Environment Collision
**Affected:** run_orfs.sh, run_lvs.sh, run_drc.sh — any script calling ORFS make
**Symptom:** `make: *** No rule to make target '.../synth.sh'` — ORFS looks in wrong directory for its scripts.
**Root cause:** External `SCRIPTS_DIR` env var overrides ORFS Makefile's internal `SCRIPTS_DIR` variable.
**Fix:** `unset SCRIPTS_DIR 2>/dev/null || true` before any ORFS make invocation.

### 10. CDL_FILE Override by Platform Config
**Affected:** All macro designs (riscv32i, tinyRocket, swerv, bp_multi_top) using custom CDL files.
**Symptom:** `[ERROR ODB-0287] Master fakeram45_XXxYY was not in the masters CDL files`
**Root cause:** ORFS Makefile includes design config.mk (line ~98) before platform config.mk (via variables.mk). Platform sets `export CDL_FILE = ...NangateOpenCellLibrary.cdl`, overwriting the design's CDL_FILE.
**Fix:** Use `override export CDL_FILE = /path/to/combined.cdl` in design config.mk.
**Hard rule:** When a design needs a custom CDL_FILE, always use `override export`. Plain `export` will be silently overwritten by the platform config.

### 11. SYNTH_HIERARCHICAL Incompatible with (* blackbox *) Stubs
**Affected:** bp_multi_top configs with `SYNTH_HIERARCHICAL = 1` (7 of 10 configs)
**Symptom:** `ERROR: Missing cost information on instanced blackbox hard_mem_*_wrapper`
**Root cause:** Yosys CELLMATCH pass (used in hierarchical synthesis) needs cost info. `(* blackbox *)` modules have no .lib → no cost info.
**Fix:** Replace `(* blackbox *)` wrapper stubs with actual module implementations that instantiate the underlying fakeram macros (which have .lib files and therefore cost info).
**Hard rule:** For designs using SYNTH_HIERARCHICAL=1, never use `(* blackbox *)` on wrapper modules. Provide real implementations that instantiate library macros.

### 12. macro_placement.tcl: find_macros, not all_macros
**Affected:** bp_multi_top configs
**Symptom:** `invalid command name "all_macros"` during floorplan
**Root cause:** `all_macros` is not a valid OpenROAD command. The correct command is `find_macros`. Also, `macro_placement` requires `global_placement` to run first for initial positions.
**Fix:** Use `if {[find_macros] != ""} { global_placement ...; macro_placement -halo {10 10} ... }`.

### 13. Zombie Processes After LVS Timeout
**Affected:** bp_multi_top, swerv — any large design where LVS times out
**Symptom:** `timeout` kills the `make` process but grandchild `klayout` survives. Zombie klayout consumes 4GB memory and holds flock, blocking subsequent designs.
**Root cause:** `timeout` only sends signals to its direct child. Grandchild processes inherit the flock file descriptor but not the signal.
**Fix:** Use `setsid timeout` to create a new process group; timeout can then kill the entire group.
**Hard rule:** Always use `setsid timeout` in scripts that run long-lived subprocesses behind make.

### 14. KLayout LVS Impractical for >100K Cell Designs
**Affected:** bp_multi_top (~200K cells), swerv (~145K cells)
**Symptom:** LVS times out at both 1800s and 3600s. The "Flatten schematic circuit" phase alone takes 30-45 minutes.
**Root cause:** KLayout's flat LVS comparison scales poorly with design size. A 200K cell design takes >60 minutes for LVS, with 4GB+ memory.
**Impact:** ORFS and RCX work perfectly for all designs (100% pass rate). Only LVS is the bottleneck.
**Workaround:** Use `LVS_TIMEOUT=7200` for large designs. Accept that bp_multi_top/swerv-class designs need 90+ minutes for LVS.
**Recommendation:** For designs >100K cells, consider hierarchical LVS or accept ORFS+RCX only.

### Batch Run Results Summary (70 designs, 7 families)

| Family | Configs | ORFS | LVS | RCX | Notes |
|--------|---------|------|-----|-----|-------|
| aes_xcrypt | 10 | 100% | 100% | 100% | Timeout fix resolved all |
| ibex | 10 | 100% | 100% | 100% | Timeout fix resolved all |
| riscv32i | 10 | 100% | 100% | 100% | CDL fix resolved LVS |
| tinyRocket | 10 | 100% | 100% | 100% | CDL fix resolved LVS |
| vga_enh_top | 10 | 100% | 100% | 100% | Timeout fix resolved all |
| bp_multi_top | 10 | 100%* | 0% | 100% | *cfg2-4 failed with old stubs; LVS >60 min timeout |
| swerv | 10 | 100% | 0% | 100% | LVS >60 min timeout |
