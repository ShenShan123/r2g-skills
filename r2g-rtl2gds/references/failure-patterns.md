# Failure Patterns

## Specification Gaps

**Symptoms:**
- Unclear module boundaries
- No clock/reset definition
- Contradictory IO behavior

**Action:**
- Stop and ask the user, or record explicit assumptions before continuing

## RTL Issues

**Symptoms:**
- Syntax errors
- Undeclared signals
- Width mismatch warnings
- Unintended latch inference

**Action:**
- Fix RTL first
- Avoid changing the testbench unless failure points to a testbench mismatch

## Testbench Issues

**Symptoms:**
- Compilation succeeds but runtime checks fail unexpectedly
- No waveform dump generated
- Reset/clock generation missing

**Action:**
- Inspect testbench expectations
- Verify clock/reset sequencing
- Add clearer self-checks and logging

## Synthesis Issues

**Symptoms:**
- Hierarchy/top module not found
- Unsupported constructs
- Blackbox or missing module errors

**Action:**
- Verify top module name
- Flatten dependencies
- Replace unsupported simulation-only code with synthesizable alternatives

## ORFS Backend Issues

**Symptoms:**
- Make target fails early
- Floorplan error (core area too small)
- Placement utilization exceeds target
- Routing congestion or DRC violations
- No final GDS generated
- Clock port not found

**Action:**
- Check config.mk: DESIGN_NAME must match RTL top module exactly
- Check constraint.sdc: clock port name must match RTL port name
- If utilization is too high, reduce `CORE_UTILIZATION` in config.mk
- If core area too small, the design may be too large for default settings
- Check flow.log for the specific failing ORFS stage
- Only consider RTL restructuring after confirming configuration is correct

## Routing Congestion (GRT-0116)

**Symptoms:**
- `[ERROR GRT-0116] Global routing finished with congestion`
- Final congestion report shows large total overflow (e.g., > 1000)
- Flow fails at `5_1_grt` stage

**Root Cause:**
CORE_UTILIZATION is too high for the design's routing complexity. Highly interconnected designs (e.g., bus crossbars, interconnect fabrics) need lower utilization to leave room for routing channels.

**Action:**
- Reduce `CORE_UTILIZATION` by 30-50% (e.g., 25 → 15)
- Compare with successful configs of the same design for a known-good utilization range
- As a rule of thumb, bus-heavy designs (wb_conmax, crossbars) need utilization ≤ 15%
- If the design uses `SYNTH_HIERARCHICAL=1`, the gate count may be larger than expected

## Placement Divergence (NesterovSolve Non-Convergence)

**Symptoms:**
- flow.log shows thousands of `[NesterovSolve] Iter: XXXX overflow: 0.2X` lines
- Overflow stays above 0.10 and oscillates without decreasing
- Process eventually killed by timeout or OOM
- No explicit error message in log

**Root Cause:**
`PLACE_DENSITY_LB_ADDON` is too low (e.g., 0.01), making the effective placement density too close to the theoretical minimum. The placer cannot find a legal solution with so little density headroom, especially for large designs with macros.

**Action:**
- Raise `PLACE_DENSITY_LB_ADDON` to at least 0.20 (0.30-0.45 for large macro-heavy designs)
- If `PLACE_DENSITY` is set explicitly, ensure it's at least 0.55
- For designs with macros (fakeram, SRAM blocks), use higher density (0.60-0.70)
- Compare with successful configs of the same design for known-good density values
- Also add `SKIP_LAST_GASP=1` to prevent post-placement optimization from stalling

## Place_gp Stuck on Timing-Driven Iteration (>1M-net BOOM-class designs)

**Symptoms:**
- `place` stage timeout (exit 124) after the full `ORFS_TIMEOUT` budget
- flow.log shows `[INFO GPL-0100] Timing-driven iteration 1/2, virtual: false.`
  followed by an `Iteration | Area | Resized | Buffers | Nets repaired | Remaining` table that never finishes
- `Remaining` net count is in the millions (e.g. 2.2M for BOOM SmallSEBoom)
- CPU stays pinned but no log progress for hours
- Earlier in the log, `[NesterovSolve]` overflow already converged below the target — the *initial* placement is fine; the *timing repair pass* is what's stuck

**Root Cause:**
For very large netlists (>1M nets after memory inference), gpl's timing-driven
incremental repair iterates over every violating endpoint and runs the
resizer pin-by-pin. With 17 OpenRAM-stub-derived flop arrays and a BOOM core,
ChipTop has 1.5-2.5M nets and the repair loop never converges in a reasonable
wall-clock budget.

**Action:**
- Re-run with `PLACE_FAST=1 FROM_STAGE=place scripts/flow/run_orfs.sh ...`
  → `run_orfs.sh` injects `GPL_TIMING_DRIVEN=0 GPL_ROUTABILITY_DRIVEN=0`
  on the make line. Place_gp completes the initial Nesterov solve and skips
  the multi-hour repair loop. CTS / route still run with timing.
- Equivalent: add `export GPL_TIMING_DRIVEN = 0` and
  `export GPL_ROUTABILITY_DRIVEN = 0` to `constraints/config.mk` permanently.
- Validated: BOOM SmallSEBoom — initial place_gp converges in <30 min with
  PLACE_FAST=1 vs >8h timeout without.
- This is orthogonal to `PLACE_DENSITY_LB_ADDON`; raising density does not
  help because the issue is the timing-repair loop, not the placer's
  legalization step.

### Sub-variant: 3_4_place_resized's `repair_design` hangs (NOT place_gp)

**Symptoms:**
- place_gp (3_3_place_gp) finishes cleanly in <1h with PLACE_FAST=1 — Nesterov
  overflow converges to target, HPWL drops by orders of magnitude
- 3_4_place_resized then runs `resize.tcl` → `repair_design -verbose`
- `Iteration | Area | Resized | Buffers | Nets repaired | Remaining` table
  starts advancing fast, then progress crawls to 0 around iter ~700K of ~1.3M
- No further log output for hours; openroad CPU stays at 100-110%, RSS stable
- ORFS_TIMEOUT eventually fires, run exits 124

**Root Cause:**
`repair_design` (the post-placement buffer-insertion + gate-resizing pass) hits
a slow inner code path on certain combinations of net count + gate fanout +
slack distribution. PLACE_FAST=1 does NOT help — it only disables gpl's
timing-driven mode, not the standalone resize.tcl invocation. No ORFS env
knob currently skips `repair_design` at place_resized.

**Validated:** arm_core (Amber a25 ARM core, 8211-line monolithic Verilog with
4 `single_port_ram_*` modules) hits this 2026-05-26. Two passes: pre-PLACE_FAST
(57600s budget, killed at 3_3 gp), post-PLACE_FAST (28800s budget, gp finished
in 2089s, killed at 3_4 place_resized iter 785K with progress stalled for 4h).

**Action:**
- Document as intractable at place_resized stage for this design on this
  ORFS/nangate45.
- For a fresh design hitting this, options ranked by promise:
  1. Reduce design size: lower CORE_UTILIZATION, smaller core area, gut large
     submodules, replace inferred RAMs with `fakeram45` macros so they don't
     synthesize as flip-flop forests.
  2. Use a newer OpenROAD that supports skipping `repair_design` at place.
  3. Accept intractable.

## OpenROAD SIGSEGV in CTS / Repair Timing

**Symptoms:**
- `Signal 11 received` during `repair_timing` at CTS stage (4_1_cts)
- Stack trace shows `sta::ClkInfo::crprClkVertexId()` or similar STA functions
- `Command terminated by signal 11` followed by make error

**Root Cause:**
OpenROAD bug in timing repair, typically triggered by complex clock trees in large designs (e.g., swerv with 10k+ clock sinks). This is non-deterministic in some cases.

**Action:**
- Add `export SKIP_CTS_REPAIR_TIMING = 1` to config.mk to bypass the crashing step
- Also add `export SKIP_LAST_GASP = 1` to avoid similar crashes in later stages
- If the design must have CTS timing repair, try reducing the number of clock sinks by increasing die area
- Re-running may work if the crash is non-deterministic

**Variant — SIGSEGV in CTS *init* (`separateMacroRegSinks` / `initClockTree`):**
the crash happens before timing repair, in `cts::TritonCTS::initClock` →
`separateMacroRegSinks`, on small designs with a derived/gated clock where a
clock net has very few sinks (e.g. 2). `SKIP_CTS_REPAIR_TIMING=1` +
`SKIP_LAST_GASP=1` is still the first fix (`fix_orfs_failures.py` →
`apply_cts_crash_fix`), but it bypasses a *later* pass than the crash, so it may
not help. If CTS still SIGSEGVs in init, this is an upstream OpenROAD bug —
classify as a **tool limitation / skip**, like the BOOM floorplan cases. Do not
keep retrying.

## RTL Reserved Keywords as Identifiers

**Symptoms:**
- Yosys parse error: `syntax error, unexpected ','` or `unexpected TOK_INT`
- Error appears at a port list or signal declaration line
- The RTL compiles fine with older Verilog-95 tools but fails in Yosys

**Root Cause:**
Verilog-2005 and SystemVerilog reserve keywords like `int`, `bit`, `logic`, `byte`, `shortint`, `longint`, `shortreal`, `string`, `type`. Legacy RTL (e.g., OpenCores IP) sometimes uses these as port or signal names. Yosys 0.50+ enforces Verilog-2005 keyword rules.

**Action:**
- Run `scripts/project/validate_config.py <project-dir>` first — it scans `wire`/`reg`/`logic`/port declarations for reserved keywords (port-only, prior-version users: the check now covers internal nets too)
- Or grep manually: `grep -wn 'int\|bit\|logic\|byte\|shortint' *.v`
- Rename the identifier everywhere: port list, port declaration, assign statements, and all instantiation sites
- Common rename pattern: `int` → `int_o` (or `int_w` for an internal wire), `bit` → `bit_o`
- Check both the module definition file AND all files that instantiate the module

**Examples:**
- `wb_dma_ch_rf.v` uses `int` as an output port → rename to `int_o` in the module and all `.int(...)` connections in `wb_dma_rf.v`.
- Faraday `dma_ctlrf.v` uses `int` as an internal wire (`wire [`DMA_MAX_CHNO-1:0] int;`) — only 4 occurrences, all local to one file. Rename to `int_w`. The validator now catches `wire`/`reg` declarations, not just port declarations, so this is detected at intake before synth.

## Missing Floorplan Initialization

**Symptoms:**
- `Error: No floorplan initialization method specified` during ORFS floorplan stage
- Backend fails at `2_1_floorplan` step

**Root Cause:**
config.mk lacks both `CORE_UTILIZATION` and `DIE_AREA`/`CORE_AREA`. ORFS requires at least one method to initialize the floorplan.

**Action:**
- Add `export CORE_UTILIZATION = 30` to config.mk (recommended default)
- Or set explicit die/core area: `export DIE_AREA = 0 0 200 200` and `export CORE_AREA = 10 12 190 188`
- For very small designs (< 10 cells), prefer explicit area over utilization

## ORFS Directory Collision (tmp.log mv Failure)

**Symptoms:**
- `mv: cannot stat './logs/<platform>/<design>/base/X.tmp.log': No such file or directory`
- The actual EDA step (floorplan, placement, etc.) appears to complete successfully (timing/area reports visible in log)
- Makefile reports Error 1 on the `do-X` target

**Root Cause:**
Multiple project configs sharing the same `DESIGN_NAME` use a shared `FLOW_VARIANT=base`, causing all runs to write to the same ORFS working directories. The `make clean_all` from one run can interfere with another run's log files, or stale state from a previous config can corrupt the current run.

**Action:**
- Use a unique `FLOW_VARIANT` per project config (e.g., project directory basename)
- The `run_orfs.sh` script now derives FLOW_VARIANT automatically from the project directory name
- If running manually: `make DESIGN_CONFIG=... FLOW_VARIANT=<unique_name>`

## Timeout / Stalled Backend Runs

**Symptoms:**
- flow.log ends abruptly during placement, CTS, or global routing with no error message
- `ERROR: ORFS run timed out after Xs` at end of flow.log (if using run_orfs.sh)
- Last log entry shows iterative optimization (e.g., NesterovSolve iterations, buffer insertion)
- No GDS or final ODB produced

**Root Cause:**
Large designs (swerv, bp_multi_top, tinyRocket) can take hours for PnR. The process may have been killed by an OOM killer, the ORFS_TIMEOUT limit, or resource limits.

**Action:**
- Increase timeout: `ORFS_TIMEOUT=14400 scripts/flow/run_orfs.sh ...` (4 hours)
- Limit CPU usage: `ORFS_MAX_CPUS=4 scripts/flow/run_orfs.sh ...` (prevent thermal/resource issues)
- For faster convergence, add to config.mk:
  - `export SKIP_LAST_GASP = 1` (skip last-gasp optimization)
  - `export SKIP_CTS_REPAIR_TIMING = 1` (skip CTS timing repair)
  - `export SKIP_GATE_CLONING = 1` (skip gate cloning)
- Increase die area to reduce placement density (lower utilization = faster convergence)
- Monitor with `tail -f flow.log` to track progress

## ABC Mapping Stalls on Behavioral Memory Explosion

**Symptoms:**
- Yosys reaches step 14 (`Executing ABC pass (technology mapping using ABC)`)
- ABC child process (`yosys-abc`) running > 1 h with no `output.blif` written
- ABC's `input.blif` in `/tmp/yosys-abc-*/` is unusually large (> 100 MB)
- RSS stable, CPU pegged at ~100% on one core
- Eventually hits `ORFS_TIMEOUT` and dies with `Error 247`

**Root Cause:**
Behavioral SRAM stubs (e.g., generated by
`tools/gen_openram_behavioral_stubs.py`) are functionally correct but
explode the post-`memory_map` gate count. A single 1w1r 512x64 memory
becomes ~32K flops + ~32K mux2 cells (read decoder). Across many such
memories, the ABC input passes 1M gates and `abc_speed.script` grinds
superlinearly.

**Action:**
1. **First, confirm via the RTL inventory.** If total memory bits across
   all behavioral stubs > ~50K, ABC is at risk.
2. **Try `SYNTH_HIERARCHICAL=1`.** Each Yosys module is mapped through
   ABC independently — your N SRAM stubs become N small ABC runs of
   ≤32K gates each, not one 1M-gate run.
3. **If that still fails, switch to real macros.** Map each
   `freepdk45_sram_*` to the nearest fakeram45 size with proper LEF/LIB
   integration (ADDITIONAL_LEFS, ADDITIONAL_LIBS, CDL, MACRO_PLACEMENT_TCL).
   For `1w1r` styles where fakeram is single-port, write a wrapper
   that uses two separate fakeram banks or accepts functionally-wrong
   single-port behavior (ORFS only cares about timing/area).
4. **Or hybrid**: keep the small memories (< ~2K bits) behavioral and
   substitute real macros only for the large ones.

**Validated case:**
- BOOM SmallSEBoom (168K total memory bits via behavioral stubs): ABC
  ground for 1h48m without writing output.blif before being aborted at
  the 2h28m mark. Full breakdown in
  `design_cases/boom_smallseboom/reports/synth-result.md`.

## Yosys Segfault (Signal 11)

**Symptoms:**
- `Command terminated by signal 11` during Yosys synthesis
- Occurs during Liberty frontend loading or hierarchy pass

**Root Cause:**
Yosys crash, typically caused by very large designs or specific RTL constructs that trigger a parser/optimizer bug.

**Action:**
- Re-run — segfaults are sometimes non-deterministic
- Try toggling `SYNTH_HIERARCHICAL` (1 ↔ 0) in config.mk
- If reproducible, try simplifying the RTL or splitting into hierarchical blocks
- Check available memory (`free -h`) — Yosys may need 4-8 GB for large designs

## Common ORFS-Specific Issues

### Clock Port Mismatch
- **Symptom:** `cannot find port` error in STA
- **Fix:** Ensure `clk_port_name` in SDC matches the exact port name in RTL

### DESIGN_NAME Mismatch
- **Symptom:** `no cells mapped` or empty synthesis
- **Fix:** DESIGN_NAME in config.mk must exactly match `module <name>` in RTL

### Missing VERILOG_FILES
- **Symptom:** `no such file` during synthesis
- **Fix:** Use absolute paths in config.mk for VERILOG_FILES

### PDN Error on Small Designs
- **Symptom:** `Insufficient width to add straps` during floorplan/PDN
- **Fix:** Set explicit `DIE_AREA = 0 0 50 50` and `CORE_AREA = 2 2 48 48` in config.mk instead of `CORE_UTILIZATION`

### Platform Not Found
- **Symptom:** Make error about missing platform
- **Fix:** Verify platform name matches a directory in `$ORFS_ROOT/flow/platforms/`
- Available: nangate45, sky130hd, sky130hs, asap7, gf180, ihp-sg13g2

## Signoff Check Failures

### Re-running signoff after ORFS scratch dirs were cleaned

- **Symptom:** `run_drc.sh` reports `ERROR: ORFS config not found at .../config.mk` or "Running DRC for design: top" with a GDS path that points to a *different* project (e.g., `iccad2015_unit02_in2`'s GDS gets picked up for `button_controller` because both have `DESIGN_NAME=top`). Make may also start re-running place/cts/route, taking 30+ minutes for a "DRC" invocation.
- **Root cause:** Two distinct issues that surface together when a project's ORFS scratch dirs (`flow/designs/<plat>/<DESIGN_NAME>/<variant>/`, `flow/results/...`) have been cleaned but the project's `backend/RUN_*/` still holds the preserved artifacts:
  1. The DRC/LVS scripts used to fall back to `flow/results/<plat>/<DESIGN_NAME>/` (no variant) and `find … -name 6_final.gds`, which silently picked up *another design's* GDS that shared the `DESIGN_NAME`. In our corpus, 59 projects use `DESIGN_NAME=top` and 28 use `DESIGN_NAME=test` — collision was guaranteed.
  2. ORFS Makefile has dependency edges: `6_drc.lyrdb` → `6_final.gds` → `6_final.def` → `5_route.odb` → `4_cts.odb` → … If only `6_final.*` are present but the upstream `*.odb` intermediates are missing, make's timestamp check rebuilds everything from `5_1_grt` backward.
- **Fix (since 2026-05-26):** `r2g-rtl2gds/scripts/flow/_restage_for_signoff.sh` is now sourced by both `run_drc.sh` and `run_lvs.sh`. It:
  - Picks the project's `backend/RUN_*/` dir that actually contains `results/6_final.gds` (not just the newest mtime — empty crash dirs often have a newer ctime than the successful one).
  - Copies `results/`, `logs/`, `reports_orfs/` back into the ORFS staging paths with `cp -n` so already-present files are kept.
  - Falls back to `final/6_final.gds` for older r2g project layouts that only preserved the final/ subset.
  - Touches all staged files so make sees them as up-to-date against `config.mk`.
- **Validated:** Restage takes 10-30s for medium designs, 60-90s for ChipTop-scale (4GB+ ODB). `make drc` then runs only the klayout step (no upstream rebuilds). Tested on `button_controller` (50s) and `bgm` (which hit the stuck-on-`or` pattern correctly without re-running place).
- **Skill-level:** When you see "DRC running on the wrong design" or "DRC is unexpectedly re-running place/cts", the restage helper is the load-bearing fix — do not regress the variant-aware lookup in `run_drc.sh` / `run_lvs.sh`.

### DRC Violations
- **Symptom:** `X violations found` in DRC report; `6_drc_count.rpt` shows non-zero count
- **Diagnosis:** Run `scripts/extract/extract_drc.py` to get per-category violation breakdown from `6_drc.lyrdb`
- **Common causes:**
  - Routing density too high → reduce `PLACE_DENSITY_LB_ADDON` or increase die area
  - Insufficient spacing → increase `DIE_AREA`/`CORE_AREA`
  - Metal width violations → may indicate congestion, try lower utilization
- **Tool:** `scripts/flow/run_drc.sh` → `scripts/extract/extract_drc.py` for detailed category breakdown

### KLayout DRC Stuck on `or` (FreePDK45.lydrc, nangate45)

- **Symptom:** `run_drc.sh` runs for hours with no progress. `6_drc.log` last line is `"or" in: FreePDK45.lydrc:121` (or another boolean-op line — also observed at lines 91, 131) and the file mtime stops advancing. CPU stays at 100% on a single klayout process; RSS plateaus around 500MB-3.5GB.
- **Root cause:** KLayout DRC's combination of `poly.not(active).separation(active, ...)` followed by an `or` builds large intermediate polygon sets. On dense designs (>~1.5K cells / >~1MB GDS) the rule scales poorly. Validated on this environment: `iscas89_s27` (86 cells), `ansiportlist` (231), `binops` (231), `CRC33_D264` (1434) all complete; `faraday_dma` (14k cells, 14.8MB GDS) hung indefinitely on rule 121. Also observed on `APB_GPIO_register`, `AXI_Lite_DMA_axilite`, `DMA_Controller_DMA_registers` stuck on rule 131.
- **Action:**
  - Don't extend `DRC_TIMEOUT` blindly — observed zombies ran 3-4 days at 100% CPU without finishing on the same rule.
  - Document in `<project>/drc/drc_result.json` with `"status": "stuck"` and the `stuck_at_rule` so the dashboard can show a yellow badge instead of red.
  - Use `setsid timeout` (already enforced in `run_drc.sh`) so terminating the parent kills the klayout child cleanly.
  - For the design itself, the rest of the flow (ORFS → RCX) is independent and can still produce GDS+SPEF.
- **Pre-existing zombies:** If the system has klayout DRC processes running >1 hour at 100% CPU on the same `lydrc` line and no log progress, they're stuck in this pattern. Kill with `kill -9 <pid>`. Six such zombies were observed in this session, accumulating ~20k+ minutes of wasted CPU before cleanup.

#### BEOL-only fallback

When the FEOL hang is confirmed, run with `DRC_BEOL_ONLY=1 bash scripts/flow/run_drc.sh <proj> <platform>`. The script generates a modified deck copy (`drc/*.beol.lydrc`) with **both** `FEOL = false` **and** `ANTENNA = false`, and passes it to `make drc` via `KLAYOUT_DRC_FILE=`. **FEOL and ANTENNA checks skipped (ANTENNA depends on FEOL-derived layers); metal/via routing geometry + off-grid checks run.** The standard cell library is pre-characterized and DRC-clean, so skipping the front-end-of-line boolean ops (poly, diffusion, gate geometry) is safe. ANTENNA must also be disabled because its `connect` rules reference the `gate` layer (`gate = poly & active`), which is derived *inside* the deck's `if FEOL … end` block — leaving ANTENNA on with FEOL off makes KLayout error (`'connect': First argument must be a layer …`) and `make` exit 1, which the runner would then mis-classify as `stuck`. `OFFGRID` stays on (no FEOL dependency, completes fine). Results are tagged `"drc_mode": "beol_only"` in `drc_result.json` and `reports/drc.json`, and a 0-violation BEOL-only run is given the **qualified status `clean_beol`** (not plain `clean`) by `extract_drc.py` so status-based aggregation can never silently miscount it as a full clean (mirrors LVS `clean_algorithmic`; `diagnose_signoff_fix.py` treats `clean_beol` as needing no fix). Do **not** report a BEOL-only run as full DRC-clean, and **antenna is NOT verified** in this mode. (On nangate45 antenna is KLayout-only and not OpenROAD-fixable anyway — see the Antenna DRC Violations section / campaign Finding B — so BEOL-only loses no fixable coverage there.)

**Deeper fallback for large designs (`DRC_BEOL_STRICT=1`, implies BEOL-only; `DRC_SKIP_CONTACT` is a back-compat alias).** Surprising empirical finding: the `FEOL = false` toggle gates the Well/Poly/Active booleans (the `:91/:121/:131` hangs) but does **NOT** gate the **IMPLANT** and **CONTACT** groups — those still execute in BEOL-only mode and **hang on large designs** (≥~465K inst: eth_mac_1g_fifo, koios_gemm_layer froze 5–8 min at 100% CPU, RSS 7.3GB, at `implant.width`/`cont.space` over millions of MOL polygons). Designs ≤~144K run those groups fine; only the largest hang. All FEOL-block geometry (well/poly/active/implant/contact) is library-internal — P&R adds only metal and vias, never intra-cell MOL shapes — so stripping the whole block body is as defensible as the FEOL toggle. `DRC_BEOL_STRICT=1` uses awk to comment **every `.output(` check between `if FEOL` and `end # FEOL`** in the generated deck (aborts if any remains uncommented), leaving the layer-derivation lines intact and only BEOL metal/via + OFFGRID checks running — the actual P&R-created geometry. Tagged `"drc_mode": "beol_only_strict"`; a 0-violation result is still `clean_beol` (the `drc_mode` records the precise scope). Use this only when plain `DRC_BEOL_ONLY=1` hangs at an IMPLANT/CONTACT op. **Empirical ceiling (verified):** on `eth_mac_1g_fifo` (469K) BEOL-strict cleared the entire FEOL block (logged `BEOL checks`) but then **hung on the first BEOL `metal1.width` (METAL1.1) op** — the legitimate P&R metal-geometry check, which *cannot* be skipped without abandoning DRC entirely. So designs whose **METAL** ops don't converge (≥~465K inst here: eth_mac_1g/mii_fifo, axis_ram_switch, koios_gemm_layer, and the multi-million-inst BOOMs) are **genuinely intractable for this KLayout build** and stay honest `stuck` — no flow lever helps. `DRC_BEOL_STRICT` only rescues a design whose hang is in the FEOL-block MOL groups *while* its METAL ops are tractable; no design in the current corpus has been shown to fall in that narrow band (everything ≤~406K already completes with plain `DRC_BEOL_ONLY`), so strict mode is presently a defensive fallback rather than a demonstrated unblock.

#### Sub-variant: externally-killed stuck (exit 2, not 137)

When klayout DRC is stuck on a polygon op and gets SIGKILL'd by something **other** than `run_drc.sh`'s own timeout — cgroups OOM, session limit, monitor script, or manual `pkill` — `make` exits 2 (target failed), not 124/137. Older `run_drc.sh` versions classified this as a generic `failed`, hiding the stuck pattern from triage.

- **Detection (since 2026-05-23):** `run_drc.sh` greps `drc_run.log` for `Killed $KLAYOUT_CMD`, `Killed klayout`, or `Error 137`. When that keyword is present AND a `*.lydrc:NN` reference exists in `6_drc.log`, classify as `status=stuck` with `killed_externally=true` in `drc_result.json` (regardless of make's wrapper exit code).
- **Symptom example:** `drc_run.log` ends with `klayout.sh: line 9: <PID> Killed   $KLAYOUT_CMD "$@"` followed by `make: *** [...] Error 137`, while the run-script's PIPESTATUS captures `2`. Total elapsed is well under the timeout (3-8 minutes) but CPU utilization is low (~13%) indicating klayout was waiting (not crashing) when killed.
- **Action:** Same as the timeout variant — treat as stuck, do not retry. The signoff outcome for the design is effectively "DRC unavailable, GDS+LVS+RCX still valid". The 3 v2 cases (APB_GPIO_register, AXI_Lite_DMA_axilite, DMA_Controller_DMA_registers) that previously logged as `drc=fail(2)` are now correctly tagged `stuck` after this fix.

### LVS Mismatch

**Automated fix:** `scripts/flow/fix_signoff.sh` (see `references/signoff-fixing.md`). Note: the 400:1 antenna-ratio relaxation is RETIRED — real layout fixes only.

- **Symptom:** `ERROR : Netlists don't match` in LVS log; mismatches in `6_lvs.lvsdb`
- **Diagnosis:** Check the extracted SPICE netlist (`*_extracted.cir`) vs the CDL reference. Common mismatch patterns:
  - Empty extracted netlist (0 devices) → gate layer definitions don't match GDS layers
  - Extra pins on subcircuits (`VDD$1`, `VSS$1`, `$6`, `$7`) → bulk terminal connectivity issue
  - Missing pins (e.g., `QN` on flip-flops) → unused output not routed in design
- **Common causes:**
  - **Device model name mismatch:** LVS rule extracts `PMOS_LVT` but CDL uses `PMOS_VTL` → rename in `.lylvs`
  - **Missing threshold voltage layers:** nangate45 GDS has no vtg/vth/thkox layers → use `lv_pgate = pgate` directly
  - **Bulk terminal pin bloat:** `mos4` extraction creates extra bulk pins → use `connect_implicit("VDD")` and `connect_implicit("VSS")` to merge
  - **Unused cell pins:** design doesn't connect all CDL pins → add `schematic.purge` and `schematic.purge_nets`
  - Extra devices from fill/tap cells
  - Port name mismatches between GDS and CDL netlist
- **Tool:** `scripts/flow/run_lvs.sh` → `scripts/extract/extract_lvs.py`

### LVS Skipped (No Rules)
- **Symptom:** `LVS is not supported on this platform` or `lvs_result.json` shows status "skipped"
- **Fix:** This is expected for platforms without KLayout LVS rule decks. Not a flow error.
- **Platforms without KLayout LVS:** asap7
- **Alternative:** For sky130hd/sky130hs, use `run_netgen_lvs.sh` (Netgen + Magic) as an alternative LVS flow

### Magic DRC Failure
- **Symptom:** Magic DRC script fails or produces no output
- **Common causes:**
  - sky130A tech file missing at `/opt/pdks/sky130A/libs.tech/magic/sky130A.tech`
  - Platform not supported (Magic DRC only works for sky130hd/sky130hs)
  - GDS file corrupted or from incomplete backend run
- **Tool:** `scripts/flow/run_magic_drc.sh`

### Netgen LVS Failure
- **Symptom:** Magic SPICE extraction fails or Netgen comparison fails
- **Common causes:**
  - sky130A PDK files missing (tech file or netgen setup.tcl)
  - No Verilog netlist found (6_final.v or synth_output.v)
  - Platform not supported (Netgen LVS only works for sky130hd/sky130hs)
- **Diagnosis:** Check `lvs/magic_extract.log` for extraction errors, `lvs/netgen_lvs.log` for comparison errors
- **Tool:** `scripts/flow/run_netgen_lvs.sh`

### RCX Extraction Failure
- **Symptom:** `extract_parasitics` error in OpenROAD; no SPEF output
- **Diagnosis:** Check `rcx/rcx.log` for OpenROAD error messages
- **Common causes:**
  - `rcx_patterns.rules` file missing for the platform
  - `6_final.odb` is invalid or corrupted
  - Backend did not complete successfully
- **Fix:** Verify platform has `rcx_patterns.rules`. Ensure `6_final.odb` exists. Re-run backend if needed.
- **Tool:** `scripts/flow/run_rcx.sh` → `scripts/extract/extract_rcx.py`

### LVS CDL_FILE Override by Platform Config

**Symptoms:**
- `[ERROR ODB-0287] Master fakeram45_XXxYY was not in the masters CDL files`
- LVS fails for designs that use fakeram or other hard macros
- CDL file passed to KLayout only contains standard cell definitions

**Root Cause:**
The ORFS Makefile includes the design config.mk (line ~98) before the platform config.mk (via variables.mk). The platform config.mk sets `export CDL_FILE = $(PLATFORM_DIR)/cdl/NangateOpenCellLibrary.cdl`, which overwrites any CDL_FILE set in the design config. This means macro designs cannot point to a CDL file that includes both standard cells and macro subcircuit definitions.

**Action:**
- Use `override export CDL_FILE = /path/to/combined.cdl` in the design config.mk. The `override` keyword prevents later assignments from overriding it.
- Create a combined CDL that concatenates the standard cell CDL with CDL stubs for all fakeram types used.
- CDL stubs can be generated from LEF pin lists: `.SUBCKT fakeram45_NxM <pin1> <pin2> ... VDD VSS` / `.ENDS`

### LVS Timeout on Large Macro Designs

**Symptoms:**
- LVS process killed after timeout (exit code 124)
- LVS log ends with `Flatten schematic circuit (no layout): ...` messages
- klayout process consumes >4GB memory
- Zombie klayout process persists after timeout

**Root Cause:**
KLayout LVS scales poorly with design size. Validated timings from 70-design batch:
- **<100K cells** (aes, ibex, riscv32i, tinyRocket, vga_enh_top): <30 min. **100% LVS pass at 3600s.**
- **~145K cells** (swerv): ~56 min. Passes at 3600s under low load (6/10 pass), times out under parallel contention (4/10 timeout). Load-dependent.
- **~200K cells** (bp_multi_top): Always >60 min. **0% LVS pass at 3600s.** Needs 7200s.

The `timeout` command only kills the `make` process; the grandchild `klayout` process survives as a zombie consuming 3-5 GB memory and holding flock file descriptors, blocking subsequent designs.

**Action:**
- **<100K cells**: Default `LVS_TIMEOUT=3600` is sufficient
- **~145K cells** (swerv-class): Use `LVS_TIMEOUT=4200` or run LVS with fewer parallel jobs to reduce CPU contention
- **>150K cells** (bp_multi_top-class): Use `LVS_TIMEOUT=7200`
- run_lvs.sh now uses `setsid timeout` to kill the entire process group, preventing zombie processes
- If zombie klayout processes persist from pre-fix scripts: `pkill -f 'klayout.*lvs'`

### SYNTH_HIERARCHICAL + Blackbox Cost Error

**Symptoms:**
- `ERROR: Missing cost information on instanced blackbox <module_name>` during Yosys synthesis
- Occurs only with `SYNTH_HIERARCHICAL = 1` in config.mk
- Same design passes synthesis without `SYNTH_HIERARCHICAL`

**Root Cause:**
The `SYNTH_HIERARCHICAL` option enables Yosys's hierarchical synthesis flow, which includes a `CELLMATCH` pass. This pass needs cost information for all instantiated modules. Modules declared with `(* blackbox *)` attribute have no cost info, causing CELLMATCH to fail. Standard cells have cost info from their .lib files, but custom wrapper modules (e.g., BSG hard memory wrappers) don't.

**Action:**
- Replace `(* blackbox *)` stubs with actual wrapper implementations that instantiate the underlying fakeram macros. The fakeram macros have .lib files (providing cost info), so they work as blackboxes. The wrapper module becomes a real module with internal instantiation.
- Example: instead of `(* blackbox *) module hard_mem_wrapper(...)`, write a module that contains `fakeram45_NxM mem (.clk(clk_i), .rd_out(data_o), ...)`.

### Invalid macro_placement.tcl Command

**Symptoms:**
- `Error: macro_placement.tcl, N invalid command name "all_macros"` during floorplan stage
- Backend fails at `2_2_floorplan_macro` step

**Root Cause:**
The Tcl command `all_macros` does not exist in OpenROAD. The correct command is `find_macros`. Also, `macro_placement` requires an initial global placement to have starting positions for the simulated annealing algorithm.

**Action:**
- Use `find_macros` instead of `all_macros` in macro_placement.tcl
- Call `global_placement` before `macro_placement`:
  ```tcl
  if {[find_macros] != ""} {
    global_placement -density [place_density_with_lb_addon] -pad_left 2 -pad_right 2
    macro_placement -halo {10 10} -style corner_max_wl
  }
  ```

### PDN Channel Repair Failure (PDN-0179)

**Symptoms:**
- `[ERROR PDN-0179] Unable to repair all channels` during floorplan stage
- Backend fails at floorplan with exit code 2
- Typically occurs with `SYNTH_HIERARCHICAL=1` which increases gate count

**Root Cause:**
The power delivery network (PDN) grid cannot fit within the die/core area. `SYNTH_HIERARCHICAL=1` can increase the effective cell count (less optimization across module boundaries), requiring more area. Combined with aggressive placement density or insufficient die area, the PDN straps don't have enough room.

**Action:**
- Increase `DIE_AREA` / `CORE_AREA` by 10-20%
- Reduce `PLACE_DENSITY` (e.g., 0.35 → 0.30)
- Try without `SYNTH_HIERARCHICAL=1` if not required
- This is a config tuning issue, not a script bug

### RCX Skipped (No Rules)
- **Symptom:** `No RCX rules found for platform` or `rcx_result.json` shows status "skipped"
- **Fix:** The platform lacks `rcx_patterns.rules`. This is uncommon — most platforms include RCX rules.

### Empty SPEF (RCX)
- **Symptom:** `rcx.json` shows status "empty", `net_count` is 0
- **Common causes:**
  - Design has no routed nets (backend did not complete routing)
  - ODB file is from an early stage before routing
- **Fix:** Verify the ORFS flow completed through the routing stage. Check `progress.json` for stage completion.

## Batch-Campaign Failure Patterns (Validated on 495-design run)

These six patterns account for every failure in the 495-design batch completion report (`docs/batch_orfs_completion_report.md`) and are fully addressed by `tools/fix_orfs_failures.py`.

### FLW-0024: Place density exceeds 1.0

**Symptoms:**
- `[ERROR FLW-0024] Place density exceeds 1.0 (current PLACE_DENSITY_LB_ADDON = 0.2). Please check if the design fits in the die area.`
- Fails early in the `place` stage
- Typical on tiny-size auto-configs (`DIE_AREA = 0 0 50 50`) whose synthesized cell count overflows the 50×50 core

**Root Cause:**
Tiny die/core area generated from line-count heuristics is too small once synthesis expands the design (e.g., `FloatingMultiplication`, `sha1_core_repo`). The placer can't even achieve 100% density, so it errors.

**Action:**
- Delete `DIE_AREA` / `CORE_AREA` and switch to `CORE_UTILIZATION = 10` (or 15). ORFS will auto-size the floorplan.
- Keep `PLACE_DENSITY_LB_ADDON = 0.20`.
- If the design is truly small but synthesizes to many cells (arithmetic/FPU), drop utilization further.

### PPL-0024: IO pins exceed available positions

**Symptoms:**
- `[ERROR PPL-0024] Number of IO pins (342) exceeds maximum number of available positions (248). Increase the die/core perimeter.`
- Fails in the `place` stage
- Common for ICCAD benchmark `top` designs, interconnect crossbars, and memory wrappers with wide word interfaces

**Root Cause:**
IO pins must fit along the die perimeter. Tiny 50×50 or small 120×120 floorplans don't have enough perimeter for designs with hundreds-to-thousands of ports.

**Action:**
- Switch to `CORE_UTILIZATION = 15` so ORFS picks a die area based on cell count, which scales perimeter accordingly.
- For extreme pin counts (>2000), `CORE_UTILIZATION = 10` plus `PLACE_DENSITY_LB_ADDON = 0.20` is more robust.
- Do not set an explicit DIE_AREA for these designs.

### SYNTH_MEMORY_MAX_BITS exceeded

**Symptoms:**
- Yosys log: `Error: Synthesized memory size 4096 exceeds SYNTH_MEMORY_MAX_BITS`
- `Largest single memory instance: 32768 bits`
- Fails at `do-yosys` target (exit code 2)
- Common for CPU cores with register files/TLB (`arm_core`), FIFOs (`verilog_axis_axis_fifo`), and Ethernet MAC buffers

**Root Cause:**
ORFS default `SYNTH_MEMORY_MAX_BITS = 4096` forces Yosys to refuse inferring memories larger than 4 Kbit rather than exploding them into flip-flops. Works for tiny designs, fails the moment RTL contains a real register file.

**Action:**
- Add `export SYNTH_MEMORY_MAX_BITS = 131072` (128 Kbit) to config.mk. Yosys will synthesize the memory into flip-flops.
- For designs >128 Kbit memory, raise further or integrate fakeram hard macros (see macro-design flow in SKILL.md).
- Pair with `CORE_UTILIZATION ≤ 20` because FF-based memories consume many cells.

### Missing `include file

**Symptoms:**
- Yosys log: `ERROR: Can't open include file 'biriscv_defs.v'!`
- Fails during `do-yosys-canonicalize`
- Common when RTL was copied without header/define files (e.g., `.vh` in a separate dir)

**Root Cause:**
The `copy RTL files` step in `tools/setup_rtl_designs.py` only copies `.v` files listed in `design_meta.json.rtl_files`; referenced `include "*.v" / "*.vh"` headers outside that list get left behind.

**Action:**
- Add `export VERILOG_INCLUDE_DIRS = <path>` to config.mk pointing at the directory that contains the referenced headers.
- If the header isn't in the repo (user-local source), either:
  - Concatenate its contents inline at the top of the first RTL file, or
  - Drop the design from the batch and fetch the original header.
- `tools/fix_orfs_failures.py` creates empty stub headers as a placeholder, but empty stubs only help if the includes are guards; designs that rely on `` `define MACRO `` macros inside the header will still fail synthesis.

**Stubbable vs unstubbable headers (validated on the rtl_designs_v2 batch — 44/213
designs hit this):** `setup_rtl_designs.py` now classifies missing `include targets:
- **`timescale.v` / `timescale.vh`** — pure `` `timescale `` directive. Auto-stubbed
  with `` `timescale 1ns / 1ps ``. Always safe.
- **`*undefines*` headers** — a list of `` `undef `` directives. Auto-stubbed empty
  (undef of an absent macro is harmless).
- **Content headers** (`*_defines.v`, `*_header.vh`, `config.vh`, `core.vh`, register
  maps) — carry real `` `define `` / `parameter` values. CANNOT be stubbed. The design
  bundle is **incomplete**; `setup_rtl_designs.py` records
  `status: incomplete_missing_headers` + a `missing_headers` list in `metadata.json`.
  Classify these as **incomplete-bundle / skip**, not a flow failure — do not burn
  retry attempts on them. They fail fast at `do-yosys-canonicalize` (~2-4 s).
- Header files (`.vh`/`.svh`/`.h`/`.inc`) shipped alongside the RTL are now copied
  into `rtl/` by setup (previously only `.v`/`.sv` were copied).

**Sibling-bundle harvest:** before declaring a content header unstubbable,
`setup_rtl_designs.py` searches two pools for a file of the same basename:
(1) every other design bundle under the RTL source dir, and (2) every
already-set-up `design_cases/*/rtl/` directory. Pool 2 makes header recovery
**compounding** — once one family member gets a header (harvested *or*
hand-reconstructed and proven by a passing run), the rest of the family
inherits it automatically on the next setup. It copies a candidate when
**either** two-plus independent bundles ship a byte-identical copy (`exact`)
**or** the match comes from a same-repo-family design (shared design-name
prefix, `family`); harvested files are listed in
`metadata.json.harvested_headers`. A lone candidate from an unrelated design
is rejected — do not hand-copy an unrelated repo's header. Design-specific
empty stubs (marked `minimal stub for ORFS`) are excluded from the index so a
stub verified safe for one design is never propagated to a sibling that may
actually depend on the real macros.

**Reconstructed headers must cover the whole family, not one module:** when a
header is hand-reconstructed to unblock one design (e.g. `rv32i_header.vh` for
`rv32i_alu`), it tends to define only the macros *that module* uses. Propagated
to a sibling that needs more (`rv32i_decoder` needs `` `OPCODE_RTYPE``,
`` `FUNCT3_* `` etc.) it fails again with `undefined macro`. Before
reconstructing, grep `` `BACKTICK_TOKEN `` usage across **all** RTL files of the
family and define the full union. ISA-standard values (RISC-V opcode/funct3
fields, CSR addresses) are fixed by the spec — reconstructing them is recovery,
not invention; arbitrary design-internal encodings are not and stay `skip`.

**Cascading missing headers:** synthesis aborts at the *first* unresolved
`` `include ``, so a naive one-error-at-a-time retry only reveals one header per
run. `setup_rtl_designs.py` instead scans **all** RTL files up front and resolves
(or reports) every referenced header in one pass — diagnose the same way: grep
all `` `include `` directives across every RTL file before re-running.

### PDN strap insufficient width (PDN-0179 + "Insufficient width to add straps")

**Symptoms:**
- `[ERROR PDN-0179] Unable to repair all channels`
- Or: `[ERROR PDN-0185] Insufficient width (N um) to add straps on layer metal4`
- Fails in `floorplan` stage (`2_4_floorplan_pdn`)
- Seen on VTR/koios large-mac and CNN benchmarks with many IO pins on a 50×50 die

Both codes are die-sizing problems: `fix_orfs_failures.py` → `apply_other`
routes `PDN-0179` and `PDN-0185` through a wrong-top check, then `apply_pdn_fix`
(switch to `CORE_UTILIZATION` so ORFS auto-sizes the die wide enough for straps).

**Root Cause:**
The PDN generator requires minimum widths between IO blockages to route metal straps. Tiny die + high IO count leaves no room.

**Action:**
- Switch to `CORE_UTILIZATION = 15` so ORFS sizes the die to the cell count.
- For designs that remain stuck, increase to `CORE_UTILIZATION = 20` and add `export PDN_CFG = ...` only if user supplies a custom PDN (rarely needed for nangate45 batch).

### Stage timeout (exit code 124)

**Symptoms:**
- `ERROR: Stage '<name>' failed (exit code 124) after 3600s`
- No final error message; process killed by `timeout`
- Common stages: `synth` for memory-heavy designs; `place` for FIFO/ethernet FIFO designs; `route` for routing-congested designs

**Root Cause:**
Default per-stage `ORFS_TIMEOUT=3600s` isn't enough for:
- Synthesis of designs with large memories (SYNTH_MEMORY_MAX_BITS=131072 expands 256×512b RAMs to 131K flops)
- Placement of FIFO designs (eth_mac_1g_fifo, axis_ram_switch) where FF-based memories consume 50K+ cells
- Routing of dense AXIS datapaths (~5K-10K cells)

**Action:**
- Raise `ORFS_TIMEOUT` per bucket:
  - Synth-timeout designs (arm_core, koios_gemm_layer): `ORFS_TIMEOUT=14400` (4h) and stay resume-ready
  - Place-timeout FIFO designs (axis_ram_switch, eth_mac_*_fifo): `ORFS_TIMEOUT=14400` + drop `SYNTH_MEMORY_MAX_BITS` to `32768` to shrink cell count
  - Route-timeout designs (axis_*fifo_adapter, zipcpu_wbdmac): use `FROM_STAGE=route ORFS_TIMEOUT=14400` to resume after CTS
- Keep `PLACE_DENSITY_LB_ADDON = 0.25` to give the placer more slack and converge faster.
- For small iscas89 designs that still time out, the issue is usually density oscillation — bump utilization to 20 and density to 0.25.
- Per-stage elapsed in `backend/RUN_*/stage_log.jsonl` tells you which stage timed out; always read this before choosing a fix.

### Concurrent runs sharing DESIGN_NAME overwrite each other's config.mk

**Symptoms:**
- Random stage failures in parallel batches, especially PPL-0024 with a "current perimeter" that doesn't match the value in the project's `constraints/config.mk`
- Flow seems to proceed for some stages then fails at a later stage
- Reproducible only when multiple cases share the same `DESIGN_NAME` (e.g., all ICCAD benchmarks set `DESIGN_NAME = top`)

**Root Cause:**
`run_orfs.sh` historically copied each project's config.mk to `$FLOW_DIR/designs/<platform>/<DESIGN_NAME>/config.mk`. That path is shared across every case that reuses the same DESIGN_NAME. When jobs A and B run concurrently, B can overwrite the shared file between two of A's `make` invocations (ORFS runs one `make` per stage, re-reading the file each time). FLOW_VARIANT isolated working directories but not the design config path itself.

**Action:**
- Fixed in `scripts/flow/run_orfs.sh`: the design copy path is now `$FLOW_DIR/designs/<platform>/<DESIGN_NAME>/<FLOW_VARIANT>/` so each project case has a unique config.mk even when DESIGN_NAME collides.
- If you see stale results that passed under the old script, re-run the case to confirm — its final GDS may have been produced against the wrong config.
- The per-case `flock` in `tools/batch_orfs_only.sh` also prevents two jobs from sharing the same project directory, but shared-DESIGN_NAME races were only visible at the ORFS-copy layer.

### Wrong top module selected (HLS-generated / multi-module files)

**Symptoms:**
- PDN strap or place density failure on a design whose RTL file contains many module definitions
- `design_meta.json` `top` field names a small leaf module (e.g., `Fadder_`, `cast_ap_fixed_...`)
- Synthesized cell count is tiny relative to the RTL file size (e.g., 3 cells from a 180K-line file)
- GDS/floorplan is disproportionately small

**Root Cause:**
`design_meta.json` was auto-generated by a tool that picked an arbitrary leaf module as `top` instead of the actual top-level design module. This is common for:
- HLS-generated Verilog (Vivado HLS outputs many utility modules before the real top, e.g., `myproject`)
- VTR/Odin benchmark files that bundle dozens of building-block modules with a top-level MAC/ALU (e.g., `mac1`, `mac2`)
- Multi-module files where the intended top is the last or largest module

**Action:**
- Identify the correct top module by looking for the module that instantiates others, has the most ports, or matches the design name. In HLS designs, look for `myproject` or similar.
- Update `DESIGN_NAME` in `constraints/config.mk` and `current_design` in `constraints/constraint.sdc`.
- Check the clock port name — HLS designs often use `ap_clk` instead of `clk`.
- Re-run with a full clean (remove old `backend/RUN_*/` directories).

**Known cases:** `koios_lenet` (correct top: `myproject`, clock: `ap_clk`), `large_mac1` (correct top: `mac1`), `large_mac2` (correct top: `mac2`).

### LFSR / CRC parametric function expansion in Yosys AST frontend

**Symptoms:**
- Yosys sits in `AST frontend in derive mode using pre-parsed AST for module '\lfsr'` (or similar parametric module) for 20+ minutes with no other output
- `1_synth.log` shows repeated `Executing AST frontend in derive mode` lines for the same module under different parameter sets
- Per-stage timer in `stage_log.jsonl` is empty (synth still running) while `flow.log` last line is an AST derivation
- Common in Alex Forencich `verilog-ethernet` designs that instantiate `lfsr.v` with varied `DATA_WIDTH` inside `generate for` blocks (e.g. `axis_baser_tx_64` uses 8 widths from 8 to 64 inside a genvar loop)

**Root Cause:**
`lfsr.v` computes its mask matrix with a pure-Verilog `function` that contains nested loops `O(LFSR_WIDTH × DATA_WIDTH × DATA_WIDTH)` (~2-8K iterations per instance). Each parameterized instantiation forces Yosys to re-derive the AST and fully unroll the function at elaboration time, since the mask is evaluated at constant-folding time. Per-instance derivation is already slow; a genvar loop with N distinct DATA_WIDTHs multiplies the cost linearly.

**Action:**
- Raise `ORFS_TIMEOUT` to 14400s (4h) for any `verilog_ethernet_axis_baser_*` or `verilog_ethernet_*_fcs` design using the genvar-parameterized lfsr pattern.
- If repeated across many widths in a genvar loop, rewrite the loop as N explicit instantiations — slightly more verbose but lets Yosys cache the derivation per-parameter set (only marginal improvement on recent Yosys).
- Do not downgrade `ABC_AREA` or `SYNTH_MEMORY_MAX_BITS` — they do not affect this front-end hotspot.

**Known cases:** `verilog_ethernet_axis_baser_tx_64` (~8 lfsr widths × genvar loop).

### Synth timeout triage: AST pathology vs scale timeout (radar split)

`exit code 124` during synth covers **two very different failure modes** that
must be handled differently. The radar in `tools/fix_orfs_failures.py`
(`_classify_synth_timeout`, added 2026-04-19) splits them automatically —
agents should read `hang_class` in `rtl_error_context.json` and branch
before doing anything else.

| Field in `rtl_error_context.json` | `ast_pathology` | `scale_timeout` |
|---|---|---|
| `hang_class` | `"ast_pathology"` | `"scale_timeout"` |
| `n_post_ast_progress` | **0** (or 1–2 stragglers) | **≥ 3**, typically 50–300 |
| `last_progress_marker` | `null` | e.g. `"12.13. Executing OPT_DFF pass…"` |
| `focus_file` / `focus_line` | set (last AST-derive module) | **`null` — intentionally suppressed** |
| `recovery_hint` | `null` (use the focused module) | populated (config-level recipe) |

**Detection rule**: after the last `Executing AST frontend in derive mode …
for module '\X'` line, count Yosys per-step progress markers (`OPT`,
`TECHMAP`, `ABC`, `SYNTH`, `PROC`, `FLATTEN`, `FSM`, `MEMORY`, `ALUMACC`,
`SHARE`, `DFFLIBMAP`, etc.). Three or more → `scale_timeout`. Fewer →
`ast_pathology`. Zero AST-derive lines at all → `unknown`.

**Why we need the split**: naming "the last AST-derive module" as the
suspect is correct for `ast_pathology` (Yosys really did freeze there)
but actively harmful for `scale_timeout` — the last AST-derive module is
usually a small, innocent leaf (typically hierarchically or
alphabetically last) that the agent will then try to rewrite, wasting
cycles and risking regressions in otherwise-correct code. For
`scale_timeout` the real hang point is tens of thousands of gates and
dozens of passes later, in a flattened netlist where no single RTL
module is the suspect.

**Action for `ast_pathology`**: fix the focused RTL module (see the LFSR
section above and the HLS megadesign section below for the two dominant
sub-cases).

**Action for `scale_timeout`** (use the `recovery_hint` as a checklist):
1. Raise `ORFS_TIMEOUT` to 14400 s (4 h) or 28800 s (8 h) for megadesigns.
2. Enable `SYNTH_HIERARCHICAL=1` with `ABC_AREA=0` to keep repeated
   sub-units (systolic PEs, MAC arrays) from flattening into an N-way
   pairwise OPT target.
3. If still too slow, factor the top — synthesize compute
   (`matmul_*_systolic`, `gemm_*_core`) separately from AXI/bus wrappers
   and tie them together at top level.
4. **Do not** edit `last_ast_module` as if it were the suspect. Do not
   lower `SYNTH_MEMORY_MAX_BITS` (only hides memories). Do not disable
   ABC (ABC already completed cleanly by the time `OPT_DFF` times out).

**Known `scale_timeout` cases**:
- `koios_gemm_layer` — 400-PE BF16 systolic array, hand-written
  (non-HLS). Terminates at `12.13 OPT_DFF` after ~90 successful sub-steps
  and ~210 post-AST progress markers. Pre-fix radar falsely pointed at
  `FPMult_PrepModule` (68-line pure-combinational leaf). Recovery:
  `ORFS_TIMEOUT=14400` + `SYNTH_HIERARCHICAL=1`.
- `arm_core`, `verilog_axis_axis_ram_switch` — legitimately large; same
  recipe.
- `koios_lenet` and other HLS megadesigns — see the HLS section below
  for the variant that prefers `SYNTH_HIERARCHICAL=0 ABC_AREA=0` because
  HLS output lacks exploitable hierarchical repetition.

### Place-stage apparent-hang (not actually a hang): global_place timing-driven resizer

**Symptoms:**
- After Nesterov global placement converges (e.g., `Iter: 451, overflow: 0.635`), OpenROAD prints the header `Iteration | Area | Resized | Buffers | Nets repaired | Remaining` followed by one line `0 | +0.0% | 0 | 0 | 0 | <N>` where N is the total net count.
- Then the log emits **nothing** for 2-5 hours while the OpenROAD process runs at 99%+ CPU with memory growing.
- Eventually a `final | ...` line appears with the completion numbers, and a similar header+iter-0 line for the next pass starts.
- Looks like a hang; is actually CPU-bound work inside `global_place.tcl`'s timing-driven repair_design.

**Root Cause:**
The resizer scans all N nets, builds RC parasitic estimates, and evaluates timing paths before making its first printable edit. For 1M+ instance designs on nangate45 this scan is ~3-4 hours per pass on a machine that can give the process ~4 cores of steady CPU.

**Action:**
- Do **not** cancel in under 2 hours for >500K-instance designs.
- Place-stage budget scales with cell count:
  - ≤ 200 K instances: `ORFS_TIMEOUT=14400` (4h) is usually enough.
  - 200 K – 1.1 M: use `ORFS_TIMEOUT=28800` (8h).
  - 1.1 M – 1.5 M: use `ORFS_TIMEOUT=57600` (16h).
  - Beyond 1.5 M: split the top into separately-synthesized sub-blocks; do not rely on budget alone.
- `SKIP_LAST_GASP=1 SKIP_INCREMENTAL_REPAIR=1` help slightly by dropping post-placement optional repair passes, but they do NOT shorten the primary per-pass scan.
- `SYNTH_HIERARCHICAL=1 + ABC_AREA=0` are still important (for synth of repeated-PE designs); they are orthogonal to this place-stage bottleneck.

**Known cases:**
- `koios_gemm_layer` (1.12 M instances): place took 19,695 s (5h28m), 6 final markers = 3 sub-passes × 2 timing-driven iterations. Passed with `ORFS_TIMEOUT=28800`.
- `arm_core` (1.25 M instances): place could not complete iter 1 within 28,800 s; requires 57,600 s (16h) budget, demonstrated in v2 retry.

### HLS megadesign (100K+ line RTL, 100+ modules)

**Symptoms:**
- RTL file is >100K lines with dozens of similarly-named auto-generated modules (e.g., `cast_ap_fixed_*`, `conv_2d_latency_*`)
- Yosys synthesis still running after 2+ hours with no error
- ABC technology mapping alone takes >1h
- Even with `SYNTH_MEMORY_MAX_BITS=131072` synth timeouts at 14400s

**Root Cause:**
HLS tools (Vivado HLS, Bambu) generate flat, hierarchy-heavy Verilog with thousands of micro-modules and inlined memories. Yosys front-end scales poorly: module loading time is quadratic in module count, ABC mapping scales with pre-mapping cell count (millions post-unfolding).

**Action:**
- These designs need dedicated multi-hour synthesis runs, not bulk batch.
- Preferred: feed a pre-synthesized gate-level netlist (Vivado HLS can output one) directly to ORFS floorplan.
- If RTL-to-GDS is required, run with `ORFS_TIMEOUT=28800` (8h) on a dedicated host, `SYNTH_HIERARCHICAL=0`, and `ABC_AREA=0` (delay mode is faster on huge designs).
- Mark as "megadesign/long-run" in batch tracking; do not count against a bulk batch pass rate.

**Known cases:** `koios_lenet` (~227K-line LeNet from HLS, 117 modules), large Bambu-generated designs.

### Zero-logic design (wire-only / trivial combinational)

**Symptoms:**
- `[ERROR FLW-0024] Place density exceeds 1.0` even with explicit small `DIE_AREA`
- GPL log shows `Standard cells area: < 10 um²`
- Design is pure combinational with `assign out = in;` or similar trivial logic

**Root Cause:**
The design synthesizes to zero or near-zero standard cells. There is nothing meaningful to place, so the placer reports density overflow regardless of die area settings.

**Action:**
- These designs cannot go through P&R — they have no physical implementation.
- Mark as "trivial/skip" in batch results. Do not retry.
- If the design is intended to have logic, check if the correct top module was selected.

**Known cases:** `clog2_test` (`simple_op` = `assign out = in`).

## Batch-Campaign Fix Tool

`tools/fix_orfs_failures.py` classifies every failure in `design_cases/_batch/orfs_results.jsonl` by scanning its log for the six signatures above, then rewrites the offending `constraints/config.mk` in place. Run it any time a batch produces failures:

```bash
python3 tools/fix_orfs_failures.py
# Then re-run only the failed cases:
grep -oE '"case": "[^"]+"' design_cases/_batch/orfs_results.jsonl \
  | sed 's/.*"\([^"]*\)"/\1/' > design_cases/_batch/failed_cases.txt
DESIGNS_LIST=design_cases/_batch/failed_cases.txt \
  bash tools/batch_orfs_only.sh 8 7200
```

Empirical fix yield (from the 93-failure retry): memory/place-density/io-pin fixes each converge in one retry; 6 missing-include designs require real header files and cannot be stub-fixed.

### Antenna DRC Violations

**Symptoms:**
- DRC report shows METAL*_ANTENNA violations (e.g., METAL4_ANTENNA, METAL5_ANTENNA)
- All violations are antenna-rule related; no spacing/width violations
- Violation counts vary across configs of the same design (layout-dependent)

**Root Cause:**
Long unbroken metal routes accumulate charge during plasma etching, which can damage thin gate oxides. Normally OpenROAD's `repair_antennas` fixes this by inserting antenna diodes during global/detailed route.

**nangate45 caveat (verified 2026-06-01 — see `docs/campaign_signoff_fixer_2026-06-01.md` Finding B):**
On nangate45, OpenROAD antenna repair is **INERT**, so raising repair iterations does nothing:
- The nangate45 **tech LEF has no antenna rules** (`grep -ci ANTENNA NangateOpenCellLibrary.tech.lef` = 0), so `check_antennas` always reports **0 net / 0 pin violations** — OpenROAD detects nothing to repair.
- The only diode, `ANTENNA_X1`, has **`ANTENNADIFFAREA 0.0`** (placeholder), so `repair_antennas` rejects it (`ERROR GRT-0244`, `WARNING GRT-0246 No diode … found`) and inserts zero diodes.
- These METAL*_ANTENNA violations are therefore visible **only to KLayout** `FreePDK45.lydrc` (300:1); the OpenROAD flow cannot see or fix them. The 2026-05-30 400:1 relaxation merely dragged KLayout toward OpenROAD's "0 antennas" view (masking).

**Automated fix:** `scripts/flow/fix_signoff.sh` (see `references/signoff-fixing.md`). The 400:1 antenna-ratio relaxation is RETIRED — real layout fixes only.

**Action (real-layout path):**
- **nangate45:** the only tool-agnostic lever is **layout relief** — lower `CORE_UTILIZATION`
  by 5 (floor 5) / grow `DIE_AREA` so the router can break long metal runs; re-run from
  floorplan. `PLACE_DENSITY_LB_ADDON` is never touched (hard rule: never below 0.10). The
  fixer skips the inert diode strategy on nangate45 and goes straight to density relief; if
  relief cannot clear it, report an **honest residual** (do not relax the deck).
- **Platforms with working antenna repair** (non-zero-diffarea diode + tech-LEF antenna
  rules, e.g. sky130/asap7): raise repair-antennas iterations
  (`MAX_REPAIR_ANTENNAS_ITER_GRT/_DRT = 10`, default 5); the diode is auto-discovered from
  its `CLASS CORE ANTENNACELL` LEF declaration (do NOT set `CORE_ANTENNACELL` — not an ORFS
  env var).
- Do NOT relax the DRC rule deck. The honest 300:1 deck is the reference; install via
  `tools/install_nangate45_drc.sh`.

### Hold Timing Violations Post-CTS

**Symptoms:**
- `6_report.json` shows `finish__timing__hold__tns < 0` and `finish__timing__hold__ws < 0`
- Hold violation count > 0 in final timing report
- High clock skew (>1ns) reported
- Designs with many macros (>20) are most affected

**Root Cause:**
Macro-heavy designs (swerv, bp_multi_top) have high clock skew (~1.5ns) due to macro placement spreading clock sinks far apart. CTS cannot fully equalize the skew, leaving hold violations. Designs that require `SKIP_CTS_REPAIR_TIMING=1` (OpenROAD crash workaround) are especially affected since hold repair is also skipped.

**Action:**
- If `SKIP_CTS_REPAIR_TIMING=1` was set as a crash workaround, check if the OpenROAD version has been updated to fix the SIGSEGV
- Increase `CTS_CLUSTER_SIZE` or `CTS_CLUSTER_DIAMETER` to reduce clock skew
- Add post-CTS hold margin: increase SDC hold uncertainty with `set_clock_uncertainty -hold 0.05 [all_clocks]`
- As a last resort, increase clock period to give more hold margin

### Unconstrained Timing (Silent Clock Mismatch)

**Symptoms:**
- Backend completes successfully with GDS output
- `6_report.json` shows very large positive WNS (e.g., 1e+38) — effectively unconstrained
- Power analysis shows >50% leakage ratio (zero switching activity assumed)
- SDC `create_clock` targets a port name that doesn't exist in RTL

**Root Cause:**
The SDC `clk_port_name` doesn't match the actual RTL clock port. OpenROAD silently skips the clock constraint when the port isn't found (no hard error), so the entire flow runs without timing constraints. Common mismatches:
- SDC uses `clk` but RTL has `clk_i` (ac97_ctrl, mem_ctrl, simple_spi_top)
- SDC uses `clk` but RTL has `wb_clk_i` (i2c_verilog)

**Action:**
- Verify clock port: `grep 'input.*clk' rtl/design.v` and compare with SDC `clk_port_name`
- Fix SDC to use the exact RTL port name
- Re-run synthesis and backend
- Prevention: add a pre-flight check in `run_orfs.sh` that warns if SDC clock port is not found in RTL

### Setup Timing Violations (Tiered WNS + TNS Response)

`check_timing.py` classifies timing into tiers based on the **worse of** the WNS tier and the TNS tier. A design with small WNS but large TNS (many slightly-violating paths) is treated as severely as one with large WNS.

**Thresholds (defaults, overridable via `--wns-threshold` and `--tns-threshold`):**

| Metric | Minor | Moderate | Severe |
|--------|-------|----------|--------|
| WNS | -2.0 to 0 ns | -5.0 to -2.0 ns | < -5.0 ns |
| TNS | -10.0 to 0 ns | -100.0 to -10.0 ns | < -100.0 ns |

#### Minor Setup Violations (combined tier = minor)

**Criteria:** WNS >= -2.0 AND TNS >= -10.0 (both metrics are minor or clean)

**Agent Action (automatic — no user interaction):**
1. Read `suggested_clock_period` from `reports/timing_check.json`
2. Update `clk_period` in `constraints/constraint.sdc` to the suggested value
3. Re-run synthesis and backend
4. Re-run `check_timing.py` to verify fix worked
5. Report the change to the user after the fact

#### Moderate Setup Violations (combined tier = moderate)

**Criteria:** WNS >= -5.0 AND TNS >= -100.0, but at least one metric is moderate

**Common scenario — TNS escalation:** WNS is only -0.5ns (minor) but TNS is -50ns (moderate) because 100 paths each violate by 0.5ns. The design looks "almost clean" by WNS alone but has widespread timing failure.

**Agent Action (stop and present options):**
1. Print the numbered `options` from `timing_check.json` to the user
2. Note which metric (`wns_tier` vs `tns_tier`) drove the escalation
3. Wait for the user to choose an option number
4. Apply the chosen fix and re-run backend, OR accept violations and proceed

**Typical options presented:**
1. Increase clock period to X ns (calculated)
2. Reduce CORE_UTILIZATION to Y%
3. Both: increase period + reduce utilization
4. Accept violations and proceed to signoff anyway (risk: high)
5. Stop flow and restructure RTL

#### Severe Setup Violations (combined tier = severe)

**Criteria:** WNS < -5.0 OR TNS < -100.0 (at least one metric is severe)

**Agent Action (stop and present options with strong warning):**
- Same options as moderate, but with stronger warnings and "stop and restructure RTL" recommended
- If WNS exceeds 50% of clock period, flag that architectural changes are needed

**Escalation Criteria:**
- **Moderate:** Agent may attempt one auto-tuning iteration (config change) if user picks option 1/2/3. If still moderate after retry, present options again.
- **Severe:** Always escalate to user immediately. Do not attempt auto-tuning.
- **Large TNS with small WNS:** Indicates widespread shallow violations. Increasing clock period is usually effective (all paths get more margin). This is noted in the options.
- **Large WNS with small TNS:** Indicates one deep critical path. Clock period increase alone may not help — RTL restructuring may be needed.

### Severe IR-Drop (>10% VDD)

**Symptoms:**
- `6_report.json` shows `finish__power__internal__total` is high relative to design size
- VDD worst-case voltage drop exceeds 10% of nominal (e.g., >0.11V on 1.1V supply)
- May cause functional failures in silicon at worst-case PVT corners

**Root Cause:**
Insufficient power delivery network (PDN) for the design's power density. Common in AES/crypto designs with high toggle rates and dense placement.

**Action:**
- Reduce placement density: lower `CORE_UTILIZATION` to spread cells
- Strengthen PDN: add more power straps or increase strap width (platform-dependent config)
- If possible, increase die area to reduce power density
- Check that `PLACE_DENSITY_LB_ADDON` is not causing excessive local density

## KLayout DRC Timeout on nangate45 (FreePDK45.lydrc)

**Symptoms:**
- `run_drc.sh` times out at 3600s even for small designs (~13K cells)
- KLayout log shows "or" operations in FreePDK45.lydrc still processing at timeout
- Or hangs specifically at `FEOL checks` / `"or" in: FreePDK45.lydrc:91`
- No `6_drc.lyrdb` or `6_drc_count.rpt` produced
- For large ethernet/AXIS designs (arp-class, ~243K nets), even `DRC_TIMEOUT=7200` (2h) is not enough — the `FEOL checks` step alone runs for >2h at ~90% CPU.

**Root Cause:**
The FreePDK45.lydrc DRC rule deck involves expensive polygon boolean operations that scale with layout complexity and metal density, not just cell count. The default `DRC_TIMEOUT=3600` is insufficient for this rule deck on any design, and the FEOL (front-end-of-line) boolean on large layouts doesn't parallelize.

**Action:**
- Set `DRC_TIMEOUT=14400` (4h) or higher for nangate45 ethernet-scale designs
- DRC is the least critical signoff check — LVS and RCX are more important for correctness
- If DRC is not needed, skip it and rely on LVS+RCX for signoff
- **nangate45 LVS rule file (`FreePDK45.lylvs`) is NOT shipped with this ORFS checkout.** `run_lvs.sh` gracefully emits `lvs_result.json` with `status=skipped` in this case. If you need LVS on nangate45, obtain the adapted FreePDK45.lylvs from the reference library manually.

## Missing Hard-Memory Wrapper Stubs (BSG Macro Designs)

**Symptoms:**
- Yosys synthesis fails with `ERROR: Module '\hard_mem_*_wrapper' ... is not part of the design`
- Only affects designs using BSG-style pickled Verilog (black_parrot, bp_multi_top)

**Root Cause:**
BSG pickled.v files instantiate `hard_mem_1rw_*_wrapper` modules that bridge BSG memory interfaces (clk_i, v_i, w_i, addr_i, data_i, data_o, w_mask_i) to the platform's fakeram primitives. These wrappers are not included in the pickled file and must be provided separately.

**Action:**
- Write a `hard_mem_stubs.v` file containing real module implementations (NOT blackbox attributes) for each wrapper, plus blackbox declarations of the fakeram45 primitives
- The wrappers map BSG ports to fakeram45 ports: `clk_i→clk`, `v_i→ce_in`, `w_i→we_in`, `addr_i→addr_in`, `data_i→wd_in`, `w_mask_i→w_mask_in`, `data_o→rd_out`
- For wrappers without bit-mask ports (e.g., `hard_mem_1rw_d512_w64_wrapper`), broadcast `{N{1'b1}}` to `w_mask_in`
- For byte-mask wrappers, expand byte enables to bit enables
- Add stubs path to `VERILOG_FILES` in config.mk

## LVS Timeout on Very Large Designs (>150K cells)

**Symptoms:**
- `run_lvs.sh` exits with code 124 (timeout) even though KLayout is making progress
- KLayout uses 100% CPU and 5-6 GB RAM throughout the run
- Log stops at "Flatten schematic circuit" messages — the heavy compare phase produces no output until completion

**Root Cause:**
KLayout LVS scales super-linearly with cell count. Empirical data:
- 145K cells (swerv): 57 min solo
- 282K cells (black_parrot, SYNTH_HIERARCHICAL+ABC_AREA): >8 hours, did not complete

The compare phase produces no log output — only "Flatten schematic circuit" lines appear before the silent phase.

**Action:**
- **>250K cells:** skip KLayout LVS — it is impractical (>8 hours and may never finish). Accept ORFS+RCX pass as sufficient evidence, especially when smaller designs in the same family pass LVS clean.
- **150K-250K cells:** run LVS solo with `LVS_TIMEOUT=14400`. Expect 60-120 min.
- **<150K cells:** default timeout (3600-7200s) is sufficient.
- Never run multiple LVS jobs concurrently for >100K cell designs.
- The process is NOT stuck if CPU is at 100% — the compare phase is silent until completion.
- To reduce cell count, try removing `SYNTH_HIERARCHICAL=1` and `ABC_AREA=1` from config.mk.
