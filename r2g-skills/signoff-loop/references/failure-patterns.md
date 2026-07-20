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

### Sub-variant: sky130hd route-dense designs (crypto SPN), 5-layer stack

<!-- r2g-lesson:
id: lesson-sky130-route-dense
status: active
trigger: {check: route, platform: sky130hd}
strategy_ids: [route_relief]
-->

- **Symptom (sky130hd/sky130hs):** `place` passes at a healthy 15-25% utilization but `route`
  fails — either `[ERROR GRT-0116] Global routing finished with congestion` or, more often, a
  `route` **timeout (exit 124)** after the global router spins all 30 extra iterations without
  clearing overflow (`Routability final weighted congestion ≈ 0.97`).
- **Root cause:** sky130hd exposes only **5 routing layers** (met1–met5) vs nangate45's ~10, so a
  design that routes cleanly on nangate45 at the *same* `CORE_UTILIZATION` can be hopelessly
  congested here. Substitution-permutation crypto cores (AES `aes_encipher_block`, DES `des_area`)
  are the worst case: dense XOR / S-box / GF-multiply fan-out creates very high *local* routing
  demand that area alone does not relieve. These are **not** the die-sizing floor bug (that aborts
  at `place`/DPL-0036) — placement succeeds; the wires simply do not fit.
- **Learned signal:** the knowledge store's `failure_candidates.json` auto-clusters these as
  `orfs-fail-route` (config median `CORE_UTILIZATION = 25`) once the `failure_events` are populated
  — i.e. high-util designs dominate the route-fail population. Lowering utilization is the lever the
  data points to.
- **Action:** drop `CORE_UTILIZATION` aggressively (≤ 8-10) to open routing channels and give the
  route stage a larger `ORFS_TIMEOUT`. Re-run **from a clean backend** (a `FROM_STAGE=floorplan`
  resume silently reuses the cached dense placement — verify `place` actually re-runs, not 3 s of
  cache).
- **Validated honest-final (2026-06-13):** `aes_encipher_block` does NOT close on sky130hd even at
  `CORE_UTILIZATION = 8` from a clean backend — global placement never converges (overflow
  oscillates ~0.51) because `[INFO GPL-0047] Routability iteration weighted routing congestion`
  stays **> 1.0 (1.01)**: the design demands more routing than 5 metal layers can supply, at *any*
  utilization. `des_area` behaves identically. These are genuine `orfs_route` residuals, not a
  fixable config — recorded honestly (the knowledge store carries them as `orfs-fail-route`); do not
  relax signoff to force a dirty GDS. Revisit only if more routing layers (e.g. an HD variant with
  met6+) or a hierarchical/partitioned floorplan becomes available.

### Sub-variant: route TIMEOUT (exit 124) ≠ congestion — the `route_relief` learnable recipe (2026-06-17)

<!-- r2g-lesson:
id: lesson-route-timeout-relief
status: active
trigger: {check: route, platform: "*"}
strategy_ids: [route_relief]
-->

- **The mislabel:** a route-stage abort is reported by the driver as "Routing congestion detected",
  but the **common** cause is the wall-clock `timeout` (exit **124/137**) killing **detailed
  routing** mid-grind, *not* a global-route `GRT-0116` abort. On a 33-design sky130hd cluster
  (2026-06-17) **all 33 cleared global route (GRT)** and died in DRT — several (`diffeq1`,
  `secworks_aes_key_mem`, `opdb_2dmesh`) had already reached **0 DRT violations** when the wall-clock
  killed them. `run_orfs.sh` now distinguishes the two modes in its HINT (timeout vs GRT-0116).
- **`route_relief` (the fix, now A/B-validated):** lower `CORE_UTILIZATION` one density step
  (−8, floor 8) so DRT has room to converge inside the budget; rerun from floorplan. SAME lever as
  `density_relief` but keyed to a **route-stage abort** (symptom `check=orfs_stage, class=route`),
  which never reaches signoff DRC — so before this the closed-loop A/B machinery was structurally
  blind to it. Drive it with:

  ```
  scripts/flow/fix_signoff.sh <project> sky130hd --check route
  ```

  It diagnoses `route_relief`, applies the util drop, re-routes, and logs a `fix_log.jsonl` row
  (`check=orfs_stage/route`) so the learner derives the recipe and the A/B loop can validate it.
- **Validated 2026-06-17:** `wb2axip_wbsafety` (1183 cells) timed out at route at `CORE_UTILIZATION
  = 25` (5400 s, 28 DRT residual); at util 12 the **route completed clean in 37 s** — a
  timeout→clean flip from one knob. The route_relief recipe rode the closed loop end-to-end:
  fix_log → learner enqueued a `route_relief` candidate → `engineer_loop ab-drain` recorded an
  `ab_trials` **win** (arm B route_relief routes; arm A control times out). DIE_AREA-sized designs
  (no `CORE_UTILIZATION` knob, e.g. `secworks_sha512_w_mem`) are an honest residual here — the v2
  lever is to enlarge `DIE_AREA`.
- **Still honest residual:** a design that demands more routing than 5 metal layers can supply at
  *any* utilization (confirmed `aes_encipher_block`: GPL routability stays > 1.0) does NOT clear
  with route_relief — `route_relief` steps to the util floor and stops (no deck relaxation). Tell
  the two apart by whether GPL routability converges < 1.0 at a lower util: timeout-victim (clears)
  vs layer-limited (honest residual).

### Sub-variant: a SUCCESSFUL recipe is unreachable by the A/B planner (2026-06-22, loop-closure bug)

The route_relief story above worked because a route-congestion abort leaves a **residual** —
a `run_violations` row the A/B planner's Tier 1 (`ab_runner.plan_trial`) keys on. The opposite
class of recipe — one that **fully clears** its symptom — silently broke the loop:

- **Symptom (campaign-level, not a single design):** all `ab_trials` were sky130hd; every
  nangate45 candidate (3× `antenna_diode_repair` DRC, 1× `period_relax` timing) sat in
  `recipe_status='candidate'` indefinitely. `ab_trials` froze across waves while `candidate`
  count grew — the Gate-A "candidates that never drain" alarm, scoped to one platform.
- **Root cause:** `plan_trial` Tier 1 reads `run_violations`, the **post-fix** residual snapshot.
  `antenna_diode_repair` drives DRC to **0**, so it leaves *no* residual row — Tier 1 is
  structurally empty for exactly the recipes worth promoting. The only fallback was Tier 2, a
  `heuristics.symptoms[sid].evidence_designs` **name-list keyed on the bare DESIGN_NAME** (`can_tx`).
  The campaign names project dirs `<SourceRepo>_<design>` (`CAN_Bus_Controller_can_tx`), and
  `_resolve_evidence` matched the evidence name only against the project-dir *basename* — so it
  resolved **0** designs (and generic module names `test`/`top` would over-match dozens of
  unrelated designs). With both tiers empty, `plan_trial` returned `None` → no arms → the recipe
  could never be A/B-validated or promoted, even though it demonstrably worked.
- **Why sky130hd escaped it:** its live candidates were `route_relief`/`density_relief`, which
  *reduce* (not zero) violations and so leave Tier-1 residuals. Single-platform A/B is the tell —
  a `fail`/`partial` corpus can have non-empty `ab_trials` and still be lying *per platform*.
- **Fix (`ab_runner.py`, new Tier 2 `_symptom_designs`):** resolve A/B subjects from
  `fix_trajectories`/`fix_events`, which record the **exact `project_path` that hit each
  `symptom_id`** (resolved *or* abandoned) — symptom-confirmed and on-disk-precise, regardless of
  dir-naming. Tier order is now run_violations → fix-history → evidence-name-list (last resort).
  A symptom only **one** design ever exhibited honestly stays unmatched at `n_designs=2` (it
  becomes A/B-able when a second exhibitor appears) rather than fabricating a subject. TDD:
  `tests/test_ab_fixhist_subjects.py`. **Lesson:** "`ab_trials` non-empty" is too weak a tripwire —
  it must hold *per platform that has a successful-recipe candidate*; a planner that keys only on
  residuals is blind to every recipe that actually fixes the problem.

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

### Sub-variant: FLW-0024 place-density overflow = die too small (2026-06-23, setup-sizing bug + loop recovery)

**Distinct from NesterovSolve divergence above** — do NOT touch `PLACE_DENSITY_LB_ADDON`.

- **Symptom:** `3_1_place_gp_skip_io.log` aborts immediately (~5 s) with
  `[ERROR FLW-0024] Place density exceeds 1.0 (current PLACE_DENSITY_LB_ADDON = 0.2)`.
  The synthesized cells do not FIT the die at all (density > 100%), so global placement
  can't even start. `stage_log.jsonl` shows `{"stage":"place","status":2}`.
- **Root cause (setup):** `tools/setup_rtl_designs.py:generate_config_mk` sized tiny/small
  designs with a **fixed `DIE_AREA`** (50×50 / 120×120) chosen from **RTL line count**.
  Line count is a terrible proxy for gate count — a <100-line design (wide multiplier, FFT
  butterfly, DMA datapath) synthesizes to thousands of cells that overflow a hardcoded
  50×50 µm die. Validated: `dma_controller`'s 50×50 (=2500 µm²) die was handed **6442 µm²**
  of cells (2.6× too big) → FLW-0024. Same class as the sky130 `mk_*_project` sizing bug.
- **This was the dominant `unseen_crash` bucket** (~38 of 81 open escalations): the loop
  escalated FLW-0024 place aborts as generic `unseen_crash`, so the learner saw novel
  symptoms instead of one characterizable, **recoverable** class.
- **Fix — two parts:**
  1. *Setup (prevent recurrence):* every size bucket now uses `CORE_UTILIZATION` (auto-size)
     so ORFS sizes a die that fits the synthesized cells; no bucket hardcodes a `DIE_AREA`.
     (`test_setup_sizing.py`.)
  2. *Loop recovery (drain the existing 708-project backlog):* `engineer_loop.process_one`
     detects FLW-0024 (`_is_flw0024`, reads the run's `flow.log`) on a `place` abort,
     rewrites `constraints/config.mk` `DIE_AREA`/`CORE_AREA` → `CORE_UTILIZATION=30`
     (`_resize_to_core_util` — **never** touches `PLACE_DENSITY_LB_ADDON`), and retries the
     flow ONCE. If it still overflows (cells exceed even the auto-sized routable die), it
     escalates honestly as `place_density_residual`, not `unseen_crash`. (`test_flw0024_recovery.py`.)
     Validated live: `dma_controller` at `CORE_UTILIZATION=30` auto-sized to 31 % util → placed.
- **Lesson:** distinguish FLW-0024 (die too small → enlarge die / auto-size) from NesterovSolve
  divergence (density floor too low → raise `PLACE_DENSITY_LB_ADDON`). They share the `place`
  stage but have opposite fixes; conflating both into `unseen_crash` blinds the learner to a
  recoverable class. (Mirrors the `route_congestion_residual` re-label.)

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
keep retrying. **Confirmed 2026-06-06** on `i2c_master_i2c_master` (a small
std-cell design) at `4_1_cts` in `TritonCTS::separateMacroRegSinks` *with both
`SKIP_CTS_REPAIR_TIMING=1` and `SKIP_LAST_GASP=1` already set* — OpenROAD
26Q1-2966-g29d97c45b3; recorded as an honest `orfs_status=fail` (no fix_event),
candidate to retry only on a newer OpenROAD build.

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

### Sub-variant: `STAGE_STATUS: unbound variable` after a synth-stage timeout (2026-06-17)

**Symptom:** a stage times out (exit 124/137) and instead of a clean
`ERROR: Stage 'synth' failed (exit code 124)` + a synth-timeout HINT, `run_orfs.sh`
crashes with `run_orfs.sh: line NNN: STAGE_STATUS: unbound variable`, exiting BEFORE
results / `ppa.json` / final status are recorded.

**Root cause:** `STAGE_STATUS` is `local` to `run_stage()`. The post-loop synth-failure
HINT block (module scope) referenced `$STAGE_STATUS`, which is out of scope there; with
`set -euo pipefail` the unbound reference aborts the script. The route-failure HINT block
correctly used the module-scope `MAKE_STATUS` (the propagated exit code); the synth block
did not. Surfaced by a campaign run with a deliberately low `ORFS_TIMEOUT` (synth itself
timed out). **Fix (2026-06-17):** module-scope HINTs use `$MAKE_STATUS`, never the
function-local `$STAGE_STATUS`. Guarded by `test_flow_journaling.py::
test_stage_status_not_referenced_outside_run_stage` (static scope check).

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
- **Fix (nangate45):** Set explicit `DIE_AREA = 0 0 50 50` and `CORE_AREA = 2 2 48 48` in config.mk instead of `CORE_UTILIZATION`

#### Sub-variant: sky130 small-core PDN strap floor (PDN-0185)

- **Symptom (sky130hd/sky130hs):** `[ERROR PDN-0185] Insufficient width (N um) to add straps on layer met4 in grid "grid" with total strap width 15.2 um and offset 13.6 um` → `do-2_4_floorplan_pdn` Error 1. Flow dies at floorplan **before** placement.
- **Root cause:** sky130hd's default PDN grid lays met4/met5 power straps that need a core wider than ~30 um. A small design under `CORE_UTILIZATION` produces a microscopic core (a 65-cell design ⇒ ~7 um wide) that cannot fit even one strap — and **switching to `CORE_UTILIZATION` does NOT help** (the generic batch-fixer's PDN remedy), because the core is small *because the design is small*, not because the die was hand-set too tight. The nangate45 advice (`DIE_AREA 0 0 50 50`) is also far too small for sky130's grid.
- **Fix:** Floor the die to a PDN-feasible size. `tools/mk_sky130_project.py` computes `core_side = sqrt(cell_count * 8um² / util)` and, when that falls below ~160 um, emits an explicit `DIE_AREA = 0 0 200 200` / `CORE_AREA = 10 10 190 190` (cordic-validated 200 um core clears met4 straps). Designs naturally larger than the floor keep `CORE_UTILIZATION` (auto-sized).
- **Validated:** `simple_i2c_slave` (65→436 cells) — was PDN-0185 at floorplan under CU=20; with the 200 um floor it ran clean through synth→floorplan→place→cts→route→finish, **timing clean, DRC 0, RCX complete** (~3.5 min).

##### Loop-side self-heal (2026-07-01 sky130 round, `engineer_loop.py`)

The setup-time floor above only protects projects materialized by `mk_sky130_project.py`. The **corpus re-point** used to start a fresh platform round (`tools/setup_rtl_designs.py --platform sky130hd --force`) re-points `config.mk` to `CORE_UTILIZATION` **without** applying the PDN floor, so a re-pointed *tiny* design (8-bit control logic, AMBA `apb_protocol`) auto-sizes a ~27 um die and hits `PDN-0185` at `2_4_floorplan_pdn`. `engineer_loop.process_one` used to file this as **`unseen_crash`** (it had detectors/recoveries for FLW-0024/PPL-0024/synth but none for PDN), blinding the learner — `ingest_run.py` still recorded the honest `orfs-fail-floorplan-PDN-0185` failure_event (so honesty stayed 5/5), but the escalation reason was a mystery instead of a classifiable, recipe-backed residual.

- **Fix (loop-side twin of the setup floor):** `process_one` now detects `PDN-0185` at the `floorplan` stage (`_is_pdn_strap_width`), **floors the die to an explicit `DIE_AREA = 0 0 200 200` / `CORE_AREA = 10 10 190 190`** (`_relieve_pdn_strap_width` — *dropping* `CORE_UTILIZATION`, the cause, NOT reusing it as FLW-0024 does; parses the reported insufficient width via `_pdn0185_insufficient_width` so a die already ≥ the 200 um floor is never SHRUNK — it escalates instead), retries the flow **once**, records a learnable `pdn_die_floor` fix (`_record_pdn_fix`, `check=orfs_stage` / `class=floorplan` — a DISTINCT symptom from the FLW-0024 place-resize), and, if the floored die STILL cannot lay straps, escalates the honest **`pdn_strap_residual`** (registered in `escalations.REASONS`, else `open_escalation` raises `ValueError` and crashes the worker — the exact `synth_memory_residual` latent-crash class).
- **Distinct lever per abort:** FLW-0024 = die too small for the CELLS (lower util → bigger die); PPL-0024 = perimeter too short for the PINS (target the demanded perimeter); PDN-0185 = die too NARROW for the power straps (floor the width — lowering util on a tiny design still can't guarantee ≥28.8 um).
- **Tests:** `tests/test_pdn0185_recovery.py` (10 — detector, width parse, floor/no-op-when-wide, recover+retry+learnable-row, honest residual).
- **Follow-up (open):** teach `setup_rtl_designs.py` to apply the same PDN floor at re-point time so the loop rarely has to self-heal it (the setup path and the loop path should size sky130 dies identically).

#### Sub-variant: sky130 high-pin-count floorplan (PPL-0024 on the PDN floor die)

- **Symptom (sky130hd/sky130hs):** `[ERROR PPL-0024] Number of IO pins (1521) exceeds maximum number of available positions (718). Increase the die perimeter from 800.00um to 2068.56um.` → `Stage 'place' failed (exit 2)`. No final GDS/ODB; the campaign driver records `residual_class=orfs_incomplete` (now `orfs_place` after the `extract_ppa.py` fail-stage fix).
- **Root cause:** The two sky130 floorplan constraints — *area* (cells) and *perimeter* (IO pads) — are **independent**, and the small-core PDN floor only satisfied area. A cell-area-tiny but **pin-huge** design (wide AXI/bus demuxes, packet routers: `verilog_ethernet_ip_demux` is 2979 cells but **1523 pads**; `verilog_ethernet_udp_ip_rx_64` is 3034 cells / **771 pads**) fits the 200 um floor on area, but the floor die's 800 um perimeter seats only ~718 pads. The prior materializer assumed high-pin ⇒ large-core ⇒ `CORE_UTILIZATION`; a high-pin **small-core** design fell through that assumption onto the 200 um floor and overflowed. The bit-blasted pad count is invisible in the RTL (`ip_demux` is ~57 port *declarations*, all wide buses).
- **Fix:** `mk_sky130_project.py` reads the true pad count from the source design's ORFS DEF (`PINS N` — buses already expanded) via `source_def_pins()`. When it exceeds the floor's ~718-pad capacity, the explicit die side is raised to `ceil(pins * 1.45 / 4 / 10) * 10` um (PPL's own recommended ~1.36 um/pad incl. corner margin, plus a safety factor). It is a strict **lower bound** that is a no-op for every ≤718-pad design, so all previously-clean designs keep their byte-identical 200 um floor. Examples: 1523 pads → 560 um die; 771 → 280 um; 325 → unchanged 200 um.
- **Validated:** `verilog_ethernet_ip_demux` (1523 pads, PPL-0024 → DIE 560) and `verilog_ethernet_udp_ip_rx_64` (771 pads → DIE 280) re-ran clean through full signoff; the other 134 wave designs (all ≤325 pads) were untouched. Discovered in the 2026-06-12 sky130 wave (waves 2–4).
- **Resolved 2026-06-13 (was "latent, not changed"):** the predicted large-core case arrived (see DPL-0036 below), so the always-0 `cell_count` read is now fixed. `mk_sky130_project.py` reads `geometry.instance_count` (where `extract_ppa.py` actually writes it) instead of the never-populated top-level `ppa.get("cell_count")`, with `source_def_components()` (logic cells from the source DEF, fillers/taps excluded) as a fallback for sources whose ppa.json predates that field. **Genuinely small designs are unaffected** — their real count is still < the ~640-cell floor threshold, so they keep the byte-identical 200 um floor; only large-core designs (which the floor *broke*) flip to utilization-sizing. The earlier worry that this would "flip 100+ validated small designs" was wrong: small designs sit below the threshold and do not flip.

#### Sub-variant: sky130 large-core over-packs the PDN floor (DPL-0036)

- **Symptom (sky130hd/sky130hs):** global placement converges but `[ERROR DPL-0036] Detailed placement failed.` aborts the `place` stage. The place log shows `Design area NNNNN um^2 ~100% utilization` and `GPL-0053 Target density … exceeds the maximum allowed 0.9900`. No final GDS/ODB.
- **Root cause:** the exact failure the PPL-0024 note predicted. With `cell_count` mis-read as 0 (see "Resolved" above), **every** design took the 200 um floor branch (core 180×180 = 32400 um²). A design whose real cell area approaches that — `iccad2015_unit14_in1` is 3106 logic cells ≈ 31825 um² → **101 % utilization** — cannot be legalized by detailed placement. The floor that protects *small* designs starves *large* ones.
- **Fix:** the `cell_count` read fix above. With the real count (~3106), `core_side = sqrt(cell_count·8/util) ≈ 352 um > 160 um`, so the design takes the `CORE_UTILIZATION` branch and ORFS auto-sizes a die that fits at the target density.
- **Validated:** `iccad2015_unit14_in1` — was DPL-0036 on the 200 um floor; with the fix it materializes `CORE_UTILIZATION = 20` and runs clean through signoff. Surfaced in the 2026-06-13 sky130 wave-5 (the first diverse wave to include a cell-dense `iccad2015` design).

> **Reporting note (extract_ppa fail-stage, 2026-06-13).** A failed ORFS stage writes a `*-failed.odb` (or nothing is collected back), so `extract_ppa.detect_orfs_progress`'s disk-ODB probe found no ODBs for the aborted `place` stage and mis-labelled the residual `orfs_synth` (the first-missing-ODB stage). `detect_orfs_progress` now reads the authoritative `stage_log.jsonl` first (real per-stage exit codes, the same source `ingest_run._derive_orfs_status` uses), falling back to ODB-probing only when no stage_log exists — so the residual stage matches the knowledge store (`orfs-fail-place-DPL-0036`).

#### Sub-variant: sky130 die under-sized for cells on the pin-heavy path (FLW-0024)

- **Symptom (sky130hd/sky130hs):** `[ERROR FLW-0024] Place density exceeds 1.0 (current PLACE_DENSITY_LB_ADDON = 0.2)` at `global_place_skip_io`, with the place log showing `Design area NNNNN um^2 >100% utilization`. No final GDS/ODB → `residual_class=orfs_place`.
- **Root cause:** a design that is **both** pad-heavy (>718 pins, so it takes the explicit-DIE path for PPL-0024) **and** cell-dense. The pin-aware sizing (PPL-0024 fix above) computed `side = max(PDN_floor, pin_side)` — sizing the die for the **pad perimeter only**, ignoring the cell-area demand. When the cells need a bigger core than the pads do, the die over-packs and place aborts. `sha256_stream` (777 pads → `pin_side` 290 um, but 12083 sky130 cells ≈ 78 000 um² need a ~650 um core) got a 290 um die → 108 % util → FLW-0024. sky130 std cells are ~4.5× nangate45 area, so a design comfortable on nangate45 can overflow here even though `instance_count` was read correctly — the bug is purely in the die-side formula, not the cell-count read (contrast DPL-0036 above, which *was* a cell-count read bug).
- **Fix (`tools/mk_sky130_project.py`):** compute `cell_side = ceil((core_side + 20) / 10) * 10` (the cell-area side at `cu_val`, +CORE_AREA margins) and size the explicit die as `max(PDN_floor, pin_side, cell_side)`. This composes the PPL-0024 (pads) and FLW-0024 (cells) constraints. Pin-tiny pad-heavy designs are unchanged (their `cell_side` < `pin_side`, so `verilog_ethernet_ip_demux` stays 560 um); large non-pin designs still take the `CORE_UTILIZATION` branch. Validated: `sha256_stream` 290 um (108 % util, FLW-0024) → 650 um (~19 % util) → place proceeds. Regression tests: `test_pin_heavy_and_cell_dense_die_sized_for_cells`, `test_pin_heavy_but_cell_tiny_die_stays_pin_driven`.

### Platform Not Found
- **Symptom:** Make error about missing platform
- **Fix:** Verify platform name matches a directory in `$ORFS_ROOT/flow/platforms/`
- Available: nangate45, sky130hd, sky130hs, asap7, gf180, ihp-sg13g2

## Signoff Check Failures

### Signoff baseline never established — fresh flow → DRC silently skipped (2026-06-17)

- **Symptom:** the `engineer_loop` flows a design clean, then "fixes" signoff, and the
  design escalates as `catalog_exhausted` with `reports/drc.json` left at
  `status: "unknown"` — **Magic/KLayout DRC was never actually run** (the project's
  `drc/` dir is empty). The fix loop's `fix_log.jsonl` shows `drc iter 1 strat none
  verdict stop_unknown`.
- **Root cause:** `fix_signoff.sh`'s `fix_one` only ran the signoff tool
  (`run_drc.sh` / `run_lvs.sh`) *after* `diagnose` returned a strategy to apply. A
  design freshly produced by `run_orfs` has no signoff report yet, so `_run_extract`
  yielded `status: "unknown"`; `diagnose` then STOPped (nothing to fix) and the check
  was **silently skipped** — never run. LVS happened to run only when a *stale*
  `lvs.json: "fail"` from a prior attempt was present. Surfaced by the wbsafety canary
  (2026-06-17): flow clean, DRC `unknown`, design escalated without DRC ever executing.
- **Fix (2026-06-17):** `fix_one` calls `_ensure_baseline <check>` first — if the
  report is missing or its status is empty/`unknown`, it RUNS the signoff tool once
  (via the `$RUN_DRC`/`$RUN_LVS` seams) to establish a real baseline, then extracts.
  Route is exempt (its baseline is the flow's own route stage). Confirmed live:
  wbsafety then ran KLayout DRC → **DRC CLEAN, 0 violations**. Guarded by
  `test_fix_signoff_logging.py::test_baseline_signoff_runs_when_no_report`.
- **Skill-level:** the loop now always checks DRC/LVS at least once on a fresh flow;
  a `stop_unknown` DRC verdict in a `fix_log` is the alarm that the baseline run was
  skipped.

### Stale prior-platform signoff report read as first-pass clean (2026-06-30)

- **Symptom:** a platform RE-TARGET round (`/r2g-debug PLATFORM=asap7` re-points the whole
  corpus's `config.mk` nangate45→asap7 and re-flows) marks designs **clean WITHOUT running
  asap7 signoff**. The `runs` rows read `drc=clean, lvs=clean, rcx=NULL`, but `reports/drc.json`
  and `reports/lvs.json` are dated to the PRIOR (nangate45) round and no fresh KLayout DRC
  (`*.lyrdb`) or asap7 LVS-skip marker exists. On the first asap7 wave **all 19 "clean" rows
  were fabricated this way** (every one's `reports/drc.json` was older than its own asap7
  `6_final.gds`). The honesty.py gates stayed green — they verify fail/event parity, not whether
  a *clean* verdict is real — so only an mtime cross-check (report vs the current GDS) exposes it.
- **Root cause (two compounding holes):**
  1. `engineer_loop._signoff_status` reads `reports/{drc,lvs}.json` with **no freshness/platform
     check**; the process_one first-pass gate `_mark_clean`s when both are clean — BEFORE `_run_fix`
     (hence before `fix_signoff._ensure_baseline`, the only GDS-mtime staleness guard, ever runs).
     Nothing deletes `reports/*.json` on a re-flow (`run_orfs.sh clean_all` wipes only ORFS's build
     tree; `setup_rtl_designs.py --force` does `mkdir(exist_ok=True)`), so the prior platform's
     clean/clean survives and short-circuits the fresh-signoff path. `ingest_run.py` then persists
     the stale verdict into committed `knowledge.sqlite`.
  2. Even after the gate falls through, asap7 LVS would STILL record `clean` not `skipped`, via
     two further holes that the gate fix exposed:
     - `run_lvs.sh` resolved `KLAYOUT_LVS_FILE` with `KLAYOUT_LVS_FILE=$(grep … platform/config.mk …)`;
       asap7's config has no such line, so under `set -euo pipefail` the no-match grep ABORTED
       run_lvs.sh **before** its graceful no-deck skip path — so `lvs/lvs_result.json=skipped` was
       never written. `fix_signoff._ensure_baseline` swallows the abort (`RUN_LVS … || true`), then
       `extract_lvs` parsed the lingering June-19 `lvs/6_lvs.lvsdb` into a false `clean`.
     - `extract_lvs.py` honored the skip marker only when NO KLayout log was present — a lingering
       nangate45 `lvs/6_lvs.log` would have defeated even a written skip marker.
- **Fix (2026-06-30, branch r2g-debug/asap7-round):**
  1. `_run_flow` now DELETES `reports/{drc,lvs,rcx,route,timing_check}.json` before re-flowing —
     the single upstream chokepoint every campaign flow passes through (and upstream of every
     `_ingest`). A re-flow makes any prior verdict stale by construction, so `_signoff_status`
     now returns `unknown`, the gate falls through to `_run_fix` → `_ensure_baseline` → FRESH
     platform-correct signoff, and ingest only ever reads fresh-or-absent reports. Platform-agnostic
     (also closes intra-platform reflow staleness). Arm dirs already exclude `reports/`, so it is a
     no-op for A/B arms.
  2. `run_lvs.sh`'s `KLAYOUT_LVS_FILE=$(grep …)` now ends with `|| true` so a no-match grep on a
     no-deck platform no longer aborts under `set -euo pipefail` — the graceful skip path is now
     reachable and writes `lvs/lvs_result.json=skipped`.
  3. `extract_lvs.py` skip gate now uses **mtime-precedence** (honor the skip marker when it is at
     least as fresh as the newest KLayout artifact), mirroring the netgen-vs-KLayout precedence — so
     the fresh asap7 skip beats a lingering nangate45 `6_lvs.log` → honest `lvs=skipped`.
  - Validated end-to-end on apb_master: fresh asap7 signoff → `drc` honest verdict + `lvs=skipped`.
    Tests: `test_engineer_loop.py::test_run_flow_invalidates_stale_signoff_reports`,
    `test_extract_lvs.py::test_fresh_skip_marker_beats_stale_klayout_log` (+ converse),
    `test_extract_lvs.py::test_run_lvs_klayout_lvs_file_resolution_tolerates_no_match`.
- **Skill-level alarm:** a `clean` run whose `reports/*.json` is OLDER than its own
  `backend/RUN_*/results/6_final.gds`, or an asap7/no-LVS-deck design recording `lvs=clean` instead
  of `lvs=skipped`, means a stale prior-platform report was trusted. Known gap (follow-up): the loop
  does not yet positively re-run RCX, so asap7 clean rows carry `rcx=NULL` (honest absence, not a
  false `complete`); other read-only reporting tools (`tools/run_signoff.sh`,
  `aggregate_signoff_results.py`, the dashboard) share the unguarded-read class but are off the
  learning path.
- **RECURRENCE 2026-06-30 (this alarm FIRED on A/B antenna arms — and the honesty gates did NOT catch it).**
  A stop-and-report status check found **8 asap7 rows with `lvs_status='clean'`** (impossible on asap7 —
  no LVS deck). All 8 were **A/B antenna arm dirs** (`..._abA_antenna__0` …), config.mk re-pointed to
  asap7 but carrying a stale nangate45 `reports/lvs.json` (`raw_status:text_match_found`) written today
  — i.e. the arm's signoff was read from stale prior-platform reports rather than freshly run/skipped for
  asap7. The bug-#4 `_run_flow` stale-report deletion did **not** cover this arm path. **Critically,
  `honesty.py` stayed 5/5 GREEN throughout** — its five gates check `fail`↔`failure_event` parity, NOT
  "is a *clean* row genuine," so fabricated cleans are invisible to them (the same blind spot that made
  bug #4 dangerous). Reconciled: deleted the 8 fabricated rows + their 16 stale report files; asap7 then
  read honest `lvs_clean=0 / lvs_skipped=47 / drc_clean=0`. **RECOMMENDED FIX (open, high-value):** add a
  sixth honesty gate — *a run on a platform with no LVS deck (asap7) MUST have `lvs_status ∈ {skipped,
  NULL}`; `clean`/`fail` is a contamination ALARM.* That single gate would have auto-caught this the
  moment it was ingested, converting a silent lie into a hard stop. Also harden the A/B arm
  create/ingest path to clear `reports/` before signoff (mirror the `_run_flow` fix) so arms cannot
  inherit a subject's cross-platform reports.
- **RECURRENCE 2026-06-30 — the DRC LEG (extractor freshness guard missing on `extract_drc`, unlike
  `extract_lvs`).** A stop-and-report check found **6 asap7 A/B antenna arm rows with
  `drc=clean, drc_violations=0, lvs=clean` — `drc_mode:full`**. On disk each arm had genuinely re-run
  `asap7.lydrc` (fresh `drc/drc_run.log`, 28s, real merged GDS) and its ORFS-side
  `flow/reports/asap7/<design>/<variant>/6_drc_count.rpt` = **25** (the documented irreducible floor:
  `LIG.LISD.S.7`, `LIG.SDT.S.8`, `V0.S.1`, `V1.S.4`, `M4.S.5`, `V2.M3.AUX.2`, `V4/V5.M*.AUX.2`) — yet
  `reports/drc.json` read `clean/0`. **Reproduced read-only:** `extract_drc.py <arm>` → `clean/0`.
  - **Root cause (the DRC twin of the LVS holes above):** these arm dirs were created in a
    *pre-copytree-fix* wave, so their `not dst.exists()` copytree was skipped and a **June-19
    `drc/6_drc_count.rpt=0` + empty `6_drc.lyrdb`** lingered locally. The Jun-30 re-run wrote the real
    25 only to the ORFS-side `reports/` dir; `run_drc.sh`'s copy back into `drc/` (lines ~200-205) was
    skipped (variant/interrupted), leaving the stale local `0`. Then BOTH readers trusted it:
    `run_drc.sh`'s count logic (`cat $DRC_DIR/6_drc_count.rpt`) AND `extract_drc.py`
    (`parse_drc_count`/`parse_lyrdb` off the local `drc/`) — **`extract_drc.py` had NO mtime freshness
    guard**, even though `extract_lvs.py` got exactly one (commit `b710905`) for this same 2026-06-30
    bug. The LVS leg recurred too: these arms had **no `lvs/lvs_result.json` skip marker** (their
    `make lvs` died at the CDL step, `can't read "::env(CDL_FILE)"`), so `extract_lvs`'s skip/netgen
    precedence never fired and it read the stale June-19 `6_lvs.lvsdb` (`text_match_found`) as `clean`
    — its guards covered skip-vs-klayout and netgen-vs-klayout, but NOT *fresh `lvs_run.log` over stale
    klayout artifacts*.
  - **Fix (2026-06-30, branch r2g-debug/asap7-round):**
    1. `extract_drc.py` — new `artifacts_stale()` mtime guard: if a fresh `drc_run.log` post-dates the
       local `6_drc_count.rpt`/`6_drc.lyrdb`, refuse to certify clean → status `stale`
       (`total_violations=None`). Mirrors the `extract_lvs` guard.
    2. `run_drc.sh` — purge stale `drc/6_drc.{lyrdb,log}` + `6_drc_count.rpt` BEFORE `make drc`, so a
       skipped fresh-copy falls through to the honest `no_count_report`→`stuck` path, never a stale-0
       clean.
    3. `extract_lvs.py` — symmetric freshness guard: a would-be `clean` derived from KLayout verdict
       artifacts (`6_lvs.lvsdb`/`6_lvs.log`) OLDER than a fresh `lvs_run.log` becomes `stale` (only
       downgrades clean — a crash/incomplete with no fresh lvsdb stays crash/incomplete).
    - Both new statuses are fail-CLOSED everywhere: `fix_signoff` clean-gate and `ingest_run.is_clean`
      whitelist `{clean,clean_beol,skipped}` only, so `stale` is an unresolved residual, never clean.
    - Reconcile: the fixed extractors (edited mid-campaign, canonical path) drove the campaign's final
      wave to re-emit these arms honestly — the 6 fabricated rows are gone, asap7 reads
      `drc_clean=0 / drc_fail=38 / stuck=9`, and a genuine `traffic_controller drc=fail(25) lvs=skipped`
      row now records the real floor. The arm-row deletion had run FK-off, orphaning **15
      `config_lineage` rows** (`current/previous_run_id`→deleted runs; the FK is `ON DELETE CASCADE`,
      so the intent is removal) — cleaned in one transaction → `foreign_key_check` 0, honesty 5/5.
    - Tests: `test_extract_drc.py::test_stale_artifacts_after_rerun_not_reported_clean` (+ fresh-stays-
      clean converse); `test_extract_lvs.py::test_stale_klayout_artifacts_after_rerun_not_reported_clean`
      (+ converse). Suite 866→870.
  - **Skill-level alarm (unchanged, now closed on the extractor side):** the LVS-`clean`-on-asap7 alarm
    also fires for **DRC-`clean` on any asap7 design** (genuine asap7 clean count = 0; see "ASAP7
    residual-DRC-by-design"). Both are now caught at the extractor boundary before ingest. The proposed
    sixth honesty gate (no-LVS-deck platform ⇒ `lvs∈{skipped,NULL}`) remains the belt to the extractor's
    suspenders and is still worth adding.
- **RECURRENCE 2026-07-02 (sky130hd round — the INVERSE direction: a stale asap7 skip masked a FRESH
  netgen verdict).** The 2026-06-30 fix #3 gave the `extract_lvs` skip gate mtime-precedence but made it
  **platform-blind AND netgen-omitting**: `_skip_klayout_mtime` compared the skip marker only against
  KLayout artifacts (`6_lvs.lvsdb`/`6_lvs.log`/`lvs_run.log`), NEVER against `netgen_lvs_result.json`. On
  the sky130hd round that inverted the bug: **33 designs** had run sky130hd (netgen wrote
  `netgen_lvs_result.json`), were briefly re-pointed to asap7 (a fresh `lvs/lvs_result.json=skipped` marker
  written), then re-pointed BACK to sky130hd where netgen RE-RAN. The asap7 skip (newer than the old
  KLayout log) won the freshness test — but this round's FRESHER netgen verdict was invisible to it — so
  extract honored the asap7 skip and recorded `lvs=skipped` over the real netgen result: **31 genuine
  `clean` (under-counted LVS-clean wins) + 2 real `top_pin_mismatch` (a HIDDEN LVS failure)**. As always
  `honesty.py` stayed 5/5 (it checks `fail`↔`event` parity, not whether a `clean`/`skip` verdict is real);
  the tell was **`clean|skipped` sky130hd rows whose `lvs_run.log` shows netgen ran** ("CONGRATULATIONS!
  Netlists match" — or a fresh `netgen_lvs_result.json` post-dating the skip).
- **Fix (2026-07-02, branch r2g-debug/sky130-round):** `extract_lvs.py` — add `netgen_lvs_result.json` to
  the skip freshness comparison (renamed `_skip_klayout_mtime`→`_skip_supersede_mtime`). The skip is
  authoritative ONLY when it is the most-recent LVS action of ANY tool, so a fresh netgen verdict now
  supersedes a stale skip → extract falls through to the netgen path and records the TRUE state. Genuine
  asap7 has no `netgen_lvs_result.json` (mtime 0) → skip still honored, byte-identical. Tests:
  `test_extract_lvs.py::test_stale_skip_marker_loses_to_fresh_netgen_result` (fails before, passes after)
  `+ test_fresh_skip_marker_beats_stale_netgen_result` (converse guard against over-correction). Suite
  898 pass. Reconciled the 33 latest rows via `reconcile_sky130_campaign.py` (regenerates ppa→drc→lvs→
  re-ingest; orfs stays `pass`, old `skipped` row preserved as history): 31→`clean`, 2→`mismatch`
  (`top_pin_mismatch` now surfaced for the loop's feedthrough fix); honesty 5/5, integrity WARN (0 alarm).
- **Skill-level alarm:** an sky130hd/sky130hs (netgen-LVS platform) `lvs=skipped` row is itself suspect —
  those platforms HAVE a Netgen deck, so LVS should not skip. If `netgen_lvs_result.json` exists and
  post-dates the skip marker, the skip is stale prior-platform contamination. (The proposed sixth honesty
  gate's mirror image — *a run on a platform WITH an LVS deck should not silently record `skipped` when a
  fresh netgen result exists* — would auto-catch this at ingest.)

### Fabricated clean via cleared route abort — signoff path bypassed entirely (2026-07-02)

- **Symptom:** a design goes ledger-`clean` with **NO `reports/drc.json` / `lvs.json` on disk at
  all** (not stale ones — absent). Its latest `runs` row reads `orfs_status='pass'` with **empty**
  `drc_status`/`lvs_status` (outcome_score 0.5), so knowledge and the ledger tell different
  stories. Found on the sky130hd round: `DSP_ACCELERATOR_CHIPLET_ifft_core` + `bgm` (2/131
  ledger-clean designs), both route-TIMEOUT designs whose `route_relief` fix genuinely cleared
  the abort (`CORE_UTILIZATION=8` → route completed → fresh GDS).
- **Root cause:** `engineer_loop.process_one`'s route-abort branch (`_fail_stage=='route'` →
  `_run_fix(check='route')`) called `_mark_clean` directly when the route fixer returned 0.
  "Route abort cleared" = *the flow completes* — a strictly WEAKER contract than the platform's
  clean state (sky130hd: Magic DRC + Netgen LVS on that fresh GDS). The branch bypassed the
  first-pass signoff gate at the bottom of `process_one` entirely, so the `_run_flow` stale-report
  deletion (bug above) never got its fresh-signoff follow-through: reports stayed absent and the
  design was declared clean on GDS-existence alone. The contract dated from 2026-06-17 nangate45
  route_relief work, where the test suite itself asserted `route fix rc==0 → clean`
  (`test_loop_route_inloop_fix.py` encoded the bug).
- **Why the alarms stayed green:** `honesty.py` checks `fail`↔`failure_event` parity — the row is
  `pass`, so no gate fires; `check_db_integrity` audits knowledge↔journal, and BOTH books honestly
  recorded what ran (a flow, no signoff). The lie lived only in the **ledger** `clean` state — no
  current gate cross-checks ledger-clean against the platform signoff contract. (Same blind-spot
  family as the asap7 fabricated cleans: "clean" is never re-derived, only asserted.)
- **Fix (2026-07-02, branch r2g-debug/sky130-round):** on `fix_rc==0` the route branch now sets a
  flag and **falls THROUGH to the signoff path** (`led.set_state('signoff')` → `_signoff_status`
  → first-pass gate / `_run_fix` → `fix_signoff._ensure_baseline` runs fresh platform-correct
  DRC+LVS). `clean` only ever comes from fresh signoff verdicts; a route-cleared design whose
  signoff then finds a residual escalates `catalog_exhausted` with the honest post-fix residual.
  Tests: `test_loop_route_inloop_fix.py::test_route_abort_fixed_in_loop` (route fixer then signoff
  fixer must BOTH run) + `::test_route_abort_clear_still_requires_signoff` (route clears, signoff
  residual → escalated, never clean). Both mislabeled designs reconciled by running real
  `fix_signoff --check both` + `extract_ppa` + re-ingest (latest-row-only; the 16:42Z empty-status
  rows stay as immutable history).
- **Skill-level alarm:** any ledger-`clean` design whose latest `runs` row does not carry
  `drc_status`/`lvs_status ∈ {clean, clean_beol, skipped}` is a fabricated clean — but do **not**
  hand-roll that cross-check (see next sub-section); run `tools/check_ledger_signoff_backed.py`.
  Also: `reports/fix_log.jsonl` showing a vacuous `before=0 after=0 verdict=cleared` route entry as
  the ONLY signoff evidence for a clean design.

### Sub-variant: the bug-#7 DETECTOR itself lied — ledger-signoff gate mis-join (2026-07-07)

- **Symptom:** the Step-4 "every ledger-clean is signoff-backed" cross-check screamed **197/593
  `FABRICATED-CLEAN`** on the sky130hd round while `honesty.py`/`check_db_integrity` stayed green
  (5/5). Nearly all 197 were **false positives** — e.g. `DMA_Controller_DMA_fsm` reported fabricated
  though its sky130hd run is `clean/clean`.
- **Root cause — the DETECTOR, not the loop.** The gate lived only as an inline heredoc in the
  `/r2g-debug` command and had rotted untested. It joined
  `SELECT drc_status,lvs_status FROM runs WHERE project_path LIKE '%'||<basename> ORDER BY
  ingested_at DESC LIMIT 1` with **no `platform=` scope**. Three flaws stacked, all newly exposed by
  the **2026-07-07 store union** (commit ad81aec) that made the ledger (campaign) and knowledge
  (unioned main) two different stores:
  1. design names are FULL of `_`, and in SQL `LIKE` an underscore is a **single-char wildcard** →
     `%foo_bar` also matches `fooXbar`, so "latest" could be a DIFFERENT design;
  2. no platform scope + `ORDER BY ingested_at DESC` → after the union a newer **OTHER-PLATFORM** row
     wins (DMA_Controller grabbed its nangate45 `(None,None)` row instead of its sky130hd `clean`);
  3. latest-ingested across a unioned store crosses rounds. Pre-union (ledger and knowledge one
     store) none of this bit — the union is what surfaced it.
- **It also MASKED the real gap.** The same fragility counted a stale prior-round
  `<design>__sky130hd` **June** run (dir long wiped, still in knowledge) as "backed", hiding **~549
  sky130 cleans** that signed off on disk (`reports/{drc,lvs}.json` clean, 07-02) but were **never
  (re)ingested** into main — `ppa.json` purged so ingest's `run_id` key (`project_path:ppa.json-mtime`)
  is gone, compounded by the db-union carrying only +136 runs. The detector was simultaneously
  crying wolf AND blind to the true issue.
- **Fix (2026-07-07):** extracted the gate into a **tested** tool
  `tools/check_ledger_signoff_backed.py` — joins on the **EXACT `project_path` + `platform`** (never
  `LIKE`, no `__platform` fallback since that is a prior round's physical run), and **on-disk tool
  truth WINS** the honesty call. Three buckets: `backed` (fresh knowledge signoff); `not_ingested`
  (WARN — on-disk `reports/{drc,lvs}.json` clean but knowledge stale/absent → run
  `reconcile_sky130_campaign.py --apply`; tagged `stale_knowledge` when an intermediate non-clean run
  was ingested then superseded on disk without a re-ingest, e.g. APB_Based_GPIO/Canakari); and
  `fabricated` (ALARM, exit≠0 — NO clean evidence anywhere). Guarded by
  `r2g-skills/signoff-loop/tests/test_check_ledger_signoff_backed.py` (9 tests pin underscore-not-a-wildcard,
  platform scope, stale-knowledge-vs-on-disk, and no-evidence fabrication). `/r2g-debug` Step 3+4 now
  call the tool. **Honest verdict on this store: backed=42, not_ingested=551, fabricated=0** — the
  entire 197 was detector error; the real work is draining the un-ingested-cleans completeness gap.
- **Skill-level:** NEVER hand-roll the ledger↔knowledge join with `LIKE`/latest-ingested. A large
  `not_ingested` count is a completeness gap to DRAIN (reconcile + re-ingest), **not** a lie — only
  `fabricated` (non-zero exit) means the loop is lying.

### Re-running signoff after ORFS scratch dirs were cleaned

- **Symptom:** `run_drc.sh` reports `ERROR: ORFS config not found at .../config.mk` or "Running DRC for design: top" with a GDS path that points to a *different* project (e.g., `iccad2015_unit02_in2`'s GDS gets picked up for `button_controller` because both have `DESIGN_NAME=top`). Make may also start re-running place/cts/route, taking 30+ minutes for a "DRC" invocation.
- **Root cause:** Two distinct issues that surface together when a project's ORFS scratch dirs (`flow/designs/<plat>/<DESIGN_NAME>/<variant>/`, `flow/results/...`) have been cleaned but the project's `backend/RUN_*/` still holds the preserved artifacts:
  1. The DRC/LVS scripts used to fall back to `flow/results/<plat>/<DESIGN_NAME>/` (no variant) and `find … -name 6_final.gds`, which silently picked up *another design's* GDS that shared the `DESIGN_NAME`. In our corpus, 59 projects use `DESIGN_NAME=top` and 28 use `DESIGN_NAME=test` — collision was guaranteed.
  2. ORFS Makefile has dependency edges: `6_drc.lyrdb` → `6_final.gds` → `6_final.def` → `5_route.odb` → `4_cts.odb` → … If only `6_final.*` are present but the upstream `*.odb` intermediates are missing, make's timestamp check rebuilds everything from `5_1_grt` backward.
- **Fix (since 2026-05-26):** `r2g-skills/signoff-loop/scripts/flow/_restage_for_signoff.sh` is now sourced by both `run_drc.sh` and `run_lvs.sh`. It:
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

<!-- r2g-lesson:
id: lesson-klayout-drc-stuck-nangate45
status: active
trigger: {check: drc, platform: nangate45}
strategy_ids: [beol_only_drc]
-->

- **Symptom:** `run_drc.sh` runs for hours with no progress. `6_drc.log` last line is `"or" in: FreePDK45.lydrc:121` (or another boolean-op line — also observed at lines 91, 131) and the file mtime stops advancing. CPU stays at 100% on a single klayout process; RSS plateaus around 500MB-3.5GB.
- **Root cause:** KLayout DRC's combination of `poly.not(active).separation(active, ...)` followed by an `or` builds large intermediate polygon sets. On dense designs (>~1.5K cells / >~1MB GDS) the rule scales poorly. Validated on this environment: `iscas89_s27` (86 cells), `ansiportlist` (231), `binops` (231), `CRC33_D264` (1434) all complete; `faraday_dma` (14k cells, 14.8MB GDS) hung indefinitely on rule 121. Also observed on `APB_GPIO_register`, `AXI_Lite_DMA_axilite`, `DMA_Controller_DMA_registers` stuck on rule 131.
- **Action:**
  - Don't extend `DRC_TIMEOUT` blindly — observed zombies ran 3-4 days at 100% CPU without finishing on the same rule.
  - Document in `<project>/drc/drc_result.json` with `"status": "stuck"` and the `stuck_at_rule` so the dashboard can show a yellow badge instead of red.
  - Use `setsid timeout` (already enforced in `run_drc.sh`) so terminating the parent kills the klayout child cleanly.
  - For the design itself, the rest of the flow (ORFS → RCX) is independent and can still produce GDS+SPEF.
- **Pre-existing zombies:** If the system has klayout DRC processes running >1 hour at 100% CPU on the same `lydrc` line and no log progress, they're stuck in this pattern. Kill with `kill -9 <pid>`. Six such zombies were observed in this session, accumulating ~20k+ minutes of wasted CPU before cleanup.

#### BEOL-only fallback

When the FEOL hang is confirmed, run with `DRC_BEOL_ONLY=1 bash scripts/flow/run_drc.sh <proj> <platform>`. The script generates a modified deck copy (`drc/*.beol.lydrc`) with **both** `FEOL = false` **and** `ANTENNA = false`, and passes it to `make drc` via `KLAYOUT_DRC_FILE=`. **FEOL and ANTENNA checks skipped (ANTENNA depends on FEOL-derived layers); metal/via routing geometry + off-grid checks run.** The standard cell library is pre-characterized and DRC-clean, so skipping the front-end-of-line boolean ops (poly, diffusion, gate geometry) is safe. ANTENNA must also be disabled because its `connect` rules reference the `gate` layer (`gate = poly & active`), which is derived *inside* the deck's `if FEOL … end` block — leaving ANTENNA on with FEOL off makes KLayout error (`'connect': First argument must be a layer …`) and `make` exit 1, which the runner would then mis-classify as `stuck`. `OFFGRID` stays on (no FEOL dependency, completes fine). Results are tagged `"drc_mode": "beol_only"` in `drc_result.json` and `reports/drc.json`, and a 0-violation BEOL-only run is given the **qualified status `clean_beol`** (not plain `clean`) by `extract_drc.py` so status-based aggregation can never silently miscount it as a full clean (mirrors LVS `clean_algorithmic`; `diagnose_signoff_fix.py` treats `clean_beol` as needing no fix). Do **not** report a BEOL-only run as full DRC-clean, and **antenna is NOT verified** in this mode. (BEOL-only is a fallback for the FEOL *polygon-op hang* on huge designs — it is NOT an antenna workaround. nangate45 antennas ARE now OpenROAD-fixable once the antenna model is installed — see the Antenna DRC Violations section — so prefer a full DRC + `antenna_diode_repair` whenever the design is small enough to complete full DRC.)

**Deeper fallback for large designs (`DRC_BEOL_STRICT=1`, implies BEOL-only; `DRC_SKIP_CONTACT` is a back-compat alias).** Surprising empirical finding: the `FEOL = false` toggle gates the Well/Poly/Active booleans (the `:91/:121/:131` hangs) but does **NOT** gate the **IMPLANT** and **CONTACT** groups — those still execute in BEOL-only mode and **hang on large designs** (≥~465K inst: eth_mac_1g_fifo, koios_gemm_layer froze 5–8 min at 100% CPU, RSS 7.3GB, at `implant.width`/`cont.space` over millions of MOL polygons). Designs ≤~144K run those groups fine; only the largest hang. All FEOL-block geometry (well/poly/active/implant/contact) is library-internal — P&R adds only metal and vias, never intra-cell MOL shapes — so stripping the whole block body is as defensible as the FEOL toggle. `DRC_BEOL_STRICT=1` uses awk to comment **every `.output(` check between `if FEOL` and `end # FEOL`** in the generated deck (aborts if any remains uncommented), leaving the layer-derivation lines intact and only BEOL metal/via + OFFGRID checks running — the actual P&R-created geometry. Tagged `"drc_mode": "beol_only_strict"`; a 0-violation result is still `clean_beol` (the `drc_mode` records the precise scope). Use this only when plain `DRC_BEOL_ONLY=1` hangs at an IMPLANT/CONTACT op. **Empirical ceiling (verified):** on `eth_mac_1g_fifo` (469K) BEOL-strict cleared the entire FEOL block (logged `BEOL checks`) but then **hung on the first BEOL `metal1.width` (METAL1.1) op** — the legitimate P&R metal-geometry check, which *cannot* be skipped without abandoning DRC entirely. So designs whose **METAL** ops don't converge (≥~465K inst here: eth_mac_1g/mii_fifo, axis_ram_switch, koios_gemm_layer, and the multi-million-inst BOOMs) are **genuinely intractable for this KLayout build** and stay honest `stuck` — no flow lever helps. `DRC_BEOL_STRICT` only rescues a design whose hang is in the FEOL-block MOL groups *while* its METAL ops are tractable; no design in the current corpus has been shown to fall in that narrow band (everything ≤~406K already completes with plain `DRC_BEOL_ONLY`), so strict mode is presently a defensive fallback rather than a demonstrated unblock.

#### Sub-variant: externally-killed stuck (exit 2, not 137)

When klayout DRC is stuck on a polygon op and gets SIGKILL'd by something **other** than `run_drc.sh`'s own timeout — cgroups OOM, session limit, monitor script, or manual `pkill` — `make` exits 2 (target failed), not 124/137. Older `run_drc.sh` versions classified this as a generic `failed`, hiding the stuck pattern from triage.

- **Detection (since 2026-05-23):** `run_drc.sh` greps `drc_run.log` for `Killed $KLAYOUT_CMD`, `Killed klayout`, or `Error 137`. When that keyword is present AND a `*.lydrc:NN` reference exists in `6_drc.log`, classify as `status=stuck` with `killed_externally=true` in `drc_result.json` (regardless of make's wrapper exit code).
- **Symptom example:** `drc_run.log` ends with `klayout.sh: line 9: <PID> Killed   $KLAYOUT_CMD "$@"` followed by `make: *** [...] Error 137`, while the run-script's PIPESTATUS captures `2`. Total elapsed is well under the timeout (3-8 minutes) but CPU utilization is low (~13%) indicating klayout was waiting (not crashing) when killed.
- **Action:** Same as the timeout variant — treat as stuck, do not retry. The signoff outcome for the design is effectively "DRC unavailable, GDS+LVS+RCX still valid". The 3 v2 cases (APB_GPIO_register, AXI_Lite_DMA_axilite, DMA_Controller_DMA_registers) that previously logged as `drc=fail(2)` are now correctly tagged `stuck` after this fix.

#### Sub-variant: stuck/incomplete mislabeled `clean` by the fix-loop exit gate (2026-06-20, **honesty bug**)

`diagnose_signoff_fix.py:260` correctly scopes `status in ("stuck","timeout")` as
`drc_{status}_tooling_out_of_v1_scope` — a *residual* with no automated fix, so `fix_one`
gets a `STOP` and returns 0 (meaning "fix loop finished", **not** "clean"). The authority on
clean-vs-residual is the python **exit gate** at the tail of `fix_signoff.sh`. That gate was
**fail-OPEN**: it flagged a residual only if `status ∈ {fail,failed,residual,timeout}`, so every
*other* status — DRC `stuck` (FEOL polygon-op hang) and LVS `incomplete`/`crash`/`unknown`
(`extract_lvs.py:351` = reached device extraction but died with no match verdict, no lvsdb) —
fell through as **exit 0**. `engineer_loop._process_one:319` then called `_mark_clean()` on a
design whose signoff never verified, recording it **`clean`** in the campaign ledger *and*
auto-draining its escalations (`_mark_clean` → `escalations.resolve_for_design`).

- **Honesty scope:** the knowledge `runs` row stayed honest (it stores the real
  `drc_status='stuck'` / `lvs_status='incomplete'`), so the *learner* never saw a lie — but the
  **loop-control / ledger layer** over-reported clean. Surfaced live 2026-06-20: `cf_fir_24_16_16`
  burned ~6h (2h full-DRC timeout `stuck` + 4h LVS `incomplete`) then was marked `clean`. A corpus
  scan found **11/101 nangate45 ledger-`clean` designs mislabeled** (8× `stuck`/`clean`, 3×
  `stuck`/`incomplete`).
- **Fix (`fix_signoff.sh` exit gate):** make it **fail-CLOSED**, mirroring `_process_one`'s
  first-pass predicate — a check is signed off ONLY for `status ∈ {clean, clean_beol, skipped}`;
  every other status is an unresolved residual → exit 2 → the loop escalates (`catalog_exhausted`).
  Regression: `tests/test_fix_signoff_clean_gate.py`.
- **Lesson:** a signoff/clean gate must be an **allowlist of clean states**, never a denylist of
  fail states — the status vocabulary (`stuck`, `incomplete`, `crash`, `unknown`) grows over time
  and a denylist silently fails open on every new value. Two gates encoding the same policy
  (`diagnose` residual-scoping vs the exit gate) MUST use the same predicate.

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
- **Sub-classify every `fail`:** `extract_lvs.py::classify_lvs_mismatch` labels the `.lvsdb`
  `symmetric_matcher` (tool limit, layout clean), `real_connectivity` (genuine defect), or `generic`
  (needs review) from net balance + device agreement — see "LVS symmetric-matcher residual" below.
  Across the current corpus the 14 fails resolve to **2 real defects (both wb2axip) + 12 symmetric
  residuals**; treat a `symmetric_matcher` label as a believed-clean residual, not a hard fail.

### LVS Skipped (No Rules)
- **Symptom:** `LVS is not supported on this platform` or `lvs_result.json` shows status "skipped"
- **Fix:** This is expected for platforms without KLayout LVS rule decks. Not a flow error.
- **Platforms without KLayout LVS:** asap7
- **Alternative:** For sky130hd/sky130hs, use `run_netgen_lvs.sh` (Netgen + Magic) as an alternative LVS flow
- **`skipped` is STALE once a platform rule is installed.** nangate45 had no bundled `.lylvs` before
  2026-05-27; designs run earlier are recorded `skipped` and will RUN if re-run now (the rule exists).
  But the knowledge store can also carry `lvs_status=skipped` (or NULL) simply because **LVS was
  re-runnable but never re-run and re-ingested** — the 2026-06-02 re-ingest read no `lvs.json`
  because no LVS had executed. Workflow: **re-run LVS first, THEN `knowledge/ingest_run.py`.** Caveat:
  "has a rule now" ≠ "will pass" — large designs (>300K cells, e.g. the verilog-ethernet udp/eth_mac
  family) re-run to `incomplete` (matcher non-convergent within cap), not `clean`.

### LVS symmetric-matcher residual (KLayout `Netlists don't match`, layout actually correct)

<!-- r2g-lesson:
id: lesson-lvs-symmetric-matcher
status: active
trigger: {check: lvs, class: symmetric_matcher, platform: "*"}
strategy_ids: [lvs_same_nets_seed]
-->

- **Symptom:** `6_lvs.log` ends with `ERROR : Netlists don't match`, but the `.lvsdb` failure is
  **only** instance/net assignment ambiguity, not a real delta. The precise, validated signature
  (`extract_lvs.py::classify_lvs_mismatch`, refined 2026-06-03) is:
  **schematic-only unmatched net count == layout-only unmatched net count (BALANCED), zero
  paired-but-mismatching nets `net(N M mismatch)`, zero device mismatches, and at least one
  same-cell-type instance swap `circuit(<layoutId> <schemId> mismatch)` or `ambiguous group of
  nets` warning.** Optionally also `entry(warning description('Maximum depth exhausted ...'))`.
  The old rule (require *zero* unmatched nets) under-reported: aes_core (8+8), vlsi_axi_slave
  (40+40), iccad2017_unit5_F (64+64), axil_crossbar_wr (420+420) all carry BALANCED unmatched nets
  with every device matched — clean layouts the old rule mislabeled `generic`.
- **Root cause:** KLayout 0.30.7's netlist comparer cannot uniquely fingerprint topologically
  identical instances in **symmetric structures** — parallel NAND/NOR/XOR/parity trees, crypto
  mixing functions (blake2s G-function), **register files / memory arrays (`MEMORY[i][j]`),
  replicated bit-slices**, and flat combinational benchmarks (ICCAD units). It gives up and leaves
  the unmatched nets perfectly balanced. **This is a tool limitation, not a layout error** —
  net/pin/device counts agree on both sides. The discriminator vs a real defect is BALANCE +
  device agreement, *not* "no unmatched nets".
- **What does NOT fix it (validated 2026-06-02, signoff-fixer campaign):** raising the comparer
  search budget. The deck exposes `max_branch_complexity`/`max_depth` (env
  `LVS_MAX_BRANCH_COMPLEXITY`/`LVS_MAX_DEPTH`). Tested:
  - `verilog_ethernet_axis_baser_rx_64` at `max_depth=32` → identical 2 NAND2 swaps; the matcher
    was **not** budget-limited (no "depth exhausted"), it simply mis-paired.
  - `iccad2017_unit5_F` at `max_depth=64, max_branch_complexity=1048576` → the "Maximum depth
    exhausted" *warning* vanished but **all 292 net mismatches persisted** (run took only 168s).
  So budget removes the *symptom warning* without touching the *real* mismatch. Do not burn
  re-runs cranking it.
- **Honest handling:** classify as residual `lvs_symmetric_matcher_residual` (see
  `signoff-fixing.md` residual taxonomy). Do **not** relax the rule deck, and do **not** promote it
  to plain `clean`. Distinguish from a **real connectivity error** (residual
  `lvs_real_connectivity_mismatch`): an `entry(error description('Net <PORT> is not matching any
  net ...'))`, **imbalanced** unmatched nets (more layout than schematic, or vice-versa), or a
  paired `net(N M mismatch)` is a genuine layout defect. In the current corpus exactly **two**
  designs are real defects — wb2axip_axi2axilite (1 net open: `S_AXI_WREADY` driver split from its
  output buffer) and wb2axip_axilsingle (16 bus opens: 104 vs 120 unmatched on `S_AXI_RDATA`/
  `M_AXI_AWVALID` bits) — everything else with this signature is the tool limit.
- **`same_nets!` seeding CAN clear it (validated 2026-06-03, operator-only).** Per-design strict
  `same_nets!` seeding on the swapped instances' **input-pin** nets produces a true `match`:
  validated on `verilog_ethernet_axis_baser_rx_64` (2 NAND2 swaps → "CONGRATULATIONS! Netlists
  match", 4 seeds). Key facts: use `same_nets!` (the strict/forcing form) — soft `same_nets` is a
  no-op the matcher overrides; seed **input nets only** (seeding the swapped gate's own output net
  over-constrains and re-fails); layout internal nets are mostly anonymous (~4% named) so address
  them as net objects via `expanded_name`, not `net_by_name`. It is **opportunistic and does NOT
  generalize** — on iccad2017_unit5_G every seed strategy left it equal or worse (deep global
  symmetry). Ship/run it only with a hard gate: accept the seeded verdict ONLY if the re-run is
  genuinely clean. Tooling: `assets/platforms/nangate45/lvs/FreePDK45_symseed.lvs` +
  `signoff-fixing.md` "Symmetric-matcher seeding (operator-only, validated)". The deeper genuine
  fix remains a newer KLayout with an improved symmetric matcher.
- **`clean_algorithmic` is a STALE label — re-validate it.** No current script emits
  `clean_algorithmic`; the 7 such reports are frozen artifacts of an earlier campaign. Re-extracting
  with the refined `classify_lvs_mismatch` flips them to `fail` + a precise `mismatch_class`. Five
  were genuine symmetric residuals (layout clean) but **wb2axip_axilsingle was hiding a real
  `real_connectivity` defect under the benign label**. Always re-run `extract_lvs.py` on any
  `clean_algorithmic` design before trusting it.
- **Cross-platform stale-status caveat:** a design can also show a bogus LVS `fail`/`failed` when
  its `6_lvs.log` is a **concatenation of an older different-platform run** prepended to the current
  one (the extractor then keys off the old failure marker). Re-running LVS fresh on the current
  platform resolves it (e.g. `cordic`: stale sky130hd failure → re-ran nangate45 → `clean`). Always
  re-run before trusting an LVS `fail` on a design that changed platform.

#### Sub-variant: the autonomous loop used the WRONG LVS tool on sky130 (2026-06-17, systematic)

- **Symptom:** Across the 94-design sky130 campaign, a large fraction of designs escalated with
  `{"drc":"clean","lvs":"fail"}` — `6_lvs.log` ends `ERROR : Netlists don't match`, yet the layout
  is correct. Validated examples: `wb2axip_wbsafety`, `vtr…blob_merge` — both **KLayout-fail →
  Netgen-clean** ("Circuits match uniquely", 0 device/net deltas).
- **Root cause (a dispatch bug, not a layout/tool-limit issue):** the autonomous loop ran **KLayout
  LVS** (`run_lvs.sh`, deck `sky130hd_r2g.lylvs`) on **every** platform. On sky130 the production
  LVS path is **Netgen** (Magic GDS extraction + Netgen compare) — KLayout 0.30.7's symmetric
  matcher mis-pairs std-cell-dense sky130 layouts (the residual documented above), so it false-fails
  designs Netgen finds clean. Two plumbing defects compounded it: (1) `fix_signoff.sh` hard-coded
  `RUN_LVS=run_lvs.sh`; (2) `extract_lvs.py` preferred the Netgen verdict **only when KLayout left no
  artifacts**, but the loop's own KLayout run always left `6_lvs.lvsdb`/`6_lvs.log`, so the stale
  false-fail clobbered any later Netgen-clean.
- **Fix (shipped 2026-06-17):**
  1. `fix_signoff.sh` selects the LVS tool by platform — `sky130*` → `run_netgen_lvs.sh`, everything
     else → `run_lvs.sh` (KLayout). An explicit `R2G_RUN_LVS` override still wins.
  2. `extract_lvs.py` now uses a **most-recently-run-tool-wins** rule (mtime of
     `netgen_lvs_result.json` vs the freshest KLayout artifact) instead of "only if KLayout left
     nothing", so a fresh Netgen verdict supersedes lingering stale KLayout artifacts. nangate45
     (KLayout-only) is byte-identical.
- **Recovery for already-run designs:** just run `run_netgen_lvs.sh <proj> sky130hd` then
  `extract_lvs.py <proj> reports/lvs.json` — no re-flow needed (the GDS is already built); then
  re-ingest. This flips the false `lvs:fail` to `lvs:clean`.
- **Distinguish from nangate45:** on nangate45 KLayout is the *only* LVS tool, so the symmetric
  residual there is genuinely unresolved without `same_nets!` seeding (above). Netgen is the sky130
  escape hatch, not a nangate45 one.

### LVS KLayout sort_circuit/gen_log_entry SIGSEGV (non-deterministic, retry-fixable)

- **Symptom:** `make lvs` dies with `ERROR: Signal number: 11` and a backtrace through
  `db::NetlistCrossReference::sort_circuit()` → `gen_log_entry(...)` (sometimes `ruby_run_node`),
  `Crash log written to ~/.klayout/klayout_crash.log`. `extract_lvs.py` reports `status=crash`,
  `reason=klayout_cpp_crash`. It dies in the **netlist COMPARE**, *after* device extraction and
  netlist build succeed (the extracted `.cir` is complete). Faulting address is a corrupted Net
  pointer — a use-after-free/uninitialised read.
- **Root cause:** a **non-deterministic heap heisenbug** in KLayout 0.30.7's comparer cross-reference
  generator. The same prebuilt GDS+CDL crashes ~most runs and survives ~1-in-N (survival rate is
  design-dependent: fifo_basic ~high, usbf_device ~0). A surviving run produces the **true verdict**
  (clean OR fail). A related milder manifestation — `dbLayoutVsSchematicWriter.cc:151 i !=
  net2id.end()` — is the same corruption hitting the lvsdb writer; the deck's begin/rescue swallows
  it and the verdict line is still emitted (so a `clean` can co-exist with that ERROR — the
  extractor handles it).
- **Fix (since 2026-06-03): retry.** `run_lvs.sh` now loops up to `LVS_CRASH_RETRIES` times (default
  4; auto-reduced to 1 for >150K-cell designs since each retry re-extracts), breaking on the first
  run with no `Signal number` in `6_lvs.log`. Validated: fifo_basic, verilog_axi_axi_fifo_wr →
  **clean**; wb2axip_aximwr2wbsp (326+326), core_usb_host_top (22+22), sha256_axi4_slave (51+51) →
  **fail/symmetric_matcher** (balanced — layout clean). So 5/7 "crash" designs were masking real
  verdicts (2 clean, 3 symmetric residuals).
- **What does NOT fix it (all tested, ruled out):** `threads(1)`, `verbose(false)`, `LD_PRELOAD`
  tcmalloc — still crash. `flat` mode (vs `deep`) dodges the crash deterministically but yields
  **garbage mismatches** (~12,840 spurious deltas; the deck's `align`/`equivalent_pins`/`purge` are
  keyed to hierarchical names) — never use it for a verdict. The real source fix is KLayout
  ≥0.30.10, but **no newer build exists on this host** (`find / -name 'klayout*'` → only 0.30.7).
- **Resource bug fixed alongside:** a SIGSEGV gives `make` exit 2 (not 124/137), so the old
  124/137-only cleanup left a multi-GB klayout child still spinning. `run_lvs.sh` now reaps orphans
  on **any** nonzero exit.

#### Sub-variant: writer crash emits a SPURIOUS "don't match" → false `lvs=fail` (2026-06-28)

The claim above ("the deck swallows the `net2id.end()` writer error and the verdict line is still
emitted, so a `clean` can co-exist") is only HALF true. When the writer corruption ALSO makes the
deck emit `ERROR : Netlists don't match`, the **lvsdb itself still says `text_match_found` with 0
mismatches** (the COMPARE matched — only the post-compare lvsdb WRITE crashed), but `extract_lvs`
read `log_status='mismatch'` *before* its crash case → a **false `lvs=fail`** on an actually-matching
design. Found: `PicoRV32_Based_SoC_fifo_basic` (mismatch=0/net=0/dev=0, lvsdb `text_match_found`, log
errors = `dbLayoutVsSchematicWriter.cc:151 i != net2id.end ()` + `RuntimeError: Internal error ... in
Executable::cleanup` + the `device_count` `NoMethodError` dump). **Fix:** `_CRASH_RE` now recognizes
the writer-crash signature (`net2id.end` / `dbLayoutVsSchematicWriter` / `Internal error ...
Executable::cleanup`), and the status decision classifies *lvsdb-matched + 0-mismatch + crash* as
`status=crash` (`reason=lvs_writer_crash_after_match`), never `fail`; `run_lvs.sh` retries on that
signature for a clean survivor. A genuine mismatch (lvsdb mismatch, no crash) still → `fail`. The
deterministic-vs-heisenbug split applies as above: a surviving retry → `clean`; an always-crashing
writer stays an honest `crash` (the compare matched, so the design is LVS-clean — the tool just can't
serialize the db). Tests: `tests/test_extract_lvs.py`.

### LVS "incomplete" is mostly a comparer bug, not honest slowness

- **Symptom:** `status=incomplete`, `reason=lvs_no_verdict_no_lvsdb` — the run reached device
  extraction / netlist build but produced no match/mismatch verdict and wrote no `6_lvs.lvsdb`.
- **Triage by grepping the log — three distinct causes, only one is "just slow":**
  1. **Comparer SIGSEGV** (`Signal number: 11` + `sort_circuit` backtrace): the crash above, e.g.
     `usbf_device` (23K cells, crashes at ~750s, peak <1GB — *smaller* than aes_core which finishes).
     A bigger `LVS_TIMEOUT` does **not** help; retry / newer KLayout does.
  2. **Comparer internal assertion** (`Internal error: dbNetlistCompareCore.cc:1003 bt_count !=
     failed_match`, e.g. `sdspi_wb_controller`): a hard KLayout-0.30.7 comparer abort. Not
     timeout-fixable; needs a newer KLayout.
  3. **Honest extraction timeout** (log stops at `"netlist" in: …:246` with `Terminated`): KLayout
     layout-netlist **extraction** is super-linear (~2700s @51K, ~10200s @62K cells), so the old
     3600s default SIGTERM'd every ≥50K design *mid-extraction*. `run_lvs.sh` auto-scale now clears
     the extraction wall (>50K→14400s, >100K→21600s, >250K→28800s, base 5400s).
- **`Killed`/`Error 137` at low wall-time and <2GB peak = external SIGKILL** (shared-host
  memory/scheduler pressure, not OOM — peak RSS stayed ≤1.65GB even at 242K cells). **Run LVS
  serially** on a shared host; concurrent peer jobs externally kill long extractions (this also
  explains the `biriscv_core` no-lvsdb: killed mid-`netlist`, not a writer crash).
- **`deep`/`flat`/`threads` tuning does not help the comparer pathology** (verified: flat mode on
  usbf still spins >470s with no verdict). Memory is never the binding constraint.
- **no-lvsdb-but-verdict trap:** a `fail` log with no `VERBOSE-LVS:` markers predates the 2026-05-28
  writer patch (the old deck only wrote the lvsdb on a *clean* match). Re-run with the current deck
  to get a classifiable lvsdb (e.g. `iccad2015_unit08_in1`).
- **ChipTop scale (5–9M cells, the BOOMs) is intractable here:** LVS is `Terminated` mid-geometry
  (e.g. `FreePDK45.lylvs:117` at 14–17GB) long before netlist compare. Honest residual:
  "KLayout-LVS-intractable at ChipTop scale on this host" — do not launch.

### LVS CDL parse error (escaped-bracket / negative-index instance names)

- **Symptom:** `ERROR: ...Pin count mismatch (N expected, got N+1) ... in Netlist::read` — LVS
  aborts before any compare; `extract_lvs.py` reports `status=unknown`, `reason=cdl_parse_error`.
- **Root cause:** KLayout 0.30.7's SPICE reader mis-tokenizes an instance name containing an escaped
  bracket / negative bit-index plus `$`, e.g. `Xr_CS_Inactive_Count\[-1\]$_DFFE_PN0P_` (from a
  `[-1]` bit-blast in synthesis). It is a deterministic **CDL-generation/parser** issue, **not** a
  layout mismatch — the layout is never assessed. Reproducer: `spi_master_single_cs`.
- **Fix:** sanitise the offending instance name in the CDL, or avoid the `[-1]` bit-blast in RTL/
  synthesis. Out of automated scope for now; surfaced honestly so it is not confused with a defect.

### Magic DRC Failure

**`run_magic_drc.sh` is an OPTIONAL alternative, NOT the loop's signoff DRC.** The autonomous
loop signs off sky130 DRC with the ORFS **KLayout** deck (`platforms/sky130hd/drc/sky130hd.lydrc`,
via `run_drc.sh`); `extract_drc.py` reads ONLY the KLayout artifacts (`6_drc_count.rpt`/
`6_drc.lyrdb`). `run_magic_drc.sh` writes SEPARATE `magic_drc*` files that no extractor consumes,
so its verdict never reaches the DB. (Older notes calling "sky130hd Magic DRC" the honored signoff
deck were ASPIRATIONAL — the naive Magic path over-reports std-cell geometry, see below.)

- **Symptom:** Magic DRC script fails or produces no output / invalid JSON.
- **Common causes:**
  - sky130A tech file missing at `$PDK_ROOT/sky130A/libs.tech/magic/sky130A.tech`
    (set `PDK_ROOT` in `references/env.local.sh`; `/opt/pdks` is only the fallback default)
  - Platform not supported (Magic DRC only works for sky130hd/sky130hs)
  - GDS file corrupted or from incomplete backend run
- **Tool:** `scripts/flow/run_magic_drc.sh`

**Bug fixed 2026-07-02: Tcl crash + invalid JSON (found by the /r2g-debug sky130 tech cross-check).**
The generated `run_magic_drc.tcl` iterated `foreach {rule count} [drc listall why] { … expr {$total
+ $count} }`, but `drc listall why` returns `{rule {box box …} …}` — the 2nd item is a LIST OF BOXES,
not a number — so `expr` aborted with `can't use non-numeric string as operand of "+"`, and
`set drc_count [drc count total]` (Magic *prints* the total but does not RETURN it) left the count
empty, leaking the literal `magic_drc_total_violations:` into `magic_drc_result.json` → **invalid
JSON**. Fix: count `[llength $boxes]` per rule (never add the box list as an int); parse the
authoritative "Total DRC errors found: N" line Magic prints; guard the shell parse to numeric
(fail-closed to 0). Validated on `CAN_Bus_Controller_can_tx`: no crash, valid JSON,
`total_violations=4777`.

**mcon/li over-reporting caveat — why naive Magic ≠ a signoff gate.** On a flattened ORFS P&R GDS,
`gds read` + `drc catchup` makes Magic re-check foundry std-cell INTERNAL geometry and cell
ABUTMENT, so it reports thousands of base-layer violations. On `can_tx` (KLayout `sky130hd.lydrc`
**CLEAN / 0**) the fixed Magic run reports `li.3` (local-interconnect spacing) = 8238, `li.1`
(li width) = 355, `mcon.2` (mcon spacing) = 86. These `li`/`mcon` rules are dominated by
std-cell-internal / abutment artifacts (sky130 foundry cells are DRC-clean by construction; a naive
flatten re-checks them out of context) — a design with 8238 real li spacing errors could not route.
A trustworthy Magic signoff needs **OpenLane-style cell abstraction** (`drc style`, gds flatten
excludes). Until that exists, **do NOT wire naive Magic DRC as the sky130 signoff gate** — it would
false-fail every design. The ORFS KLayout `sky130hd.lydrc` deck remains the practical sky130 signoff
the loop uses; whether any *top-level* (non-cell) geometry has real violations is an open question
requiring the proper Magic setup — escalate, do not auto-chase (cf. the asap7 "residual-by-design"
lesson: run the deck on ORFS's own reference before believing a floor is real).

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

### sky130 LVS: ORFS KLayout rule unworkable; needs Netgen+Magic+sky130A PDK

**Symptoms (sky130hd/sky130hs, KLayout `make lvs`):** a chain of failures in the
ORFS-bundled `platforms/sky130hd/lvs/sky130hd.lylvs`:
1. `... 6 expected, got 7` — KLayout's CDL reader counts the ` / ` node/model separator
   in the platform CDL's `*_macro_sparecell` instance lines as an extra pin.
2. `Can't find a value for a R, C or L device ... rI12 VGND LO short` — the tie/power
   cells (`conb_1`, etc.) use 6 non-numeric `short` (zero-ohm) resistors the default
   SPICE reader rejects, aborting `Netlist::read` (`sky130hd.lylvs:19`).
3. After (1)+(2) are fixed, the run completes but reports `ERROR : Netlists don't match` —
   the stock rule extracts **MOS only** and flattens the hierarchical schematic against a
   flat-transistor layout extraction, leaving a residual net/device mismatch even with a
   reader delegate that shorts the R=0 nets.

**Root cause:** the ORFS-bundled sky130 KLayout LVS rule is not production-grade; the
canonical sky130 LVS path is **Magic (SPICE extract) + Netgen (compare) + sky130A PDK**.
**Important:** a DB `lvs_status=clean` on a sky130 design may be a STALE/cross-contaminated
nangate45 artifact — verify the actual `lvs_sky130hd_*` console logs, not `reports/lvs.json`
alone (cordic's "clean" was an old nangate45 `6_lvs.log`; its real sky130hd LVS failed).

**Fixes shipped (parse-level, gated to sky130 in `run_lvs.sh`):** a slash-normalized +
`short`→`0` CDL (`s| / | |g; s/... short$/... 0/`) so KLayout *runs to completion and
yields a classifiable verdict instead of an unparseable crash*, plus a corrected rule
`assets/platforms/sky130hd/lvs/sky130hd_r2g.lylvs` adding a SPICE-reader delegate that
shorts the R=0 tie/power resistors. These make LVS *runnable*, not *clean*.

**Genuine clean LVS requires Magic + Netgen + the sky130A PDK. As of 2026-06-10 these are
INSTALLED** (user-level Miniconda `eda` env from the litex-hub channel; `open_pdks.sky130a`
PDK staged at `/proj/workarea/user5/sky130_pdk/share/pdk/sky130A` — on `/proj`, not `/home`,
which is full). They are wired into the skill via `references/env.local.sh`
(`MAGIC_EXE`/`NETGEN_EXE`/`PDK_ROOT`); confirm with `scripts/flow/check_env.sh`. The driver
(`tools/run_sky130_design.sh`) auto-routes sky130 LVS through `run_netgen_lvs.sh` (detects
`PDK_ROOT/sky130A` + magic + netgen), so KLayout LVS is no longer the sky130 path. If
`PDK_ROOT` is ever unset/missing the prior `env_blocked` honesty rule still applies — do NOT
report sky130 LVS as clean without a real Netgen verdict.

**First real-run findings (2026-06-11, sky130 campaign smoke + validation).** The
first end-to-end Netgen runs surfaced three defects that had made the path inert:

1. **Driver never sourced the skill env → KLayout fallback.** `tools/run_sky130_design.sh`
   gates LVS-tool selection on `$PDK_ROOT`/`$MAGIC_EXE`/`$NETGEN_EXE`, but those live in
   `references/env.local.sh` (sourced *inside* each flow script's `_env.sh`), not in the
   driver's own shell. So the gate saw them unset, logged `LVS via KLayout (Netgen/PDK
   absent)`, and produced a bogus `lvs_fail` on every design. **Fix:** the driver now
   `source`s `scripts/flow/_env.sh` near the top (it restores caller shell flags on exit).

2. **Bare `magic` invocation → exit 127.** `run_netgen_lvs.sh` and `run_magic_drc.sh`
   resolved `MAGIC_EXE` but then called `magic` *by bare name* at the actual call site.
   Magic lives in a non-PATH conda env (`~/miniconda3/envs/eda`), so extraction died with
   `magic: No such file or directory`. **Fix:** both call sites now use `"$MAGIC_EXE"`.

3. **Magic-extraction hygiene fixes (SHIPPED 2026-06-11).** `run_netgen_lvs.sh` now:
   - extracts **hierarchically** (dropped the non-standard `flatten`) so each std cell stays
     a subckt that matches the cell-library definition;
   - runs Magic inside a `lvs/magic_ext/` scratch dir so its ~50 per-cell `*.ext` files no
     longer pollute the caller's CWD (the repo root, via the driver's `cd $REPO`);
   - loads the std-cell SPICE library
     (`sky130A/libs.ref/sky130_fd_sc_hd/spice/sky130_fd_sc_hd.spice`) into the **schematic**
     circuit via a netgen TCL (`readnet spice <lib>` into circuit2 *before* `readnet verilog`
     into the same handle), the OpenLane pattern, so the schematic cells are no longer hollow.

4. **Real remaining blocker: ORFS `6_final.v` is NOT power-aware → implicit-power-pin net
   explosion (OPEN).** Even with (3), LVS still mismatches with an IDENTICAL signature:
   `Circuit 1 contains 623 devices, Circuit 2 contains 623 devices` (devices match — layout
   is sound) but `729 nets (layout) vs 3219 nets (netlist)`. Root cause: ORFS writes
   `6_final.v` with **zero** `VPWR/VGND/VPB/VNB` connections (`grep -c VPWR 6_final.v` → 0).
   Netgen therefore logs `Note: Implicit pin VGND/VNB/VPB/VPWR in instance _NNN_ of <cell>`
   for every cell and creates per-instance power nets that never merge into the four global
   supplies — inflating circuit2 to ~623×4 extra nets (3219) vs the layout's merged 729.
   This is the classic sky130 "non-powered netlist" Netgen problem, **independent of the
   cell-SPICE fix**. Candidate fixes (not yet validated): (a) have ORFS emit a power-aware
   LVS netlist (`WRITE_VERILOG`/`def2v` with power pins), or (b) add netgen global-net
   handling so VPWR/VGND/VPB/VNB merge across implicit pins, or (c) flatten both sides to
   transistors before compare. Until one lands, sky130 Netgen LVS reports an honest,
   well-characterized `lvs_mismatch` residual (devices-match, power-net-modeling-differs) —
   NOT clean, and NOT a layout defect.

#### sky130 Netgen LVS: top-level pin-matching residuals (antenna diodes + port feedthroughs)

<!-- r2g-lesson:
id: lesson-sky130-netgen-top-pin-mismatch
status: active
trigger: {check: lvs, class: top_pin_mismatch, platform: "*"}
strategy_ids: [netgen_diode_normalize, buffer_port_feedthroughs]
-->

5. **Two residual LVS-mismatch causes found in the first 50-design sky130 wave (2026-06-11,
   FIXED 2026-06-11).** All 13 wave mismatches shared the SAME netgen verdict — `Top level
   cell failed pin matching` — with every subcircuit and net count matching (i.e. NOT a
   topology/connectivity defect). Final classification after root-causing both: 8 antenna-diode
   + 5 port-feedthrough (the initial "power-port reconciliation" guess for the diode-free
   subset was wrong — VDD/VSS matched fine; the unmatched pins were signal ports).
   `run_netgen_lvs.sh` now classifies this verdict as `mismatch_class=top_pin_mismatch`.
   - **Antenna diodes (8/13) — FIXED in `run_netgen_lvs.sh`.** Diode-bearing designs carry
     `sky130_fd_sc_hd__diode_2`. Magic extracts its primitive as an `X` *subcircuit instance*
     (`X0 VNB DIODE sky130_fd_pr__diode_pw2nd_05v5 perim=... area=...`) with no `.subckt`
     definition → netgen invents a black box with pins `1 2`. The PDK cell library models the
     same primitive as a `D` *device* (pins anode/cathode, properties `area`/`pj`). The class
     mismatch makes netgen flatten every diode_2 and fail top-level pin matching. **Fix (two
     parts):** (a) post-process `extracted.spice` rewriting diode `X` instances to `D` device
     lines and `perim=` → `pj=` (the netgen setup compares `pj` at 2% tolerance and deletes
     `perim`); (b) run netgen with `MAGIC_EXT_USE_GDS=1` so the PDK setup's `ignore class`
     rules for layout-only cells (tapvpwrvgnd, fakediode) activate instead of flattening.
     Validated: ultraembedded_irq_ctrl 355/355 devices, 398/398 nets, "Circuits match
     uniquely".
   - **Port-to-port feedthroughs (5/13) — FIXED via `POST_GLOBAL_PLACE_TCL` hook.** Diode-free
     bridge/interface designs (`assign out_port = in_port` in RTL: axis_ll_bridge,
     ll_axis_bridge, wb2axip_axilite2axi, axi_ram_wr_if, APB GPIO slave) put 2+ top-level port
     names on ONE net. SPICE cannot express two ports on one node, so Magic's extraction keeps
     only one name → pin lists can never reconcile ("Netlists match uniquely with port
     errors" + failed pin matching). Yosys emits a buffer for these assigns, but ORFS
     `global_place.tcl` runs `remove_buffers` (GPL_TIMING_DRIVEN=1) which deletes it and merges
     the port nets; OpenROAD's `buffer_ports` skips port-only nets. **Fix:**
     `scripts/flow/orfs_hooks/buffer_port_feedthroughs.tcl`, wired as `POST_GLOBAL_PLACE_TCL`
     (first point after the flow's last `remove_buffers`): splits every aliased output port
     onto its own net behind a real `MIN_BUF_CELL_AND_PORTS` buffer placed at the port pin
     (legalized by detailed placement). Idempotent; no-op for designs without feedthroughs.
     `tools/mk_sky130_project.py` (agent-r2g repo) wires it into every generated sky130
     config.mk; requires a backend re-run from synth/floorplan (the netlist changes).
   Both were LVS-setup/representation residuals, not layout defects. The PD flow itself was
   flawless across the wave: 0 DRC fails, 0 crashes, 0 timeouts.

6. **The feedthrough hook was ORPHANED from the `setup_rtl_designs.py` re-point path (found
   2026-07-01, /r2g-debug sky130hd round; FIXED `tools/setup_rtl_designs.py`).** The port-feedthrough
   fix (cause 5) is wired ONLY by `tools/mk_sky130_project.py` (`export POST_GLOBAL_PLACE_TCL =
   …/buffer_port_feedthroughs.tcl`). But a `/r2g-debug` platform round is bootstrapped by
   `tools/setup_rtl_designs.py --platform sky130hd --force`, whose `generate_config_mk` did NOT
   emit that line — so every re-pointed design's config.mk lacked the hook. Feedthrough designs
   (`picorv32_mem_adapter`, `sirv_gnrl_icb_arbt`) therefore hit `global_place`'s `remove_buffers`,
   merged their `assign out = in` ports onto one net, and Netgen honestly reported
   `top_pin_mismatch` ("Netlists match uniquely with port errors" + "Top level cell failed pin
   matching") — DRC clean, LVS mismatch, escalated `catalog_exhausted`. The honesty gates cannot
   see this (the design is honestly `mismatch`, honestly escalated); it surfaces only by tracing an
   individual escalation to "is the documented fix actually wired for THIS setup path?". The symptom
   `top_pin_mismatch` carried `strategies: {}` in heuristics.json because the 2026-06-11 fix was
   validated by hand, never recorded as a `fix_event`, so the learner never attached it.
   **This is the SAME parity class as the PDN-floor gap** (`setup_rtl_designs.py` omits config.mk
   provisions `mk_sky130_project.py` includes). **Fix:** `generate_config_mk` now emits
   `POST_GLOBAL_PLACE_TCL = …/buffer_port_feedthroughs.tcl` for `platform in (sky130hd, sky130hs)`
   (KLayout-LVS platforms don't need it). TDD: `tests/test_setup_sizing.py::test_sky130_wires_feedthrough_hook`
   (+ sky130hs + nangate45-omits). Suite green (6/6 setup_sizing, 34/34 related). **OPEN Layer-2
   follow-up:** this round's already-generated 671 pending configs were written PRE-fix, so they
   still lack the hook — a re-pointed round needs EITHER a between-waves targeted config.mk re-point
   for pending (not in-flow) designs, OR loop-side recovery (detect `top_pin_mismatch` → add hook →
   re-flow, mirroring the FLW-0024/PPL-0024/PDN-0185 loop self-heal pattern). **End-to-end validation
   CONFIRMED + committed `664f9c3`:** re-flowing `picorv32_mem_adapter` with the hook inserted **42
   `sky130_fd_sc_hd__buf_4` buffers on port-feedthrough nets** (flow.log), flipping Netgen from "Top
   level cell failed pin matching" → **"Circuits match uniquely"** (`top_pin_mismatch → clean`, DRC
   still clean). **Cross-design transfer CONFIRMED:** the same hook cleared `top_pin_mismatch → clean`
   on BOTH `picorv32_mem_adapter` AND `sirv_gnrl_icb_arbt` (drc=clean+lvs=clean+orfs=pass ingested for
   both; their `catalog_exhausted` escalations auto-drained on clean). The two designs are the exact
   `evidence_designs` of heuristics symptom `a0d6b4c6` (top_pin_mismatch), which had `strategies:{}`
   — this fix restarts that learning. NOTE for future recovery: drive an isolated design through a
   DIRECT flow+signoff (run_orfs.sh + run_drc.sh + run_netgen_lvs.sh), NOT `engineer_loop run` — the
   latter fires the GLOBAL (platform-unscoped) A/B planner and balloons a 2-design temp ledger into
   dozens of cross-platform arm entries (the tick-1 "A/B planner not platform-scoped" finding).

6. **`extract_lvs.py` clobbered a clean Netgen verdict on DRC-fail designs (2026-06-13,
   FIXED).** `extract_lvs.py` is a *KLayout* lvsdb/log parser. A Netgen run leaves NO KLayout
   artifacts (`6_lvs.lvsdb`/`6_lvs.log`/`lvs_run.log` all absent) — only
   `lvs/netgen_lvs_result.json`. The driver copies that Netgen result into `reports/lvs.json`
   up front, but its **DRC/LVS fix-loop branch** (`if DRC==fail || LVS==fail`) re-runs
   `extract_lvs.py reports/lvs.json` *after* the fix attempt to refresh DRC. With no KLayout
   artifacts every parser returned empty and the status fell through to `unknown`, **overwriting
   the clean Netgen verdict**. Net effect: any sky130 design that is **LVS-clean but DRC-fail**
   (e.g. `RV32I_Memorycontroller`: 84 genuine `m3.2` met3-spacing violations, Netgen "Circuits
   match uniquely") was mis-recorded as `lvs_unknown` and ingested as such — an honesty-invariant
   violation (`run_violations`/residual-class for an LVS-clean design now lies). Latent because
   the re-extract only runs on DRC/LVS failures, so all 174 prior clean-DRC passes were
   unaffected. **Fix (root cause, in `extract_lvs.py`):** before the KLayout parsers, if
   `lvs/netgen_lvs_result.json` exists and no KLayout artifacts are present, emit the Netgen
   verdict (status + `mismatch_class`) directly. Defers to KLayout whenever its artifacts exist,
   so nangate45 is byte-identical. Regression tests: `test_netgen_clean_is_honored`,
   `test_netgen_fail_is_honored`, `test_klayout_takes_precedence_over_netgen`. The driver needs
   no change — its post-fix-loop `extract_lvs.py` call is now correct for the Netgen path.

#### sky130 Netgen LVS Magic top-cell extraction hang (routing-dense designs) → bogus lvs_none

7. **Magic `extract all` did full-parasitic extraction → O(n²) coupling-cap hang on
   routing-dense top cells (2026-06-13, FIXED).** Symptom: a sky130 design with **clean DRC**
   reports residual `lvs_none` (empty `reports/lvs.json`), and `lvs/magic_extract.log` ends at
   `Extracting <top> into <top>.ext:` followed by `Created database crash recovery file` — with
   `netgen_lvs.log`/`netgen_lvs.rpt`/`netgen_lvs_result.json` all absent. Looks like a Magic
   SIGSEGV but is actually a **timeout-kill of a pathologically slow extraction**: the std cells
   extract in seconds, then the **top cell hangs for 8 min – 1 hr+** and `run_netgen_lvs.sh`'s
   `timeout --signal=TERM ... $NETGEN_TIMEOUT` (default 3600 s) SIGTERMs Magic, whose signal
   handler prints the crash-recovery line. **Root cause:** the extract TCL ran `extract all`,
   which computes substrate + **internodal coupling** capacitance; coupling extraction is O(n²)
   over nearby geometry and explodes on a routing-dense top cell (apb_spi_master / sha1_core /
   LIBELLULA / diffeq2: ~75 k via+cell instances). **LVS never uses parasitics** — it compares
   topology (devices + nets) only — so this work was pure waste. Observed live: 4 designs in one
   45-design wave hung this way; LIBELLULA's Magic reached **54 min CPU** before being killed.
   **Fix (in `run_netgen_lvs.sh`):** disable the parasitic passes before `extract all`:
   `extract no capacitance` / `coupling` / `resistance` / `adjust` / `length` (option names are
   exact — `adjustment` is a syntax error; `extract all` then extracts all cells using those
   do/no settings). Yields the **identical** LVS netlist far faster. Validated 2026-06-13:
   apb_spi_master went from an 8-min hang (killed, `lvs_none`) to **87 s** extract → Netgen
   "Circuits match uniquely" → `lvs_status=clean`. Connectivity is unaffected by R/C settings, so
   the 174 previously-clean designs stay clean (only their parasitic annotations, which LVS
   ignores, change). If a top cell is *still* slow after this, raise `NETGEN_TIMEOUT` rather than
   re-enabling parasitics. NOTE: when Magic still produces no SPICE, `run_netgen_lvs.sh` writes
   `{"status":"error",...}` and the driver should record an honest `lvs_incomplete`/`lvs_error`
   residual — never the ambiguous `lvs_none`.

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

**Action (auto-scale tiers raised 2026-06-03 — extraction is super-linear, not just compare):**
`run_lvs.sh` auto-scales `LVS_TIMEOUT` from the design cell count unless you set it explicitly:
base **5400s**, **>50K→14400s**, **>100K→21600s**, **>250K→28800s**. The old 3600s default
SIGTERM'd every ≥50K design *mid-extraction* and mislabeled it `incomplete` (extraction alone is
~2700s @51K, ~10200s @62K). See "LVS incomplete is mostly a comparer bug, not honest slowness" for
the full triage — a bigger cap only helps the genuine extraction-timeout subset; comparer
SIGSEGV/assertion designs need retry or a newer KLayout, not more time.
- run_lvs.sh uses `setsid timeout` to kill the whole process group and now reaps orphaned klayout on
  **any** nonzero exit (a SIGSEGV exits 2, which the old 124/137-only cleanup missed → multi-GB leak).
- **Run LVS serially on a shared host** — concurrent peer jobs externally SIGKILL long extractions.
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

### Platform re-target CLI mismatch — `--platform asap7` silently a no-op (2026-06-30)

**Symptoms:**
- Bootstrapping a NEW platform round (Step 1b): `python3 tools/setup_rtl_designs.py --platform asap7 --force`
  prints `Setting up 1 designs (force=True)... Done: 0 set up, 1 skipped, 0 errors` and **exits 0**.
- No `config.mk` is re-pointed: `grep -l 'PLATFORM *= *asap7' design_cases/*/constraints/config.mk` returns 0;
  every project still says the OLD platform. `build_pending_ledger.py --platform asap7` then enumerates
  0 designs ("round complete!", false) — or, if you skip the grep, the round silently builds the OLD PDK.

**Root Cause:**
`setup_rtl_designs.py` used a hand-rolled `for arg in sys.argv` parser that matched only the `=` form
(`--platform=asap7`). The documented invocations — SKILL Step 1b, `build_pending_ledger.py`'s header,
and the `/r2g-debug` command — all use the **space form** (`--platform asap7`). With the space form,
`--platform` matched no branch (silently ignored) and the value `asap7` fell through to the
`elif not arg.startswith("--")` positional-design branch → `selected=["asap7"]`, `platform_override=None`.
So the whole-corpus PDK re-target became a no-op while still exiting 0. The `--platform` feature was
added but never validated as documented (its sibling `build_pending_ledger.py` uses `argparse`, which
accepts both forms, hiding the asymmetry).

**Why it is dangerous:** it is the silent "never re-point ONLY the ledger" footgun in reverse — NEITHER
config.mk nor (correctly) the ledger gets re-pointed, and nothing errors. An "asap7 campaign" would have
rebuilt nangate45, teaching the loop lies under an asap7 label.

**Fix (commit on the asap7 round branch):** `setup_rtl_designs.py` now normalizes argv with
`_normalize_value_flags` (rewrites `--flag value` → `--flag=value` for `--designs/--designs-file/--platform/--rtl-dir`)
and the parse logic is split into a unit-testable `parse_setup_args(argv)`. Both arg forms now work.
Regression: `tests/test_setup_platform_cli.py` (8 cases: normalizer + parse outcome, space + equals).
Validated end-to-end: `--platform asap7 --force` re-pointed 708 designs (was 0).

### ASAP7 Fmax under-reported 1000× — picosecond liberty time-unit (2026-06-30, FIXED)

**Symptoms:**
- Every asap7 design's Fmax comes back absurdly slow: `Fmax ~ 0.00244 GHz (period 409.6 ns)` — i.e.
  single-digit MHz on a 7nm node. `reports/fmax_search.json` `winner.fmax_predicted_signoff` ~0.002–0.006.
- The flow itself is FINE (GDS + DRC clean); only the *recorded Fmax number* is wrong.

**Root Cause (confirmed):** ASAP7 liberty is `time_unit : "1ps"` (nangate45/sky130 use `1ns`). OpenSTA
adopts the liberty time unit, so the SDC `create_clock -period 10.0` means 10 **ps** and all reported
slacks/periods are in **ps**. `fmax_search.py`/`fmax_model.py` are unit-agnostic internally (period and
slack share whatever unit STA emits — self-consistent, so the search converges and the SDC stamped back
by `rewrite_clk_period` is correct, and the asap7 *flow builds at the right frequency*). BUT the
human/recorded outputs assume **ns**: `build_labels` does `1.0/t` GHz and labels "ns"; the winner
`fmax_predicted_signoff = 1/t`; `record_verify_triple` stores the ps value into the `clock_period_ns`
column. For asap7 (t in ps) this is 1000× wrong (409.6 ps = 0.41 ns = 2.44 GHz, recorded as 0.00244 GHz).
nangate45 never exposed it because its unit IS ns, so `1/period[ns]=GHz` happened to be correct. ASAP7 is
the first ps-unit platform run through the proxy.

**Fix (2026-06-30, reporting-boundary normalization, NOT a search-core rewrite):** the search,
SDC stamping, and closing-period seed are SELF-CONSISTENT in the STA unit (the flow builds at the
right frequency), and the stored `clock_period_ns` is read back by `seed_period` in that same unit —
so converting the *internal* period would force touching the proven timing core (and couples the seed
to the DB). The minimal, low-risk fix normalizes ONLY the human/recorded Fmax at the orchestrator
boundary in `fmax_search.py`:
- `_platform_time_unit_ns(platform)` returns ns-per-STA-unit (`1.0` for ns platforms — identity, so
  nangate45 is byte-identical; `0.001` for asap7=1ps). Map mirrors the ORFS liberty `time_unit`.
- `build_labels` and `search()`'s `winner` now report `fmax_predicted_signoff = 1/(t_star*tu)` (realistic
  GHz) and add `period_ns = t_star*tu`, while keeping the raw STA-unit `period` (what `rewrite_clk_period`
  writes + seeds the next search). `fm.search_loop` stays unit-agnostic.
- Tests: `test_fmax_search.py::{test_platform_time_unit_ns, test_build_labels_asap7_normalizes_1000x,
  test_search_asap7_records_realistic_ghz, test_search_nangate45_fmax_unchanged}`.
**Residual (documented, not a lie):** the DB `clock_period_ns` column still holds the STA-unit period for
asap7 (ps) — self-consistent per platform (only `seed_period` reads it back, never cross-platform) and
NOT honesty-gated; the authoritative recorded Fmax is `reports/fmax_search.json` `winner` (now correct).
A full all-internal-ns normalization (convert at `run_probe`/`seed_period`/`rewrite_clk_period` too) is a
deeper follow-up only if a consumer ever needs honest ns in that column. Any asap7 Fmax recorded BEFORE
this fix is recoverable: multiply the raw period by `time_unit_ns`.

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

**Loop auto-handling (2026-06-28, `synth_memory_relax`).** `engineer_loop.process_one` now
RECOVERS this in-loop instead of escalating it: on a synth abort whose flow.log contains
`exceeds SYNTH_MEMORY_MAX_BITS`, `_raise_synth_memory_cap` sets the cap to `65536` in
`constraints/config.mk` and re-flows ONCE. The retry is recorded as a learnable `fix_log` row
(`strategy='synth_memory_relax'`, `check='orfs_stage'`, `class='synth'`) so the next ingest
projects it into a `fix_event` → Tier-3 recipe (a cross-design prior, exactly like the FLW-0024
die-resize). If the raise does not clear it (memory still over budget even at 65536), it escalates
honestly as `synth_memory_residual` (use a fakeram macro), never `unseen_crash`. Root cause: the
loop previously collapsed EVERY early synth abort into `unseen_crash` — 15 of 79 nangate45
"mystery crashes" were really this mechanical, documented cap (2026-06-28 unseen_crash audit).
Validated on `verilog_axis_axis_fifo`: default-cap synth aborts in 3 s; cap=65536 expands the
memory to flops and synth proceeds. Tests: `tests/test_synth_abort_classify.py`.

**Size gate (2026-06-28 iter-7).** FF-expansion is the RIGHT fix only for MODEST memories. A large
memory (this corpus: 17408 / 18944 / 40960 bits) FF-expands into 17-41 K flops -> a ~153 Kum^2 design
whose route TIMES OUT and whose KLayout LVS legitimately runs ~4h at 99% CPU, tail-blocking the
campaign on a design that mostly never signs off (all 4 memcap re-queues escalated
`route_congestion_residual` after a CLEAN synth). The in-loop recovery now gates on
`_synth_memory_ff_expandable` (parses `Largest single memory instance: N bits`): N <=
`_SYNTH_MEM_FF_LIMIT` (16384) -> FF-expand; larger -> escalate `synth_memory_residual` routed to a
fakeram HARD MACRO, never FF-expand into a tail-blocking design. The A/B arm is unchanged (the recipe
still validly clears synth and stays promoted; only the APPLICATION policy is refined).

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

**Loop auto-handling (2026-06-28, `incomplete_missing_header`).** When the harvested RTL is
genuinely incomplete (the header was never shipped upstream — `setup_rtl_designs.py` already marks
these `metadata.json status=incomplete_missing_headers`, `harvested_headers=[]`), the loop can't
synthesize them. `engineer_loop.process_one` now escalates such a synth abort (`_is_synth_missing_header`)
under the honest reason `incomplete_missing_header`, NOT `unseen_crash` — so the escalation queue
and the learner are not told this is a novel synth symptom to diagnose. This was the LARGEST slice
of the misclassified bucket: 48 of 79 nangate45 `unseen_crash` escalations (2026-06-28 audit). These
need source completion (upstream header fetch), not a flow recipe.

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

**Include-guard cross-file macro leak (the header IS present, but only the first
module sees its body):** symptom is *not* "Can't open include file" — it is
`do-yosys-canonicalize` aborting with
`ERROR: Failed to resolve identifier \FOO for width detection!` (or
`undefined macro`) for a token that **is** defined in a header that **is** in
`rtl/` and **is** `` `include ``d by the failing module. Mechanism: yosys reads
every RTL file in ONE preprocessing context, so `` `define `` macros persist
across files. A header wrapped in `` `ifndef FOO_VH / `define FOO_VH / `endif ``
that is `` `include ``d *inside* several modules emits its body only into the
**first** including module; for every later module the guard is already true and
the body is skipped, leaving its identifiers unresolved. This bites
reconstructed/harvested content headers that carry **module-scoped `localparam`s**
(FSM-state encodings, CSR address maps) which are legitimately included per
module — PYGMY_V32I_rtl_core (`core.vh` + `control_status_registers.vh`, included
in core/decode/csr/lsu) is the canonical case; first run failed at
`decode.v:138: Failed to resolve identifier \CORE_STATE_EXEC`.
- **Diagnose:** the token is defined and the header is present + included →
  `grep -n "ifndef\|define\|endif" rtl/<header>.vh`. If the header is guarded
  **and** included inside >1 module, this is the bug.
- **Fix (design-safe):** remove the `` `ifndef/`define/`endif `` guard from the
  header. Per-module `localparam`s do not collide across modules, so re-emitting
  the body into each including module is correct — the guard was never needed for
  a per-module-included localparam header (guards only matter for top-level
  `` `define `` / global decls). Do **not** instead hoist the localparams into
  one module or convert to `` `define `` macros — that changes RTL scoping.
- After the fix the design synthesizes and routes normally (PYGMY_V32I_rtl_core:
  clean timing WNS +6.4 ns, 11.6K insts, GDS produced on the first post-fix run).

**Missing proprietary primitive library (Synopsys GTECH / DesignWare) — incomplete
bundle, do NOT stub:** some legacy ASIC RTL (e.g. `faraday_dsp`) is *structural* — its
clock-gating, register, and DFT-scan wrappers instantiate Synopsys GTECH
technology-independent primitives (`GTECH_AND2/3`, `GTECH_AND_NOT`, `GTECH_MUX2/4/8`,
`GTECH_NOR2/3`, `GTECH_NOT/BUF`, `GTECH_OA21`, and the sequential `GTECH_FJK3` JK
flip-flop) directly by module name. These cells live in `$SYNOPSYS/.../gtech.v` /
`dw_foundation.sldb`, ship only with Design Compiler, are **not redistributable**, and
are absent from the open-source RTL bundle.
- **Signature:** synthesis reaches `hierarchy -check -top` and aborts at
  `do-yosys-canonicalize` with
  `ERROR: Module '\GTECH_AND3' referenced in module '\CLKC' in cell '\uck6' is not part
  of the design.` (the exact leaf cell varies). Earlier files elaborate fine as
  pre-parsed AST; the failure is the first unresolved GTECH leaf, not a parse error.
- **Diagnose:** `grep -rhoE "GTECH_[A-Z0-9_]+" rtl/ | sort | uniq -c` to enumerate the
  used primitives, then `grep -rln "module[[:space:]]\+GTECH" .` (repo-wide) to confirm
  none are defined anywhere (including sibling bundles and `assets/`).
- **Action: classify as incomplete-bundle / skip — do not burn the retry.** No config.mk
  knob supplies a missing cell library. The combinational GTECH cells *could* be stubbed,
  but the sequential `GTECH_FJK3` and the exact MUX4/MUX8 select encodings and async
  set/reset polarities are vendor-defined; reconstructing them is *invention*, not
  recovery (same rule as arbitrary design-internal macro encodings above). A guessed stub
  passes synthesis but yields a functionally-wrong netlist that fails LVS/sim silently —
  never acceptable for a signoff-quality flow. Record
  `status: incomplete_missing_primitive_lib` and stop. If the operator can supply the
  genuine `gtech.v`, point `VERILOG_FILES` at it and re-run.

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

#### Sub-variant: synth timeout is a Yosys AST-elaboration blowup (NOT memory→flop expansion) — raising the timeout does NOT help

**Signature (distinguish from the memory-expansion case above):**
- `1_1_yosys_canonicalize.log` ends mid-elaboration at
  `N.M. Executing AST frontend in derive mode using pre-parsed AST for module '\<leaf>'`
  with **no further progress** — it never reaches step 14 (ABC) or even `proc`/`opt`.
  (The memory-expansion case gets *past* elaboration and stalls in ABC/opt with a huge gate count.)
- The leaf module computes its structure from a **constant Verilog `function` called inside a
  `generate` loop** (one call per output bit). The function does array-of-vector
  shift/XOR work whose cost is super-linear in the bus widths.
- Re-running with a larger `ORFS_TIMEOUT` reaches the *same* line and times out again
  (e.g. 3600s → 14400s, both 124).

**Root cause:** Yosys 0.63's AST `derive` constant-folds the function on every generate
iteration. For a memory-array-shift function evaluated `(W_a + W_b)` times with per-call cost
~`O(W_b · W_a²)`, the elaborator does not converge in hours. This is a **synthesis front-end**
limit, independent of `STYLE`, `-DSYNTHESIS`/translate_off, `ORFS_TIMEOUT`, utilization, density,
or any P&R lever — none of which touch elaboration.

**How to confirm cheaply (do this BEFORE spending an ORFS budget):** run yosys standalone on
just the VERILOG_FILES with a short cap — `timeout 180 $YOSYS -q -p 'read_verilog <files>;
hierarchy -check -top <top>; stat'`. If it times out at 180s it will also time out at 4h.
Trying `chparam -set STYLE "LOOP"` and/or `read_verilog -DSYNTHESIS` and re-capping at 300s
isolates whether the hang is in the constant function (it is, if both still hang) vs. the
mask *usage*.

**Action:** No flow/config lever fixes this — report it honestly as `intractable` at the synth
stage with `make_status=124`, do NOT keep raising the timeout. A real fix requires RTL surgery
(precompute the masks offline and emit a flat XOR network, or pre-elaborate with a faster tool),
which is out of automated scope — escalate to the user.

**Validated case:** `verilog_lfsr_rtl_lfsr_descramble` (Alex Forencich parametrizable LFSR,
`lfsr_descramble` → `lfsr` at LFSR_WIDTH=58 / DATA_WIDTH=64; `lfsr_mask` constant function
shifts a 58×58-bit memory 64×, called 122×). Two backend runs hung at step 7.2
`derive … module '\lfsr'` (3600s then 14400s, both exit 124). Standalone yosys read+hierarchy
times out at 180s (default) and at 300s with `STYLE="LOOP" -DSYNTHESIS`. No GDS.

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

#### Sub-case: wide single-instance lfsr (DATA_WIDTH 64) is INTRACTABLE, not just slow (2026-06-08)

Some standalone Forencich designs instantiate **one** `lfsr` with a large
`DATA_WIDTH`/`LFSR_WIDTH` rather than a genvar sweep of small widths, and for
those the "raise `ORFS_TIMEOUT` to 14400s" remedy above is **not enough** —
they do not finish at 4h and never will on this Yosys/host.

- **Confirmed intractable:** `verilog_lfsr_rtl_lfsr_scramble` and
  `verilog_lfsr_rtl_lfsr_descramble` — the 64b66b Ethernet (de)scrambler:
  top instantiates `lfsr` with `DATA_WIDTH=64, LFSR_WIDTH=58`. Two real runs
  hung at the identical line `7.2. Executing AST frontend in derive mode …
  for module '\lfsr'` and timed out (exit 124) at 3600s and 14400s.
- **Confirmed tractable (for contrast):** every `DATA_WIDTH=8, LFSR_WIDTH≤32`
  sibling completed to GDS — `verilog_lfsr_rtl_lfsr`, `..._crc`,
  `..._prbs_gen`, `..._prbs_check`. The breaking variable is `DATA_WIDTH`.
- **Why it's a cliff, not a slope:** the `lfsr_mask()` constant function costs
  ≈ `O(DATA_WIDTH × LFSR_WIDTH²)` per call and is re-evaluated once per
  generate bit (`LFSR_WIDTH + DATA_WIDTH` calls). DATA_WIDTH 8→64 and
  LFSR_WIDTH 31→58 takes the work from ≈0.3M to ≈26M heavy multi-bit vector
  iterations in Yosys's AST interpreter (~88×).
- **Proof it's pure CPU, not a fixable OOM:** a bounded standalone derive of
  the real `lfsr` instance ran 597s of CPU in a 600s wall window (100% busy),
  made zero progress past the `lfsr` derive, and peaked at only **158 MB** RSS.
  Memory knobs (`SYNTH_MEMORY_MAX_BITS`) and `ABC_AREA` are non-levers — the
  hotspot is single-threaded front-end constant-folding.
- **STYLE override does NOT help.** Forcing `STYLE="REDUCTION"` (via
  `VERILOG_TOP_PARAMS = STYLE REDUCTION`, which `chparam`-propagates to the
  child instance) only changes how the mask is *consumed*; the mask is still
  *computed* by the same nested-loop constant function. Don't burn a run on it.
- **Action:** classify as `ast_pathology` (intractable sub-bucket) and STOP —
  do not launch another full ORFS run shorter than the 14400s that already
  failed; that is guaranteed thrashing. A genuine fix would require rewriting
  `lfsr.v`'s mask generation to a closed-form/iterative form Yosys can fold
  cheaply, or escalating to a Yosys version with a faster const-eval — both
  out of scope for a bounded single-design completion attempt.

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

### VHDL-only design (no Verilog frontend) — intractable, not a flow bug

**Symptoms:**
- `tools/setup_rtl_designs.py` skips with `no design_meta.json`, yet the source dir is
  clearly a real, large design.
- Source dir ships a legacy `config.tcl` with `FILE_FORMAT "vhdl"`; `rtl/` is dominated
  by `.vhd` files.
- If forced through synthesis: `read_verilog … leon.vhd: syntax error, unexpected
  TOK_DECREMENT` (Yosys), or `expected member` (slang/SystemVerilog), on the first VHDL
  `entity`/`port` line. `ghdl.so: cannot open shared object file` (no GHDL Yosys plugin),
  and no `ghdl` binary on `PATH`.

**Root Cause:**
This flow is **Verilog/SystemVerilog-only**. Yosys here has no GHDL/VHDL frontend, so a
VHDL RTL tree is unsynthesizable — it is an *unsupported language*, not a missing config or
a flow bug. **Trap:** a VHDL SoC often ships a handful of Verilog *leaf peripherals* (e.g.
the OpenCores Ethernet MAC `eth_top.v`/`ethermac.v` inside a LEON2 tree) — do NOT mistake
those for a Verilog version of the CPU/SoC top.

**Action:**
- Mark **intractable (unsupported language)** in batch results. Do not retry, do not stub.
- `setup_rtl_designs.py` now detects this (`_is_vhdl_only`: `config.tcl` FILE_FORMAT vhdl,
  or `.vhd` files outnumber `.v`/`.sv`) and emits the explicit reason
  `VHDL design — unsupported (no GHDL/Verilog frontend)`.
- A real fix requires either installing a GHDL Yosys plugin (out of scope) or a
  Verilog/SV reimplementation of the RTL (escalate to user).

**Known cases:** `gaisler_leon2` (LEON2 SPARC V8 SoC, top entity `leon`, 79 `.vhd` files +
3 unrelated Verilog Ethernet-MAC leaves), 2026-06-08.

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

<!-- r2g-lesson:
id: lesson-nangate45-antenna-diode
status: active
trigger: {check: drc, class: "*_ANTENNA", platform: nangate45}
strategy_ids: [antenna_diode_repair]
-->

**Symptoms:**
- DRC report shows METAL*_ANTENNA violations (e.g., METAL4_ANTENNA, METAL5_ANTENNA)
- All violations are antenna-rule related; no spacing/width violations
- Violation counts vary across configs of the same design (layout-dependent)

**Root Cause:**
Long unbroken metal routes accumulate charge during plasma etching, which can damage thin gate oxides. Normally OpenROAD's `repair_antennas` fixes this by inserting antenna diodes during global/detailed route.

**nangate45 — was "inert", now FIXED (2026-06-02; supersedes the 2026-06-01 Finding B
"unfixable residual" conclusion).** The stock nangate45 LEFs were missing the antenna
model in **three** places, so OpenROAD `check_antennas` reported 0 and `repair_antennas`
did nothing:
1. tech LEF (`NangateOpenCellLibrary.tech.lef`) has **no per-layer antenna ratios** at all
   (`grep -ci ANTENNA … .tech.lef` = 0) → no threshold to check against.
2. the SC LEF ORFS actually uses (`NangateOpenCellLibrary.macro.mod.lef`) has
   **`ANTENNAGATEAREA` stripped** from std-cell pins (`grep -c ANTENNAGATEAREA` = 0); the
   full per-pin model still lives in the sibling `NangateOpenCellLibrary.macro.lef` (same
   cell set) → without gate areas OpenROAD cannot form a ratio (0 even at ratio 1).
3. the only diode `ANTENNA_X1` has **`ANTENNADIFFAREA 0.0`**; OpenROAD only accepts a
   `CORE_ANTENNACELL` diode when `diffArea > 0` (RepairAntennas.cpp:559) → `GRT-0246
   "No diode … found"`, zero diodes inserted.

**Fix — install the antenna model (one-time, reversible):**
```bash
tools/install_nangate45_antenna.sh            # ratio 300 (matches signoff), diff-area 0.1
tools/install_nangate45_antenna.sh --status   # verify: 10/10 ratios, 387 gate-area pins, diode>0
tools/install_nangate45_antenna.sh --uninstall # restore stock LEFs from *.r2g-pre-antenna.orig
```
This adds `ANTENNAMODEL OXIDE1` + `ANTENNAAREARATIO 300` to every routing layer (300 **matches**
the KLayout signoff deck — it does NOT relax it), merges the per-pin gate areas back from
`.macro.lef`, and gives the diode a positive `ANTENNADIFFAREA`. The KLayout 300:1 deck is
untouched. With the model installed, OpenROAD's per-net PAR matches KLayout's ratio exactly
(stream_register: OpenROAD 488.80 vs KLayout 489.17).

**The diodes-NOT-jumpers principle (the trap).** Once the model is installed, OpenROAD's
*default* repair fixes antennas with **jumpers** (layer hops): its partial-area-ratio drops
and it reports `Found 0 antenna violations`, but **KLayout still flags** — the FreePDK45
`antenna_check(gate, metalN, 300, diode)` sums the *whole net's* metalN area connected to the
gate (jumpers don't reduce that) and only credits a connected **diode** (`#adiodes`,
`#diode_factors`). So the fix must force *diode* insertion, not jumpers.

**Automated fix:** `scripts/flow/fix_signoff.sh <proj> nangate45 --check drc` auto-applies the
`antenna_diode_repair` strategy (see `references/signoff-fixing.md`):
`SKIP_ANTENNA_REPAIR=1` (disable global-route jumper repair) + `MAX_REPAIR_ANTENNAS_ITER_DRT=10`
(force physical `ANTENNA_X1` diode insertion during detailed routing), then re-run from route.
Validated 2026-06-02: stream_register 489:1 → CLEAN with 1 diode. The 400:1 deck relaxation is
RETIRED — real layout fixes only; the deck is never relaxed (install honest 300:1 via
`tools/install_nangate45_drc.sh`).

**LVS is not broken by the inserted diodes.** The bundled `FreePDK45.lylvs` flattens the
physical-only `ANTENNA_X1` cell (`Flatten layout cell (no schematic): ANTENNA_X1`), so the
diodes are not counted as schematic-missing devices — stream_register stays `LVS CLEAN` with a
diode inserted (verified 2026-06-02). Re-run LVS after a DRC antenna fix to refresh the report.

**Coverage, residuals, and fast verification (2026-06-02 campaign).** Validated on the small
fail set: stream_register, riscv_alu4b, fifo_basic (16→0, 13 diodes), pyocdriscv stream_register,
iccad2017_unit2_G / _unit18_F, eth_arb_mux (133→0, 19 nets) all → **CLEAN**; cpu → 0 antennas
(OpenROAD). The one partial case, eth_demux, went **147 → 3** (98% reduction) and resists full
closure:

- **Irreducible residual (per-gate vs summed-gate).** KLayout's `antenna_check` flags via the
  *worst single gate* on the net (e.g. `#agate 0.02625`, `#ratio 307`), while OpenROAD's per-net
  PAR uses the *sum* of `ANTENNAGATEAREA` over the net's fanout. A high-fanout net driving one
  tiny gate therefore reads `<< 300` in OpenROAD even though KLayout sees `> 300`, so
  `repair_antennas` never touches it. A tighter install (`--ratio 200`) clears *single-gate*
  borderline nets but not these multi-gate ones (re-routing also shifts which net is borderline,
  a moving target). Report the small remainder as an **honest residual** — never relax the deck.
  Clearing it would need KLayout-violation-driven diode insertion (map flagged polygons → nets →
  insert diodes), not yet implemented.

- **Fast antenna verification for large designs.** Full KLayout DRC is impractically slow on
  ≥~5K-cell designs (the FreePDK45 FEOL `or`/well-derivation rule runs minutes-to-stuck — cpu hit
  ~30 min). But with the model installed, **OpenROAD `check_antennas` matches KLayout's antenna
  ratio to the decimal** (stream_register 488.80 vs 489.17), so use it as the antenna verifier:
  `read_lef <tech> <sc>; read_def 6_final.def; check_antennas`. Reserve the full KLayout DRC for
  final signoff / non-antenna checks.

- **Larger designs carry *unchecked* antennas.** Every nangate45 design ≥~10K cells in the corpus
  is `clean_beol` — BEOL-only DRC skips ANTENNA, so antennas were never verified (e.g. oc54_cpu,
  10K, has 2 METAL4 antennas found by an antenna-inclusive check). The fix (re-route + diode
  repair) and OpenROAD verification extend to them; an antenna-inclusive DRC mode that skips the
  slow FEOL derivations (not just the `.output` checks) would let KLayout sign them off too.

**Other platforms** (sky130/asap7/gf180/ihp — ship a real antenna model + non-zero-diffarea
diode): unchanged — raise repair iterations (`MAX_REPAIR_ANTENNAS_ITER_GRT/_DRT = 10`, default
5); the diode is auto-discovered from its `CLASS CORE ANTENNACELL` LEF declaration (do NOT set
`CORE_ANTENNACELL` — not an ORFS env var).

### Hold Timing Violations Post-CTS

<!-- r2g-lesson:
id: lesson-hold-post-cts
status: active
trigger: {check: timing, platform: "*"}
strategy_ids: []
-->

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

<!-- r2g-lesson:
id: lesson-setup-timing-tiers
status: active
trigger: {check: timing, platform: "*"}
strategy_ids: [period_relax, utilization_reduce, backend_aware_synth_retune]
-->

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

## ASAP7 residual-DRC-by-design — `asap7.lydrc` is NOT flow-achievable-clean (deck-vs-flow truth, 2026-06-30)

<!-- r2g-lesson:
id: lesson-asap7-drc-deck-floor
status: active
trigger: {check: drc, platform: asap7}
strategy_ids: []
-->

**Finding (decisive, workflow-verified):** asap7 designs SYSTEMATICALLY carry residual `asap7.lydrc`
KLayout-signoff DRC that **no ORFS flow lever (density/route/util relief) can clear** — this is a
predictive-PDK deck-vs-flow TRUTH, not a fixable loop/flow bug and not a stale-log artifact.
- **Proof:** running the shipped deck on ORFS's OWN canonical `gcd` reference —
  `klayout -zz -r platforms/asap7/drc/asap7.lydrc` on `results/asap7/gcd/base/6_final.gds` — yields **20
  violations** (V1.S.4, V*.M*.AUX.2, M4.S.5, M1.S.6, LIG.S.4-5, …), the exact rule classes that dominate
  every campaign design. Yet ORFS ships gcd as *router-DRC-clean*. **ORFS deliberately gates asap7 only on
  `detailedroute__route__drc_errors=0`** (TritonRoute's LEF-based DRC; `designs/asap7/gcd/rules-base.json`);
  the community `asap7.lydrc` deck (reverse-engineered from the ASAP7 DRM) is a **non-default, non-gated**
  `make drc` target. So router-clean ≠ `asap7.lydrc`-clean *by design*.
- **The irreducible floor in EVERY design** (min 8 violations, none 0): ~⅓ FEOL/MOL **cell/library-internal**
  on layers the detailed router never emits (`GATE`=TYPE MASTERSLICE poly; `LIG/LISD/SDT/V0`=MOL —
  `run_drc.sh:156` already labels this block "all library-internal"); ~⅙ **tech-LEF via-width AUX**
  mismatches (`asap7.lydrc:361/423/431`) present in ALL designs incl. the 8-cell `Control_logic`
  (density-independent); the rest **router-model-vs-KLayout-deck** BEOL disagreements (M4.S.5, V1.S.4,
  M1.S.2/.6) that appear uniformly even on tiny designs (NOT congestion hotspots). `clean_beol` is also
  empirically unreachable (BEOL layers carry their own universal router-vs-deck violations).
- **The 5 "stuck"** are GENUINE fresh `asap7.lydrc` KLayout timeouts (2h) on costly ops
  (`:186 ACTIVE.W.2`, `:193 ACTIVE.WELL.EN.1`, `:318 M3.S.2` over ~2M edges) — NOT stale nangate45 reads,
  NOT a wrong-deck re-queue class. (A leftover pre-retarget `6_drc.lyrdb` naming FreePDK45 is a red herring
  the `stuck` classification does not read; `run_drc.sh:245` keys off the FRESH `6_drc.log`.)

**Consequence for the learning loop (HONEST, not a bug):** on asap7, **A/B arms BOTH DRC-failing is CORRECT**
— no recipe can make a design `asap7.lydrc`-clean, so trials are honestly `inconclusive` and **no
DRC-based promotion is achievable**. The loop refusing to promote here is the honesty contract working.
Do NOT chase a "first asap7 DRC-clean design" as a loop fix. To get first-promotion evidence: either judge
asap7 on the router-internal DRC ORFS itself signs off on (`detailedroute__route__drc_errors=0`,
attainable → arms can diverge), or run first-promotion on a platform with a genuinely honored signoff deck
(nangate45 router-DRC / sky130hd **KLayout `sky130hd.lydrc`** deck — NOT the naive Magic path, which
over-reports std-cell li/mcon and is unwired from `extract_drc`; see "Magic DRC Failure"), where the
store's genuine promotions already live. The 12
asap7 `drc=clean` rows that existed were fabrications (arm copytree inherited the nangate45 subject's clean
`drc/`+`reports/` before the 2026-06-30 copytree-exclude-stage-dirs fix) — reconciled out; genuine asap7
`drc=clean` count = 0.

**The authoritative resolution is Calibre, not a better KLayout deck** (2026-07-01). The community
`asap7.lydrc` floor is a *deck* limitation; the official **encrypted ASAP7 Calibre deck** (from
asap.asu.edu) is the only genuinely clean-able asap7 DRC/LVS. This machine has Calibre + a license but
NOT the deck (only placeholder READMEs). A guarded scaffold is in place — `scripts/flow/run_calibre_drc.sh`
+ `scripts/extract/extract_calibre_drc.py` (skip cleanly until the deck is installed; emit `engine:calibre`
verdicts in the `extract_drc` schema). When the deck lands and smoke-passes on this Calibre (2025.1 vs the
deck's 2017.4 target — a real version risk), asap7 DRC-clean becomes achievable and this "no asap7
promotion is honest" premise must be revisited. Full runbook + integration steps: `references/calibre-signoff.md`.

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

## Learning-Loop Closure Failures (A/B promotion never fires)

This is a *meta* failure class: not a flow that fails, but the **learning loop failing to
PROMOTE a genuinely-good recipe** — the loop records fixes and runs A/B trials yet `promoted`
never grows. Symptom (the alarm): `ab_trials` is non-empty and `fix_events` grows across waves,
but `SELECT COUNT(*) FROM recipe_status WHERE status='promoted'` is flat — and **per-platform**,
e.g. NO `nangate45` recipe ever promotes while sky130hd does. The coarse "ab_trials non-empty"
check passes, so the loop looks live while being inert for a whole platform/recipe class. Found
by the 2026-06-23 audit; see `docs/superpowers/plans/r2g-loop-closure-audit-2026-06-23.md`.

**Pattern 1 — A/B arms do byte-identical work (the dominant cause).** `plan_arms_for_candidates`
copytrees each arm dir; if it does NOT exclude `reports/`, a *signoff* arm (whose subject is a
previously-FIXED clean project) inherits a clean `reports/drc.json`. `process_one` then reads
that stale verdict (`_signoff_status`) and `_mark_clean`s the arm **before `_run_fix` runs**, so
arm A's `R2G_FIX_EXCLUDE` and arm B's `R2G_FIX_RANK_FIRST` never take effect — both arms are
identical and only `wall_s` differs. Tell-tale: `ab_trials.metrics_json` shows arm A and B with
identical `is_success`+`outcome_score`+`fix_iters`. **Fix:** exclude `reports` from the arm
copytree AND never short-circuit a `kind=='ab_arm'` to clean (engineer_loop.py). A signoff arm
must always reach `_run_fix`. sky130 recipes escaped this only because their subjects were
*failures* (no clean report to copy), so their flows genuinely diverged.

**Pattern 2 — noise decides the verdict.** When both arms reliably sign off (a success-LCB tie),
do NOT break the tie on raw mean wall-clock with a flat band — k≈2 flow-time jitter then flips
`win`↔`loss` at random and demotes a good recipe to shadow. **Fix:** a variance-aware tiebreak
(combined-stderr; `<2` repeats → `inconclusive`), so a cost-neutral correct recipe stays shadow
*honestly* rather than oscillating. The real promotion path is the success-rate LCB (arm B signs
off where arm A, with the recipe excluded, does not) — keep that intact.

**Pattern 3 — the verdict can't accumulate (volatile lifecycle key).** `design_class` is part of
the `recipe_status`/`ab_trials` key but is derived per-run; an FLW-0024 place abort re-ingests
with `cell_count=NULL` → size band flips to `unknown` → `diff_and_enqueue` sees a "new" key and
RESPAWNS a fresh candidate while the prior verdict strands on the old class. **Mitigation (#9a):**
pin the size band from the project's prior non-null `cell_count` at ingest (the stored
`cell_count` stays honestly NULL). **Structural fix (deferred #9b):** key on
`(symptom_id, platform, strategy)` only.

**Pattern 4 — silently-skipped candidates.** A candidate with fewer than `n_ab_designs`
resolvable on-disk subjects makes `plan_trial` return `None`; do not `continue` silently — log +
open an idempotent `unvalidatable_insufficient_subjects` escalation and leave it `candidate`
(NEVER demote — `diff_and_enqueue` won't re-enqueue a symptom that already has a row, so demotion
is terminal).

**Pattern 5 — junk `unknown` arm rows.** An A/B arm whose flow produced no backend (clone/setup
aborted) must NOT be ingested — it becomes an `orfs_status='unknown'`, `design_name='unknown'`
row that, via the latest-row-per-project metric query, clobbers a prior real arm outcome and
turns a trial into a false loss. **Fix:** `_ingest` skips a project with no backend stage_log AND
no ppa.json; the arm escalates `route_arm_incomplete`; the judge records no verdict for an
all-None pair.

**Pattern 6 — a recovery that records no fix_event is unlearnable.** A backend-stage recovery
applied as a raw config rewrite + reflow (the FLW-0024 die resize) leaves ZERO `fix_events`, so
the learner builds no trajectory/recipe and Gate A never enqueues it — a validated recovery the
loop can never promote (contrast `route_relief`, which goes through `fix_signoff.sh` and DID
promote). **Fix:** record the recovery as a `fix_log` row (`strategy`,`check=orfs_stage`,
`violation_class=<stage>`) so the next ingest projects it into `fix_events` (symptom_id is
computed at ingest from the row — no separate-writer drift); record the REAL outcome
(`cleared`/`no_change`) so negative learning is preserved.

**Honesty corollary — re-validating after a fix that invalidates past verdicts.** When a fix
makes prior A/B verdicts *known-contaminated* (e.g. Pattern 1), do NOT rewrite the immutable
`ab_trials` history. Instead flip the affected `recipe_status` rows back to `candidate` (a
current-state edit) so a fresh, valid trial runs. Then EXECUTE+VERIFY: confirm `ab_trials`
gains rows with arm A `is_success` ≠ arm B and the recipe transitions `candidate → promoted`.
"The A/B machinery existing" is never proof the loop learns — only a recorded promotion is.

### Sub-variant: whole CLASSES inert + judge defeated by noise/last-trial (2026-06-24 closure)

**Pattern 7 — a whole strategy CLASS routes to an A/B check that can't exercise it.**
`_symptom_check` mapped every non-route symptom to `--check both` (DRC/LVS), so a **timing**
(`period_relax`) or **place** (`core_util_relief`) recipe's `R2G_FIX_EXCLUDE/RANK_FIRST` were
no-ops → arms byte-identical → permanent `inconclusive`, while each timing arm burned a full
multi-hour signoff (the campaign stall). FIX: `_symptom_check(conn, symptom_id, strategy)` routes
by **strategy** — place→`place` (apply-then-flow backend arm; arm B `_resize_to_core_util`),
timing→`timing` (`fix_signoff --check timing`). Because a timing miss never aborts the flow,
`is_success` ties both arms — `_arm_metric(timing=True)` judges on `wns_ns`/`timing_tier` instead.
`_ab_coverage_gap` then *refuses to plan* an arm that still can't diverge (`lvs_resolve_unknown`, or
≥`AB_INCONCLUSIVE_MAX` inconclusive trials with 0 decisive) — escalates `ab_coverage_gap`, never
demotes. (A latent trap: `check_timing.py` wrote `wns`/`clock_period` but `diagnose`'s timing plan
reads `wns_ns`/`clock_period_ns` → `period_relax` emitted no SDC edit; aliases added.)

**Pattern 8 — `inconclusive` demoted to a TERMINAL `shadow`.** `record_trial` demoted on every
non-win; `shadow` is never re-planned (re-enqueue no-ops on an existing row) → one inert/noisy trial
permanently buried a recipe. FIX: `inconclusive` carries no information and NEVER demotes.

**Pattern 9 — the LAST trial overwrote the status (UPSERT), defeating the per-trial LCB.** A
trailing noisy loss demoted a net-winning recipe. FIX: `ab_runner.judge_recipe` makes `recipe_status`
a function of the FULL `ab_trials` corpus (net wins>losses → promote; net losses>wins → shadow; else
unchanged), so a later win can revive a shadow and a single late loss can't bury a net winner.

**Pattern 10 — the success-tie tiebreak flipped on flow-time jitter.** The cost tiebreak floored
the |Δwall| bound at 1% of the mean, so ~3% scheduler noise promoted a deterministic-same-outcome
recipe. FIX: require |Δwall| ≥ `COST_FLOOR=8%` AND sign-consistency (`max(cheaper)<min(dearer)`);
`se==0` is MAXIMAL confidence so a real large deterministic cost win still promotes.

**Fixture≠production corollary (the recurring trap).** `engineer_loop.fmax_drain`'s SDC stamp was
silently inert off-test: `_fmax_one` did `import fmax_model`, but the module only put `knowledge/` on
`sys.path` — `conftest.py` injected `scripts/reports/` so the unit test passed while production
returned `characterized 0 design(s)` and never stamped (the same class as the 22f3e67 fmax pilot
bug). Guard: a feature invoked as a real CLI needs a **subprocess** regression test that does NOT
inherit conftest's path help, and a no-op must be **uncountable** (stamp-then-verify), never swallowed
in a bare `except` that returns a truthy-looking status.

### Sub-variant: arms RUN but don't APPLY different work; a stale verdict freezes a fake promotion (2026-06-26)

The 2026-06-24 fixes made the arms RUN and routed classes correctly, but a resumed nangate45 campaign
STILL showed the alarm — `ab_trials` grows, `promoted(nangate45)` **flat at 1 for 8 waves**. Two
deeper causes (both fixed + the first PROVEN end-to-end):

**Pattern 11 — the PLACE apply-then-flow arm was a no-op on already-auto-sized subjects.**
`_apply_recipe_strategy`(place)→`_resize_to_core_util` only converts a FIXED `DIE_AREA`→
`CORE_UTILIZATION=30` (the FLW-0024 recovery) and **returns False (no edit) when `CORE_UTILIZATION` is
already set** — the COMMON case on a resumed corpus. So arm B (relief) kept the subject's util,
byte-identical to arm A (control) → every `core_util_relief` trial `inconclusive` forever → the place
class never promoted. Verify on disk: `abA_core_uti/config.mk` and `abB_core_uti/config.mk` BOTH
`CORE_UTILIZATION = 20`, and the two arms' `runs` rows share `orfs_status`+`outcome_score`. FIX:
`_lower_core_util()` — when the subject already auto-sizes, arm B LOWERS the existing util (`*0.6`,
floor 10) so a bigger die diverges from the control; the fixed-die case still converts. PROVEN on
`iscas85_c2670` (PPL-0024 place fail at util=25): arm A aborts at place, arm B (util=15) signs off to a
full GDS → judge `WIN`. NB: the dominant nangate45 "place fail" is **PPL-0024 (IO pins exceed die
perimeter)**; lowering util enlarges the perimeter, so `core_util_relief` empirically recovers
small-pin-gap PPL-0024 (a proper pin-aware die handler is the cleaner fix — open follow-up).

**Pattern 12 — `judge_recipe` counts FROZEN verdict strings, so a since-fixed judge change is not
retroactive.** A trial's `verdict` is written once; when `judge_repeated` was hardened (Pattern 10),
the OLD noise win/loss verdicts stayed in the corpus and `judge_recipe` kept aggregating them → a
nangate45 antenna recipe sat `promoted` on `ab_corpus:3w1l` that the current judge scores `0w0l` (all
four trials re-judge to `inconclusive`: identical `is_success`+`outcome_score`, differ only on
`wall_s`). `judge_recipe` ALSO can't self-heal — a net-zero corpus returns None (status unchanged), so
the fake promotion never reverts. FIX: `knowledge/reconcile_ab_verdicts.py` (now `ab_runner.py reconcile-verdicts`) re-derives each verdict
from its stored `metrics_json` via the CURRENT `judge_repeated` (only for trials with full A/B samples
— never invents from missing data), re-runs `judge_recipe`, and EXPLICITLY reverts a now-evidence-less
`ab_corpus` promotion/demotion to `candidate`. Run it after ANY `judge_repeated` change. On the real
store: 9 noise verdicts→inconclusive, 6 recipes→candidate; real wins (`density_relief` sky130hd
`2w0l`) and real negative evidence (route_relief shadows) preserved; honesty 5/5 green. **Alarm
refinement:** a `promoted` row whose backing `ab_trials.metrics_json` shows IDENTICAL
`is_success`+`outcome_score` across arms is a FAKE promotion even though `ab_trials` and `promoted`
both look populated — re-judge from metrics, never trust the frozen `verdict` column.

**Fmax honesty corollary (2026-06-26).** `_fmax_one` stamped `{period:g}` (6 sig-figs) but verified
the stamp with `abs(cur-period) < 1e-9` against the FULL-precision winner → a correct stamp like
`0.69180034→'0.6918'` failed by 3.5e-7 and returned None (uncounted; ~28% of stamps). FIX:
`_period_stamped()` compares the read-back against the `%g`-formatted value (same "a no-op must be
uncountable, but a real op must COUNT" coin as the fixture≠production trap above).

### Sub-variant: the relief LEVER can't change the outcome — wrong-lever divergence (2026-06-27)

The 2026-06-26 fixes made the PLACE arms APPLY different work (Pattern 11) and reconciled the fake
promotion (Pattern 12), but a resumed nangate45 campaign STILL showed the alarm: `ab_trials` grew to
54, yet **every one of the 39 nangate45 trials was `inconclusive` and `promoted(nangate45)=0`** (the
fake one was correctly reverted at wave 9). The honest truth: *no legitimate nangate45 promotion had
ever happened* — the loop was honest but **stuck**.

**Pattern 13 — `core_util_relief` applies the WRONG LEVER for PPL-0024 (cell-area util vs. pin
perimeter).** The dominant nangate45 place candidate is PPL-0024 on **cell-tiny / pin-huge** designs
(`verilog_ethernet_ip_demux` 1521 IO pins, `DSP_..._dma_controller` 3089). `CORE_UTILIZATION` sizes
the die from **cell area**, but PPL-0024 is a **die-perimeter** constraint — the placer error literally
states the target: `Increase the die perimeter from 631.18um to 851.76um`. Pattern 11's `_lower_core_util`
(one `*0.6` step, floor 10) only nudged the perimeter (ip_demux util 12 → 631um) and **undershot the
demanded 851.76um**, so arm B PPL-0024-aborted *identically* to arm A (control). For `dma_controller`,
reaching 1729.84um would need util ≈ 3.5 — far below the floor. Tell-tale: `ab_trials.metrics_json`
shows BOTH arms `is_success=false, outcome_score=0.333` (place abort) differing only on `wall_s`; the
subjects' `backend/RUN_*/flow.log` carry PPL-0024 with a perimeter `to` value the util step can't reach.
This is *genuine non-divergence of OUTCOME despite divergent CONFIG* — distinct from Pattern 11 (config
was a no-op) — and it was the exact "open follow-up" Pattern 11 flagged.

**FIX (`engineer_loop.py`, 2026-06-27):** size the die to the perimeter the placer DEMANDS, not the
cell area. `_ppl0024_required_perimeter(path)` parses the `to <B>um` target from the run's PPL-0024
message; `_set_explicit_die(path, B)` rewrites `config.mk` to a SQUARE `DIE_AREA`/`CORE_AREA` whose CORE
perimeter ≥ `B × 1.15` (drops `CORE_UTILIZATION`/`DIE_AREA`/`CORE_AREA`; never touches
`PLACE_DENSITY_LB_ADDON`). `_relieve_pin_overflow(entry, perimeter_target=…)` prefers this lever and
falls back to the util step only when no perimeter is parseable (e.g. an FLW-0024 over-pack — preserves
the FLW-0024 behavior). The A/B **arm copy excludes the subject backend**, so arm B can't re-read its own
PPL-0024 message → `plan_arms_for_candidates` stamps the SUBJECT's required perimeter onto each place
arm as `pin_perimeter_target`, and `_apply_recipe_strategy`(place) hits it directly. PROVEN end-to-end
on `verilog_ethernet_ip_demux` (util 12, demands 851.76um): arm A aborts at place (`PPL-0024`), arm B
(`DIE_AREA 0 0 265 265`, core perimeter 980um) runs synth→floorplan→place→cts→route→finish to a final
`6_final.gds` (RC=0) → a DECISIVE `WIN`. Suite 787→797 (new `tests/test_ppl0024_perimeter_die.py`);
honesty 5/5 green. Beyond the A/B win, this also recovers the ~30 production designs escalating as
`pin_overflow_residual`.

**Coverage-gap reset (honesty corollary applied).** With the lever fixed, the 13 pre-fix
`core_util_relief` `inconclusive` trials were *known-contaminated* (arms forced to tie by the broken
lever). Three of four place candidate keys had ≥`AB_INCONCLUSIVE_MAX` inconclusive → `_ab_coverage_gap`
would PERMANENTLY skip re-planning them, leaving the fix dormant. So those 13 trials were deleted (only
`core_util_relief`/`inconclusive`/nangate45; 0 decisive verdicts to lose; the GENUINELY non-divergent
antenna inconclusives were LEFT so they stay correctly gapped). After: all 4 place keys re-plannable,
`ab_trials` 54→41, honesty 5/5 green. The next drain re-validates them with the working lever → the
first legitimate nangate45 promotion. (Per the honesty corollary, `recipe_status` was NOT hand-edited;
the candidates stay `candidate` and the drain decides.)

**Pattern 14 — end-of-drain judging hid finished promotions for ~12h (latency, not correctness).**
Both drain paths (`ab_drain`, `_run_parallel`) ran ALL arm flows via `ex.map(...)` (a barrier) and called
`judge_finished_trials` ONCE afterwards. A drain bundles fast place arms with slow `period_relax` timing
arms (a 2h38m full-signoff reflow) and large-design `rerun_from_stage` arms, so the FIRST legitimate
nangate45 promotion (`core_util_relief/logic/small`, whose place arms finished in minutes) did not surface
until the whole wave-11 drain finished ~12h later — `ab_trials`/`promoted` looked flat for hours while the
loop had already learned the win. **FIX (2026-06-27):** judge INCREMENTALLY — `ex.submit` the arms and call
`judge_finished_trials` as each completes (`as_completed`), plus a final sweep. Safe because
`judge_finished_trials` acts only on pairs whose arms are BOTH terminal (a still-running arm's pair is
skipped) and is idempotent (marks `judged`), and the Ledger is in-memory + lock-guarded so the per-completion
rescans are cheap; worker threads keep private DB conns while the main thread judges (busy_timeout serializes).
Same final state, surfaced the instant each pair completes. Test: `tests/test_incremental_judge.py` (judges
only both-terminal pairs, idempotent, picks up a pair once its last arm finishes). Suite 797→798. NOTE: this
improves promotion *latency/observability within a wave*, NOT wave wall-clock — a wave is still bounded by its
slowest arm; bounding the per-wave arm count (or running the A/B drain concurrently with design processing) is
the open follow-up for wave throughput.

**Pattern 15 — a re-planned A/B arm kept a STALE `judged` flag, so its re-run was never re-judged (the
large-pin class could not promote).** `plan_arms_for_candidates` re-adds an arm entry every drain;
`Ledger.add` does `e.setdefault("state","pending")` so the entry resets to `pending` and the arm RE-RUNS —
but the merge `dict(existing, **e)` KEPT the prior wave's `judged=True` (the re-plan entry has no `judged`
key). `judge_finished_trials` filters `not e.get("judged")`, so a candidate whose arm DIRS survive a prior
wave re-ran every wave but its new verdict was NEVER recorded → it could never promote, AND `_ab_coverage_gap`
(which counts `ab_trials`) was starved so the candidate re-ran forever. This is why the nangate45 large-pin
place class (`logic/medium`, `bus_heavy/medium`) stayed `inconclusive` for many waves even after the perimeter
fix: `ip_demux`'s arm re-ran with the correct perimeter die and SUCCEEDED, but the win was discarded. (A
FRESH-dir candidate like `logic/small` had no prior `judged`, so it judged + promoted normally — which masked
the bug.) **FIX (2026-06-27):** a `pending` event is fresh work → drop any stale `judged`. Applied in BOTH
`Ledger.add` and `__init__`'s JSONL reload (each wave is a fresh process that replays the ledger, so the
invariant must survive the restart). Tests: `tests/test_ledger_replan_judged.py` (re-plan clears `judged`
in-memory AND after reload; a terminal judge-mark still sticks). Suite 798→801. Confirmed empirically: after
a prior-wave `judged=True`, a re-plan left `state=pending judged=True` and the judge's candidate list was
empty — the re-run was invisible. The fix self-heals on the next fresh wave: its reload + re-plan clears
`judged`, the large-pin arm re-runs the perimeter die, and the win is finally recorded → promotion.

### Sub-variant: a crashing `plan_trial` strands every candidate after it (2026-06-28)

A new shape of "`ab_trials` grows but a recipe never promotes" — root cause is **planning-loop
fragility**, not arm non-divergence. `plan_arms_for_candidates` called `ab_runner.plan_trial(...)`
with **no try/except**. `plan_trial` reads state that can race the campaign's concurrent
`heuristics.json` / ingest writes and throw transiently (caught in the wild as an intermittent
`KeyError 'design'`; a clean re-run of the same candidate resolves its subjects fine). One crashing
candidate **aborts the entire planning loop**, so every candidate AFTER it is never planned on that
drain. `synth_memory_relax` — the **LAST of 33 pending candidates** — sat at 0 A/B trials for hours:
any transient crash earlier in the list blocked it every drain, so a perfectly-plannable recipe could
never reach a verdict or promote. Tell-tale: a candidate is in `recipe_lifecycle.pending_candidates`,
`plan_trial` succeeds when run standalone, yet there are **zero** arm entries for it in the ledger and
**no** planning log line (success appends silently; the skip paths log — so *neither* trace means the
loop never reached it). **Fix:** wrap `plan_trial` in try/except — a crashing candidate is skipped +
logged (stays `candidate`, re-plans next drain), never aborts the loop, never demotes. Tests:
`tests/test_plan_arms_isolation.py` (a crasher as the FIRST of two candidates no longer strands the
second). Suite 826→827.

**Related follow-up (symptom over-coarse, iter-6):** the synth-abort symptom is keyed only by
`{check=orfs_stage, class=synth}`, so it conflates synth **memcap** / **timeout** / **missing-header**
aborts under one `symptom_id`. `plan_trial` then resolves a timeout subject (`verilog_ethernet_arp`)
for the memcap recipe `synth_memory_relax`, whose cap-raise can't help a timeout → both arms time out
(7200s each) → inconclusive, wasting ~8h of arm flows per drain. The in-loop *application* is correctly
signature-gated (`_is_synth_memory_cap`), so this is an A/B-efficiency bug, not a correctness one. The
fix is a memcap-specific symptom predicate (or a recipe-applicability subject filter) — careful
symptom-keying work, deferred to do deliberately.

### Sub-variant: `worker_exc:<Type>` — an undiagnosable worker crash (2026-06-29)

The parallel worker guard `_safe_process` catches *any* exception from a design's `_drain_arm`
so one crash never aborts the batch (the right behavior) — but it recorded **only the exception
TYPE** as the escalation reason (`reason=worker_exc:ValueError`), swallowing the message and
traceback. Caught in the wild: four designs (`wbscope_wishbone`/`_avalon`/`_axil`,
`zipcpu_wbdmac`) escalated `worker_exc:ValueError` during wave 17. By the time the crash was
investigated, their on-disk state had moved on (a synth abort whose `flow.log` was mid-write at the
instant of the crash briefly looked FF-expandable → entered the memcap recovery+recursion path →
threw there), so the root-cause line was **unfindable** — the loop can't learn from a failure it
can't see, and the operator can't triage a bare type name. This is the diagnosability twin of the
`plan_trial` crash above. **Fix:** `_safe_process` now prints the full traceback to the wave log
(stderr) and stamps the one-line `Type: message` onto the ledger `note`; the `reason` key stays
`worker_exc:<Type>` for stable triage/honesty bucketing. The next occurrence carries its own
root-cause line. Test: `tests/test_safe_process_records_traceback.py`. (NOTE: these `worker_exc`
escalations are *ledger-only* — `_safe_process` has no knowledge-DB conn — so they don't fabricate a
`failure_event`; honesty parity is unaffected. A genuinely synth-aborted design like these re-queues
to its honest `synth_memory_residual` reason once its log is fully written.)

- **Root-cause instance (2026-06-30): a `worker_exc:ValueError` that was a LATENT escalation-reason gap.**
  Two asap7 designs escalated `worker_exc:ValueError` with note `ValueError: unknown escalation reason:
  synth_memory_residual`. `process_one` legitimately emits `reason="synth_memory_residual"`
  (engineer_loop.py:912, the synth_memory_relax residual added 2026-06-28), but `synth_memory_residual`
  was **missing from `escalations.REASONS`** — so `open_escalation` raised `ValueError` (escalations.py:73),
  the worker crashed, and the design was mislabeled `worker_exc:ValueError`, burying the honest reason.
  This is the EXACT "emitted by process_one but never registered here" latent-crash class the
  `place_density_residual` comment in `escalations.py` already flagged (2026-06-23). **Fix:** add
  `synth_memory_residual` to `escalations.REASONS`. Test: `tests/test_escalations.py::
  test_synth_memory_residual_is_valid_reason`. Reconcile: re-queued the 2 mislabeled designs (fixed code
  escalates them honestly). **Lesson:** any new escalation reason the loop emits MUST be added to
  `escalations.REASONS` in the SAME change, or it is a latent worker crash that fires only when that
  residual occurs.
- **RECURRENCE (2026-07-02 sky130 round) — the SAME class, 2 more unregistered reasons, now guarded
  systemically.** During waves 3-4, **24 real designs** (PYGMY_V32I/RISC_V/RISCV_Tang_E203/I2SRV32/
  MS_DMAC) escalated `worker_exc:ValueError` with note `ValueError: unknown escalation reason:
  incomplete_missing_header`. `process_one` emits `reason="incomplete_missing_header"`
  (engineer_loop.py:1036, `_is_synth_missing_header`) AND `reason="synth_timeout"` (engineer_loop.py:1043,
  `_is_synth_timeout`) — **both missing from `escalations.REASONS`** (the 5th and 6th time this class
  recurred: place_density → pin_overflow → synth_memory → pdn_strap → these two). The `ValueError` at
  `escalations.py` fires at the whitelist check *before* the dedup check, so it crashed even for designs
  that ALREADY had an open `incomplete_missing_header` escalation (reconcile confirmed: the 24 were
  dedup no-ops, 52→52 — so knowledge was NOT blind; the harm was crashed workers wasting wave slots +
  cosmetic ledger `worker_exc` mislabel). **Fix:** register both reasons in `escalations.REASONS`.
  **Systemic guard (stops the 7th recurrence):** `tests/test_escalations.py::
  test_all_loop_emitted_reasons_are_registered` parses `engineer_loop.py` for every `reason = "<literal>"`
  and asserts each is in `escalations.REASONS` — so a new reason can never ship unregistered again.
  Plus per-reason tests `test_incomplete_missing_header_is_valid_reason` / `test_synth_timeout_is_valid_reason`.
  Honesty stayed 5/5 throughout (the crash is operational, not a fabricated verdict).

### Sub-variant: A/B re-plan resets clean arms before judge → candidate never promotes (2026-06-30)

- **Symptom:** a fresh-platform round (asap7) LEARNS candidates (`recipe_status` candidate>0) and the
  arms RUN (arm runs ingested), but `ab_trials` for that platform stays **0** and `promoted` never grows
  — `ab_trials grows but promoted flat` is the older alarm; this is subtler: **`ab_trials` never even
  appears.** A single arm's ledger history shows it cycling `pending → clean → re-plan pending → clean`
  within one drain, `judged=None`, never judged.
- **Root cause:** `plan_arms_for_candidates` called `led.add(arm_entry)` UNCONDITIONALLY for every arm of
  every pending candidate. `arm_entry` carries no `state`, so `Ledger.add` defaults it to `pending` and
  drops `judged`. Each plan cycle (run's `_run_parallel` AND `ab_drain`, per wave) therefore RESET arms
  that had already reached a terminal state but were still awaiting their pair's verdict.
  `judge_finished_trials` only records a verdict when BOTH arms of a `(base, strategy)` pair are terminal
  (`clean`/`escalated`/`abandoned`) + unjudged at the SAME judge moment — so resetting one arm per cycle
  means a complete A+B pair is never simultaneously terminal → the trial never judges → the candidate
  loops forever (re-plan → run → clean → re-plan), burning arm flows, never promoting. nangate45 happened
  to judge within a single drain window (arms fast/co-located), so it promoted; asap7's slower/cross-phase
  arms got reset first — a latent bug the asap7 round exposed.
- **Fix (2026-06-30):** `_arm_awaiting_judge(led, design)` returns True for an existing arm that is
  terminal but NOT judged; `plan_arms_for_candidates` SKIPS the `led.add` for such an arm (leaves it for
  the judge). A *judged* terminal arm is still re-planned normally (a fresh trial — the 2026-06-27
  Pattern-15 re-judge path is unchanged). Added `Ledger.get`. Also extended the arm copytree to exclude
  `lvs/drc/rcx` stage dirs (not just `reports/`) so a DRC-only arm fix can't inherit the subject's stale
  `lvs/6_lvs.lvsdb` and record `lvs=clean` for asap7 (the arm lvs-residual). Tests:
  `tests/test_ab_replan_preserves_terminal.py`. Suite 860→865.
- **Skill-level alarm:** for a platform with `candidate>0` + arm runs ingested but `ab_trials=0` that
  PERSISTS across waves, check whether arm ledger entries cycle through `clean` back to `pending` — a
  re-plan is resetting them before the judge fires.

### Sub-variant: A/B arm runs the WRONG platform's DRC deck (stale config.mk) → hangs the wave (2026-07-01)

- **Symptom:** a wave stalls for hours with a few designs stuck in `fixing` at **~0 % CPU** (not the 99 %
  legit-extraction case), the wave log frozen. `ps` shows the stuck job is an A/B arm
  (`<subj>_abA_<strat>_<r>`) whose `fix_signoff.sh <arm> nangate45` was called, yet ORFS built into
  `results/asap7/…` and KLayout is running `platforms/asap7/drc/asap7.lydrc` — the arm's
  `constraints/config.mk` says `export PLATFORM = asap7` while its `ab_key.platform` is `nangate45`.
- **Root cause:** `plan_arms_for_candidates` materializes an arm with `shutil.copytree(subject → arm)` and
  repoints only `SDC_FILE` (`_localize_arm_sdc`) — it inherits the SUBJECT's `config.mk` **PLATFORM
  verbatim**. **PLATFORM is ORFS ground truth** (`run_orfs.sh`/`run_drc.sh` build the design and pick the
  DRC deck from `config.mk`, NOT the arg they are passed), so when the subject (or a reused arm-scratch
  dir) carries a *prior round's* platform, the arm runs the wrong, heavy `asap7.lydrc` deck on a nangate45
  GDS and **hangs**, tail-blocking all wave workers. Exposed in the 2026-07-01 sky130 round: the shared
  knowledge store still held nangate45 `antenna_diode_repair` candidates whose msrv32 subjects were
  asap7-config arm-scratch dirs left from the asap7 round (`setup_rtl_designs.py --force` only re-points
  real `rtl_designs/`, not arm-scratch). 4 arms hung 32 min @ 0 % CPU.
- **Fix (2026-07-01):** new `_localize_arm_platform(dst, ab_key.platform)` (regex repoint mirroring
  `_localize_arm_sdc`), called in `plan_arms_for_candidates` on **every** plan and guarded on `dst.is_dir()`
  so it corrects BOTH fresh arms and already-materialized stale ones (idempotent). Existing pending arm
  dirs were reconciled in place with the same helper. Tests: `tests/test_loop_timing_place_ab.py`
  (`_localize_arm_platform` unit) + `tests/test_plan_arms_isolation.py`
  (`test_plan_arms_sets_arm_platform_from_ab_key` — plans from a stale-asap7 subject, asserts arm PLATFORM
  == ab_key). Suite 885→888.
- **Skill-level alarm:** a wave design stuck in `fixing` at ~0 % CPU for >10 min whose `ps` cmd names a
  DRC deck (`*.lydrc`) from a DIFFERENT platform than the design's `ab_key`/ledger platform — kill it
  (log it, no silent caps) and check the arm's `config.mk` PLATFORM vs its `ab_key.platform`.

### Detecting the gap directly: `tools/check_db_integrity.py` (both-DBs cross-check, 2026-06-30)

Every closure failure above is ultimately *the two memory DBs disagreeing about what happened* —
`knowledge.sqlite` (what RESULTED) and `journal.sqlite` (what was DONE) drifting out of step.
`honesty.py` polices only the knowledge side (it is deliberately journal-free so it runs over a
fresh clone in CI), so the *cross-DB* drift was invisible to the gate. `tools/check_db_integrity.py`
closes that hole: it **imports `honesty.run_all`** (so the knowledge verdict can never drift from
CI) and adds the journal/cross-DB invariants on top, one PASS/WARN/ALARM line per code:

- **`H:*` (ALARM)** — the five knowledge honesty gates, over the whole committed store.
- **`J1`/`J2` (ALARM)** — journal writer alive; and no project has a knowledge run + journal actions
  yet **zero** back-filled `run_id` (ingest must link the ledger to the result).
- **`L1`/`L2`/`L3` (WARN)** — every `ab_trials` symptom has an `ab_launch` action, every `promoted`
  recipe a `promote` action, every open symptom-escalation an `escalate` action: i.e. each knowledge
  MOVE left a journal trace. Directional ⊇ (the ledger may hold more — re-launches/re-promotions).
- **`J4` (WARN)** — no journal `run_id` dangles (resolves to a real `knowledge.runs` row).
- **`K3` (WARN)** — per-platform `ab_trials`>0 but `promoted`=0 with ≥3 inconclusive (the 2026-06-24
  identical-arms stall).

**Severity contract:** knowledge is the source of truth + sole learner input, so its dishonesty is an
ALARM (exit 1, stop and fix). The journal is best-effort/lossy/gitignored, so a move it failed to
record is a WARN (exit 0, a lead) — never a fabricated lesson. Run it after every wave:
`python3 tools/check_db_integrity.py --platform nangate45` (honesty gates stay global; the trend +
correspondence checks scope to the platform). The `/r2g-debug` command wires it into Step 0/2/4.
Known-benign WARNs on the live store at first wiring: one `sky130hd density_relief` promotion that
**predates** the 2026-06-17 promote/ab_launch journaling (L1/L2), and two nangate45 journal `run_id`s
whose projects were wiped in the 2026-06-19 `design_cases` purge (J4). Test:
`tests/test_check_db_integrity.py`.

### Sub-variant: ab-drain not platform-scoped stalls a focused round (2026-07-01, FINDING #3, FIXED)

`engineer_loop run` scopes the FLOW phase per-ledger-entry (`entry["platform"]`), but the A/B
phase (`plan_arms_for_candidates`) iterated **every** pending candidate in the shared
`recipe_status` regardless of platform. On a **sky130 round** the per-wave `ab-drain` therefore
planned **asap7** candidate arms (and nangate45 ones). asap7 arms run `asap7.lydrc` KLayout DRC —
**slow** *and* structurally unable to promote (asap7 is not DRC-clean-able, footnote ¹ of the
signoff contract) — so they wedged wave 1 for **6h+** (host idle, the next wave's larger pool never
applied, only benign arm escalations accrued). Symptom: `pgrep` shows the driver running
`asap7/<design>` `asap7.lydrc` arms while the sky130 `run` phase is long done; `waves.log` shows no
new `WAVE_START`; pending flat while escalated climbs with only arm/`unvalidatable`/`route` reasons.
This is the campaign-throughput analogue of the tick-1 temp-ledger blow-up (a global A/B planner is
too broad for a single-platform round). **Fix:** `_ledger_round_platform(led)` derives the round's
platform from the ledger's base (non-`ab_arm`) entries (dominant, ≥60% majority else `None` =
fail-open), and `plan_arms_for_candidates` **skips off-platform candidates** — leaving them
`candidate` (validated when a round on THEIR platform runs), never demoting/escalating them. One
change covers all callers (`run`/`ab_drain`/`ab_enqueue`) because each already receives `led`; a
one-line `[loop] A/B platform-scope … skipped N off-platform candidate(s)` summary keeps it honest
(no silent cap). Tests: `tests/test_ab_platform_scope.py` (5). Aligns ab-drain with the "one
platform per round" hard rule. (Belt-and-suspenders on restart: `DRC_TIMEOUT` bounds any residual
slow arm.)

### Sub-variant: GHOST A/B arms — Tier-1 subjects from wiped rounds starve the candidate (2026-07-03)

- **Symptom:** `place_arm_incomplete` (or `route_arm_incomplete`) escalations pile up for arm
  designs whose dirs **do not exist under `design_cases/`** — names like
  `<design>__sky130hd_abA_core_uti_0` (note the `__<platform>` infix inherited from a PRIOR
  round's clone-dir naming). `ab_trials` stays flat across the wave while those arms re-escalate,
  and the candidate (`core_util_relief` logic/medium + logic/small on the 2026-07 sky130 round)
  never validates. Found wave 7: 6 ghost arms (FIR_ex4LS16 ×4, wb2axip_axisgdma ×2), subjects =
  June-17-era `<design>__sky130hd` clone dirs wiped by the 2026-07-02 clean-slate reset.
- **Root cause (two compounding holes):**
  1. `ab_runner.plan_trial` **Tier 1** (`run_violations` exhibitors) was the ONLY subject tier
     without the on-disk `os.path.isdir` filter (`_symptom_designs`/Tier 2 and
     `_resolve_evidence`/Tier 3 both have it). `runs`/`run_violations` are immutable history —
     correct per the honesty invariants — so a wiped round leaves exhibitor rows whose
     `project_path` is gone, and **cheapest-first ordering ranks the tiny wiped clones AHEAD of
     real dirs**, crowding genuine subjects out of the trial.
  2. `plan_arms_for_candidates`' copytree guard (`if src.is_dir() and not dst.exists()`) silently
     no-ops for a missing subject **but the arm entry was still appended to the ledger** — a
     ghost arm that flows against a nonexistent project, produces no backend, and escalates
     `*_arm_incomplete` on every drain, forever (the judge can never see a terminal pair).
- **Fix (2026-07-03, branch r2g-debug/sky130-round):** (1) Tier 1's `_q` now applies the same
  `isdir` filter as Tiers 2/3 — all ghosts filtered ⇒ the tier honestly falls through (or
  plan_trial returns None ⇒ the documented `unvalidatable_insufficient_subjects` escalation);
  (2) defense-in-depth: `plan_arms_for_candidates` skips (with a `[loop] A/B subject dir missing
  on disk` log, no silent cap) any arm whose subject dir AND arm dir are both absent. Tests:
  `test_ab_fixhist_subjects.py::test_tier1_skips_wiped_subject_dirs` +
  `::test_tier1_all_ghosts_is_honestly_unmatched`,
  `test_plan_arms_isolation.py::test_plan_arms_skips_missing_subject_dir`. Existing fixtures that
  seeded `/p/d<i>` fake paths were updated to real tmp dirs (the filter is now part of the
  plan_trial contract).
- **Skill-level alarm:** any `*_arm_incomplete` escalation whose design dir is absent from
  `design_cases/` is a ghost arm — check `ls design_cases/ | grep _ab` against the ledger's
  `ab_arm` entries. Historical ghost-arm ledger rows are terminal escalated artifacts (benign
  history); the candidate re-plans with real subjects on the next drain after the fix.

### Sub-variant: judge blind to the target symptom — 85% inconclusive, no reason recorded (2026-07-04, judge v2)

- **Symptom:** `ab_trials` grows but is dominated by `verdict='inconclusive'` (live store: 193 of
  228 = 85%), whole strategy classes are 0-decisive forever (`antenna_diode_repair` 0-in-93,
  `pdn_die_floor` 0-in-12, `rerun_from_stage` 0-in-14, `beol_only_drc` 0-in-5), 38 candidates sit
  capped dead by `AB_INCONCLUSIVE_MAX` with zero decisive verdicts, and `metrics_json` holds only
  tied samples — nothing says WHY a trial concluded nothing. Every capped trial burned a full
  flow+fix ×2×k before teaching zero bits.
- **Root cause (metric granularity — the timing/synth lesson, never generalized):** DRC/LVS signoff
  arms were judged on the whole-run `knowledge_db.is_success`. A signoff subject usually carries
  MORE THAN ONE residual, so arm B clears its target class (e.g. `METAL5_ANTENNA` → 0) while an
  UNRELATED residual (density, LVS) keeps `is_success` false in BOTH arms → `judge_repeated` sees a
  0-0 success tie → inconclusive, structurally, forever. Timing arms hit the identical wall in
  2026-06-24 (both arms reach GDS) and were fixed by judging on `wns_ns`; synth arms in 2026-06-28
  (stage clearance); DRC/LVS — the majority class — never got the fix.
- **Fix (2026-07-04, judge v2):** (1) `judge_finished_trials` resolves the candidate's symptom to a
  target (`_symptom_target`) and `_arm_metric(target=...)` judges a DRC arm on the TARGET class
  count reaching 0 on a definitively-run DRC (`_drc_symptom_cleared`, quoted-class-normalized;
  stuck/unknown never demonstrates a clear), an LVS arm on `lvs_status='clean'`. Verified against
  the live store: 44 of 384 real (arm × antenna-symptom) samples flip success under the v2 metric —
  exactly the separations the old judge could not see. (2) `ab_runner.judge_repeated_ex` returns a
  (verdict, **reason**) pair — `both_arms_never_succeed`, `success_tie_cost_within_noise`,
  `success_tie_insufficient_repeats`, `arm_no_samples`, `cost_tiebreak`, `success_lcb_delta` — and
  `record_trial` metrics carry `judge_version: 2` + `reason` + `target`, so the inconclusive corpus
  is queryable. (3) `_ab_coverage_gap` counts ONLY judge-v2 inconclusives toward the re-plan cap:
  pre-v2 verdicts were blind to the symptom under test, so they no longer permanently bar the 38
  capped candidates (decisive verdicts count from any era — a win still unblocks). (4) Non-divergent
  strategies (`lvs_resolve_unknown`) are refused at ENQUEUE (`recipe_lifecycle.diff_and_enqueue`/
  `enqueue_candidate`) and legacy rows are healed to the non-terminal `parked` status
  (`park_nondivergent`, top of every drain) instead of being re-skipped+re-escalated forever.
  Tests: `test_judge_v2_symptom_target.py`.
- **Skill-level alarm:** `SELECT strategy, COUNT(*) FROM ab_trials WHERE verdict='inconclusive'
  GROUP BY 1` showing a strategy with high counts and ZERO decisive rows = the judge cannot see that
  strategy's effect — check what `metrics_json.reason` says before burning more arms on it.

### Sub-variant: negative evidence written but never consumed — the same dead fix re-tried 112× (2026-07-04)

- **Symptom:** `fix_trajectories` accumulates the same (design, symptom, strategy) abandonment over
  and over (live store: `('test','timing','utilization_reduce')` abandoned 112×, 186 triples
  re-abandoned ≥3×; 2376 'abandoned' rows total of which 1957 tried NOTHING — path all
  `strategy:'none'`). Fix sessions on a design repeat the exact strategies that already failed there
  in prior sessions; violation classes appear as raw quoted KLayout text (`"'m3.2'"`, a 100-char
  LISD rule sentence, `"'  '"`), each spawning a single-use symptom bucket that can never pool.
- **Root cause (three holes):** (1) the ranker's Beta down-rank is the ONLY negative-evidence
  consumer, and it is cross-run-memoryless per design — nothing excluded a strategy that terminally
  failed on THIS design before, so the fixer's catalog walk re-applied it every session; (2) A/B
  demotion (`shadow`) only stripped the strategy's learned boost from the INDEXED recipe path
  (`filter_promoted`) — via the static catalog / pooled prior / fallback paths a demoted recipe
  could still sort first and auto-apply; (3) write-side noise: give-up-before-trying episodes were
  recorded as `abandoned` (bogus negative evidence), and `extract_drc` stored KLayout `<category>`
  text verbatim (quotes and all), fragmenting the symptom index.
- **Fix (2026-07-04):** apply-side — `_annotate_live_gates` marks strategies with
  ≥ `R2G_FIX_DEAD_AFTER` (default 2) terminal failures and zero clears on this design+check as
  `dead_here`, and `_live_auto_strategy` skips `dead_here` + `lifecycle_status='shadow'` strategies
  in blind live runs (`R2G_FIX_RETRY_DEAD=1` opt-out; `--rank-first`/A/B arm B bypasses all gates by
  design — the harness must be able to force the strategy under test). Write-side —
  `symptom.normalize_class` at every entry point (extract_drc parse, `canonical_signature`),
  `_build_trajectory` re-keys legacy quoted-class signatures on rebuild (history heals into the
  pooled buckets), and a none-only episode is now outcome `not_attempted`, freeing `abandoned` to
  mean "tried real strategies, none worked". Tests: `test_negative_evidence_gates.py`.
- **Why not blacklist globally?** A strategy that fails on one design can win on another (the whole
  premise of symptom-pooled learning) — so the hard gate is DESIGN-LOCAL and the global signal stays
  a soft Beta down-rank + the A/B lifecycle. The fix philosophy "down-ranked, never zeroed" survives;
  what changed is that a HUMAN-obvious stop rule ("don't re-run the exact fix that failed here
  twice") now exists at the only place it can act — auto-apply time.

#### Nested finding: incremental judging FRAGMENTS k-repeat trials (2026-07-04, found via reason codes)

- **Symptom:** v2 trials record `repeats: {A:2, B:1}` or `{A:1, B:1}` with
  `reason=success_tie_insufficient_repeats`, while the LEDGER shows the full k=2 arm entries
  planned per side — and a straggler repeat can sit terminal+unjudged forever (live:
  `koios_tdarknet` route_relief arm-B r1). This was invisible before reason codes existed —
  the first bug the judge-v2 forensics surfaced, minutes after going live.
- **Root cause:** `judge_finished_trials` judged whatever repeat subset was terminal at each
  pass (the 2026-06-27 incremental-judge design waited for both ARMS, not all REPEATS). A k=2
  trial fragments into a 2-vs-1 (cost tiebreak needs >=2 per side → success-ties land
  inconclusive) or two 1-vs-1 fragments (LCB over one sample = no variance protection), and
  once the early fragment marks its entries judged, the late repeat is a one-sided pair the
  `{A,B}` completeness check skips every drain — stranded compute.
- **Fix:** cohort-wait — a pair is judged only when EVERY repeat of both arms is terminal;
  zombie non-terminal-but-judged entries (historical fragments) don't block. Pre-existing
  stranded orphans stay benign history (the candidate re-plans fresh arms, Pattern 15).
  Tests: `test_incremental_judge.py::test_waits_for_full_repeat_cohort_before_judging` +
  `::test_zombie_judged_nonterminal_entry_does_not_block_cohort`.
- **Skill-level alarm:** any v2 trial whose `metrics_json.repeats` differs between arms, or
  differs from `R2G_AB_REPEATS`, is a fragmentation lead.

**Pattern 16 — post-ingest autolearn on a SANDBOX db clobbered the SHIPPED heuristics.json
(2026-07-09, exposed by rtl-acquire's flow_scope ingest test).** `ingest_run.py` ends with an
env-gated autolearn (`R2G_FIX_AUTOLEARN=1` default) that calls `fix_log_manager.manage(args.db)`
— but `manage()` defaulted its learner output to `DEFAULT_KNOWLEDGE_DIR/heuristics.json`
REGARDLESS of which db was ingested. Any ingest into a non-default db (a pytest tmpdir, an
rtl-acquire scratch corpus via `R2G_KNOWLEDGE_DB`, an A/B sandbox) therefore full-rewrote the
committed heuristics.json from that other db's tiny corpus — tell-tale: `source_run_count` drops
to ~1 and `families` collapses to almost nothing while the committed knowledge.sqlite still has
thousands of runs. Same class for `mine_rules` → `failure_candidates.json` (gitignored, so lower
stakes, but it silently replaced the operator's live review queue). Fix: `manage()` keys BOTH
outputs to the db they were derived from — the default db keeps the shipped paths; any other db
writes `heuristics.json`/`failure_candidates.json` NEXT TO itself. Guard:
`tests/test_fix_log_manager_sandbox_outputs.py` (shipped bytes must be identical after a
sandbox-db manage()). If you find a gutted heuristics.json, restore with
`git checkout -- knowledge/heuristics.json` and re-run `learn_heuristics.py` off the committed db.

### 2026-07-16 agent-logic issue-report audit (failure-patterns #50 — 9 issues)

The second external adversarial audit (`docs/superpowers/plans/2026-07-16-agent-logic-issue-report.md`,
probed at cb50537) targeted the seams the #49 fixes left: evidence OWNERSHIP (not just existence),
aggregate determinism, guard completeness, and apply/judge revalidation. All 9 issues were confirmed
live and fixed TDD. The committed store gained one nullable column (`recipe_status.status_version`)
with **0 verdicts moved** (old-vs-new evidence counting verified identical on all 114 committed trial
keys; the one tied corpus — `af17c0ba… sky130hd bus_heavy/medium core_util_relief`, 1w1l — only
re-queues as `candidate` if/when a future trial fires for that key).

- **Issue 1 — real-but-FOREIGN runs certified a win.** #49's `_runs_exist` checked only that the two
  run_ids resolve to rows, so any two ingested runs (other projects, other platforms) passed as A/B
  evidence. **Fix:** `ab_runner._arms_owned` — a database-derived OWNERSHIP predicate: each run's
  project basename must parse `_ab[AB]_` with the CORRECT role in the correct column, both arms share
  one base subject + trial tail, the tail carries THIS trial's strat8, platforms match the key.
  `judge_recipe` re-derives a stamped-True `provenance_complete` locally before counting (merged
  bundles can't self-certify); absent-key legacy rows stay grandfathered. Guards:
  `test_ab_runner.py::test_{foreign_real_runs_cannot_certify_a_win,swapped_arm_roles_are_not_owned,
  cross_subject_and_cross_strategy_pairs_are_not_owned,cross_platform_arm_runs_are_not_owned,
  stamped_true_provenance_is_reverified_at_judge}`.
- **Issue 2 — tied corpus was ORDER-dependent.** `judge_recipe` returned None on a tie, so the FIRST
  decisive row's transition survived: win-then-loss stayed `promoted`, loss-then-win stayed `shadow` —
  opposite lifecycle states from the same net corpus. **Fix:** tied decisive evidence (wins==losses>0)
  deterministically re-queues as `candidate` (`recipe_lifecycle.revalidate`); wins==losses==0 stays a
  no-op (inconclusives still carry no information). Forward-only — no retroactive re-judge of the
  committed store (24 `merge:*` rows are intentionally inert exporter evidence). Guards:
  `test_ab_causal_guards.py::test_{tied_corpus_is_order_independent,tie_neutralizes_transient_promotion}`.
- **Issue 3 — causal isolation checked only a 4-knob whitelist.** `_arm_spec_mismatch` compared
  CLOCK_PERIOD/DIE_AREA/CORE_AREA + SDC period, so an unrelated knob smuggled into one arm
  (PLACE_DENSITY_LB_ADDON, ABC_AREA…) was credited to the recipe. **Fix:** `_arm_baseline_divergence`
  — the arms' HUMAN-AUTHORED baseline region (config.mk minus the fix auto-block) must be
  knob-identical (arm-local values like each arm's own SDC_FILE path are normalized); every
  legitimate edit lands INSIDE the block, so any baseline delta is contamination → decisive verdict
  vetoed to `inconclusive` in both directions. Same signoff-check scoping as the spec guard
  (place/synth write bare exports by design; timing arms edit the SDC).
- **Issue 4 — target-symptom win hid cross-check regressions.** The only regression veto compared DRC
  classes, so a recipe clearing its DRC target while flipping LVS clean→fail or timing clean→severe
  still promoted. **Fix:** `_ab_global_regression` — a per-check severity partial order over the arms'
  ingested outcome vector (LVS clean→{fail,crash,mismatch,incomplete,stale}, timing
  {clean,minor}→{moderate,severe,unconstrained — losing the constraint IS a disabled check},
  orfs pass→fail, DRC {clean,clean_beol}→{fail,failed,stuck}, and a check A definitively ran going
  MISSING in B). Win-only veto; no materiality escape hatch outside DRC; both arms'
  `_global_repair_state` vectors ride `metrics_json.global_state`. Never folded into outcome_score
  (invariant H4). Guards: `test_ab_causal_guards.py::test_global_regression_*`.
- **Issue 6 — lifecycle never revalidated at apply/judge (TOCTOU).** `--next` gated on lifecycle but
  the separate `--apply` invocation looked the strategy up by id and wrote directly — a recipe demoted
  between the two processes still applied; and the A/B freshness guard compared only `generation`,
  which `recipe_lifecycle._set` never bumps, so a demotion landing mid-trial was invisible and a stale
  decisive trial re-promoted over the withdrawal. **Fix:** (a) `--apply` re-reads the CURRENT lifecycle
  in-process (rc=5 `lifecycle_blocked`; fail-closed when the store is unreadable; `--rank-first
  <same-id>` bypasses — the A/B arm-B path, now passed by fix_signoff.sh and the route-arm apply);
  (b) `recipe_status.status_version` bumps on EVERY `_set` transition, is stamped on each arm at plan
  time, and the judge CANCELS a trial whose version moved (also closes the NULL-generation hole of
  `enqueue_candidate` rows). Guards: `test_diagnose_signoff_fix.py::test_apply_{refuses_lifecycle_
  blocked_strategy,rank_first_bypasses_lifecycle_gate,fails_closed_when_lifecycle_unreadable}`.
- **Issue 7 — arm identity collided across symptoms.** Arm dirs/ledger keys were
  `subject+arm+strat8+repeat`, so two candidates sharing subject+strategy but differing in symptom
  produced IDENTICAL arm names — the second plan merged onto the first's ledger entries, overwriting
  `ab_key` (evidence attributed to the wrong symptom; the first experiment silently lost); the judge's
  `(base, strategy)` grouping was a second collision surface. **Fix:** arm dirs carry a per-trial
  UPPERCASE-hex hash of the FULL recipe key (`_ab{arm}_{strat8}{H6}_{r}`; uppercase so a digest can
  never embed a spurious `_ab` parse token), and the judge groups cohorts/pairs by `(base, full
  ab_key)` with a legacy fallback. Guard: `test_plan_arms_isolation.py::test_two_symptoms_same_
  subject_strategy_get_distinct_arms`.
- **Issue 8 — regression auto-demotion crossed domains.** The #49 sweep called
  `auto_demote_on_regression`, which filtered `fix_events` by symptom+strategy only — two asap7/cpu
  failures demoted the nangate45/crypto recipe they never touched. **Fix:** evidence is EXACT-DOMAIN
  scoped: provenance `'live'` only (a backfilled import is not a live regression), the key's OWN
  platform, and the key's OWN design_class (joined via the event project's latest ingested run).
  Cross-domain failures are transfer signal, never demotion grounds. Guards:
  `test_ab_causal_guards.py::test_auto_demote_ignores_{cross_platform,backfilled}_regressions`.
- **Issue 9 — apply reported success with ZERO effect.** `--apply` returned rc=0 after merely
  identifying a strategy: a missing `constraint.sdc` (or an SDC whose period the regex couldn't
  rewrite) silently skipped the edit, fix_signoff reran a full backend stage on a no-op, and the
  unchanged failure was recorded AGAINST a recipe that was never applied. **Fix:** verified-effect
  apply — preconditions checked BEFORE any write (missing/unrewritable SDC → rc=4
  `precondition_failed`, nothing written), every declared edit re-read and verified after writing
  (`no_effect` → rc=4), literal `create_clock -period N` SDCs (harvested RTL) now rewritable, and a
  by-design no-edit strategy (`lvs_resolve_unknown`) reports an explicit `applied_no_op`. fix_signoff
  aborts the iteration on rc≠0 — no more zero-effect reflows. Guards:
  `test_diagnose_signoff_fix.py::test_apply_period_relax_*` + `test_apply_no_edit_strategy_is_explicit_no_op`.

(Issue 5 of this report — DEF/signoff-gate provenance loss in def-graph — is documented with the
2026-07-16 full-pipeline entry below.)

### 2026-07-19 post-consolidation audit (failure-patterns #52 — 20 claims audited, 9 fixed)

`docs/superpowers/plans/2026-07-19-post-consolidation-agent-and-full-pipeline-audit.md`, probed at
136bb7d in a foreign checkout, claimed 20 defects (14 P0 / 6 P1) + 4 policy decisions. **Unlike the #50/#51
round, the claims did NOT all hold** — audit each one against live code before fixing:

- **Not reproducible here.** The report's "Production Store Integrity Snapshot" listed 4 WARNs including
  `24/24 A/B-trial symptoms had no ab_launch action`, `6/6 promoted without promote`, `27/27 escalations
  without escalate`. On this checkout L1/L2/L3 all **PASS** (`0 alarm, 1 warn, 15 pass`). `journal.sqlite`
  is **gitignored and machine-local**, so journal-coverage findings from a foreign checkout say nothing
  about this store — never port a journal-side verdict across machines.
- **Deliberate design, not a defect.** P0-R3 ("legacy unverifiable A/B wins can still promote"): an ABSENT
  `provenance_complete` is grandfathered-countable ON PURPOSE (`ab_runner.judge_recipe._verifiable`,
  documented at #48/P0-1). 77 legacy decisive trials carry NULL run_ids; rejecting them would move 21
  promoted keys, violating the standing **0-flips** discipline. This is the report's own "unresolved policy
  decision" class and needs an operator ruling, not a silent code change.
- **Missing evidence.** The companion doc the report cites for P0-N7/P1-N6
  (`2026-07-19-real-instance-fix-value-assessment.md`) and its harness
  (`tools/audit_post_consolidation_2026_07_19.py`) were never delivered to this repo.

Nine confirmed defects, all fixed TDD (each test proven red pre-fix, green post-fix; committed
`knowledge.sqlite` verdicts unchanged):

- **P0-R1 — an explicit ORFS failure could be learnable SUCCESS.** `knowledge_db.is_success`'s relaxed
  path required a positive signoff signal but never vetoed `orfs_status='fail'`. The relaxed path exists
  to rescue runs whose backend record is merely INCOMPLETE (`partial`/`unknown`); `fail` is not
  incomplete. Blast radius on the real store: **exactly 1 row** — `rv32i_csr/nangate45`, whose
  `stage_times_json` is a single 10-second `synth` at status 2 yet carries `drc=clean lvs=clean
  rcx=complete` (stale fields from an earlier flow in the same project dir — ingest reads `reports/` per
  PROJECT, not per run). Fixing it corrects a lie rather than moving a verdict.
- **P0-N1 — parking bypassed the lifecycle version.** `recipe_lifecycle.park_nondivergent` did a raw bulk
  `UPDATE` instead of `_set()`, so `status_version` never moved. That version IS the A/B plan→judge
  staleness handshake (#50 issue 6), so a trial planned while the row was `candidate` still looked current
  after the park and a late win could re-promote a deliberately non-divergent recipe. Parking now routes
  through `_set()`, and `judge_recipe` refuses to read the corpus at all for a `parked` key (record the
  trial as honest history, filter at the consumer — the same firewall the provenance filter uses).
  *Lesson: the bug was not a missing guard but an existing guard with a road around it.*
- **P0-N3 — an explicit `flow_variant` did not select the requested run.** All three stage runners took
  `flow_variant` as `$3` but used it ONLY for the live-ORFS-results fallback; the five run-selection loops
  took the first reverse-sorted `RUN_*` holding a final DEF. `run_graphs.sh <proj> nangate45 variant_a`
  returned 0 while publishing variant_b's layout — a dataset-identity failure of the same silent class as
  #30. New shared `scripts/flow/_select_run.sh` (sibling of `_provenance.sh`, one copy) filters on
  `run-meta.json`'s recorded `flow_variant`. No variant requested ⇒ byte-identical legacy ordering; a
  requested variant fails CLOSED (an unrecorded run cannot satisfy an explicit request).
- **P0-N4 — a failed extractor republished stale labels under a fresh marker.** `run_labels.sh`'s
  `run_soft` is fail-soft BY DESIGN (one dead extractor must degrade one column, not abort six) but was
  also fail-SILENT: extractors wrote in place, so a failure left the PREVIOUS run's CSV at the canonical
  path and the unconditional stats roll-up stamped a fresh completion marker over it. `run_soft` now
  quarantines each declared target to `<name>.stale` BEFORE launching, so a failure leaves the path absent
  — which `compute_label_stats` already calls `skipped`. Second half: `graph_lib.load_label_df` raised
  `FileNotFoundError` on an absent label, killing the builder AND verifier with a traceback and producing
  NO manifest; it now degrades to an empty frame and `label_health` reports an explicit `missing` status,
  so the manifest honestly reads `ok_with_label_gaps`.
- **P0-R4 — a partial source manifest claimed full verification.** `promote_candidates.py` digested only
  files the manifest happened to mention, then set `source_bytes_verified=True` regardless. A real 5-file
  `eth_rxethmac` candidate with a 1-file manifest promoted with `source_bytes_verified=true` and
  `rtl_file_count=5`. Coverage is now checked FIRST: every `rtl_file` needs a manifest entry
  (`source_manifest_incomplete`) before any per-file comparison is meaningful.
- **P0-R6 — legacy candidates with no manifest auto-promoted.** The `source_bytes_verified=false` stamp
  was descriptive, not an enforcement gate: promotion continued into project creation and vendoring, so an
  automatic campaign could publish a design whose synth-proven bytes cannot be reconstructed. Now blocked
  (`source_manifest_missing`) with a logged `--allow-unverified-source` operator override that RETAINS the
  false stamp downstream — matching how the license gate already fails closed on legacy `unknown`.
- **P1-N1 — the canonical trace API mixed recipe domains.** `observe.solution_origin` scoped
  `recipe_status` by the full key but `ab_trials` by `symptom_id+strategy` and `fix_trajectories` by
  `symptom_id+winning_strategy`, so a nangate45/logic-small trace silently absorbed a sky130hd/cpu-large
  loss. `density_relief` alone spans 44 lifecycle keys across 9 symptoms and 22 domains, so mixing is the
  normal case. Evidence is now full-key scoped, with cross-domain rows RETAINED under `transfer_evidence`
  tagged by domain (transfer is this skill's premise — it just must not masquerade as the key's own
  record). `bug_solutions` no longer reports a latest-row-across-domains status: it takes optional
  `design_class`/`platform`, else returns `status_by_domain` and collapses to `mixed` when domains disagree.
- **P1-N2 — J4 ordered mixed-timezone timestamps as STRINGS.** This store really is mixed: ~42k `Z` rows
  beside ~14k `+HH:MM` offset rows. Lexically `2026-07-18T10:00:00+08:00` (02:00Z) sorts ABOVE
  `2026-07-18T03:00:00Z` (03:00Z), so a resolving action genuinely OLDER than a dangle looked newer and
  the dangle was written off as benign re-ingest residue — hiding a live writer failure behind the benign
  class, i.e. defeating the very distinction 9d5125f added J4 to draw. Now ordered by
  `julianday()`; the original ts is kept for display, and an UNPARSEABLE ts is never silently benign.
  Verdict on the real store is unchanged (8 dangles, 2 UNEXPLAINED).
- **P0-R2 — A/B ownership never bound the FULL recipe key (the round's most serious find).** The report
  worded this as "only an eight-character strategy prefix", which UNDERSTATES the existing checks:
  `_arms_owned` already verifies role-per-column, same base subject, identical trial tail, strat8 AND
  platform, and there are zero 8-char strategy-prefix collisions today. The real hole is that it never
  sees `symptom_id` or `design_class`, so one physical experiment certifies every key sharing a subject +
  strategy + platform. **Live in the committed store:** arm pair `80696de2/ce5b719f` is the SOLE decisive
  evidence promoting THREE density_relief keys (`logic/unknown` + `bus_heavy/medium` + `crypto/small`,
  trials 395/397/401); `8949e7f8/937964ec` shadows two core_util_relief keys (400/403). All 6 stamped
  `provenance_complete=1`. The planner had already solved it and the verifier just never read it back —
  `engineer_loop` stamps `trial_h6 = sha1(symptom|class|platform|strategy)[:6]` UPPERCASE into the arm dir.
  `_arms_owned` now re-derives it and requires the tail to carry THIS key's hash — but only when the tail
  actually holds a 6-uppercase-hex segment. All 6 committed decisive trials predate the scheme
  (`density__0`, no hash) and are GRANDFATHERED: rejecting them flips nothing today (the judge would just
  see no decisive evidence) but would make 6 live keys un-re-derivable from evidence nobody can
  regenerate. Verified 0 verdicts moved by re-judging all 114 trial keys under old vs new code.
- **P1-R2 — repair-cycle detection discarded violation counts.** `_global_repair_state` kept only the SET
  of nonzero DRC classes, so the real `wbuart32` pair (same `M1_SPACING`, counts 100 and 10) fingerprinted
  identically and a 90% reduction escalated as `repair_cycle_nonconverged`, stopping a converging
  campaign. Each class now carries a MAGNITUDE bucket (`count.bit_length()`): the documented tolerance is a
  factor of ~2, so a halving is progress and 100→95 is still the same state. Raw counts would have been
  the opposite error (100 vs 99 = "new" state ⇒ no ping-pong ever caught); bucketing also errs toward
  letting a campaign continue, which is the cheaper failure direction.

**Two measurements that must be re-taken, not assumed, before the next round acts:**
- **100% of the corpus is manifest-less.** 708/708 `design_meta.json` under `rtl_designs/` have NO
  `source_manifest` (and 708/708 DO have a local vendored `rtl/`). The report examined 9 and inferred a
  minority. So the P0-R6 default-block halts promotion for the WHOLE existing corpus until each candidate
  is re-expanded or promoted with `--allow-unverified-source`. Kept default-block per operator decision
  (2026-07-19), matching the already-fail-closed license gate — but budget a re-expansion wave.
- **The 2026-07-16 issue-6 staleness handshake is INERT on the committed store.**
  `status_version IS NOT NULL` = **0 of 140** `recipe_status` rows, so `engineer_loop`'s
  `if _rsv is not None` never stamps an arm and the judge's mid-trial cancel can never fire. It
  self-arms as rows transition (every `_set` does `COALESCE(status_version,0)+1`, and P0-N1 above added
  parking to that set) — but until then the guard is decorative. Do NOT read "the guard exists" as "the
  guard is live"; check the column. Backfilling it is a tracked-DB mutation ⇒ operator action.

**Audited but NOT fixed — architectural or operator-decision, listed so the next round does not re-derive
them:** P0-R3 (above), P0-R5 (include headers not vendored/frozen), P0-R7 (project-level signoff
reports not bound to the selected DEF's digest), P0-R8 (feature/label freshness is mtime- not
content-based), P0-R9 (graph publication is not an atomic generation swap), P0-N2 (synth `config.mk` is
re-parsed at promote time, so top params/defines are not frozen), P0-N7 (no graph schema-version contract),
P1-N6 (relocated corpora keep absolute acquisition-time paths), P1-R1 (no-PPA re-ingests collapse distinct
attempts), P1-R3 (diagnosis can learn a superseded intermediate timing failure). These share ONE root the
report names correctly: **identity is inferred from mutable paths, filenames, timestamps, and file
presence** instead of carried as one immutable generation id. Fixing them piecemeal adds another local
guard per symptom; the durable fix is a compilation-input → candidate → project → run → X/Y → graph
identity chain.

### 2026-07-16 full-pipeline issue-report audit (failure-patterns #51 — 12 issues + 1 found in verification)

The companion audit (`docs/superpowers/plans/2026-07-16-full-pipeline-issue-report.md`) probed the
COMPLETE path — discovery → clone → closure → synth-only proof → publish → promote → ORFS → signoff →
graph build — with real repos (ethmac/wbi2c/axi-interconnect) plus isolated probes. All 12 confirmed
live and fixed TDD; the /r2g-debug verification pass then surfaced a 13th (the platform-arg provenance
hole below) and proved it red→green on its own reproduction.

- **#1 — promotion not bound to the synth-proven RTL bytes.** `rtl_signature` hashes top + sorted PATH
  strings (a dedup key), and promote vendored whatever bytes the paths held NOW. **Fix:** expansion
  stamps a per-file `source_manifest` `[{path,size,sha256}]` (+ rollup `source_digest`) into
  design_meta; `promote_one` re-digests every file and refuses `rtl_bytes_changed_since_synth`;
  legacy metas promote with an explicit `source_bytes_verified:false` stamp. `rtl_signature` keeps its
  dedup semantics untouched.
- **#2 — unknown license / unresolved revision passed publish.** No stage recorded either. **Fix:**
  `clone_repo_manifest` records `resolved_commit` (`git rev-parse HEAD`, works under --depth 1) + a
  conservative license classification (`allow|review|deny|unknown`; AGPL/GPL deny, LGPL/MPL review,
  permissive allow, unclear unknown) in the clone summary; expansion stamps `source_kind`/
  `source_commit`/`license_status` into every design_meta; the publish gate FAILS CLOSED —
  `allowed_license_status` (default `["allow"]`) + `require_source_commit` for cloned repos; legacy
  metas read `unknown` → blocked with an explicit reason.
- **#3 — failed synth reconstructed as success from stale files.** `synthesize()`'s rc was captured
  but never read (success == "a netlist path exists", satisfiable by a PRIOR run's file), and
  `rebuild_external_index_from_dirs` inferred success from surviving artifacts over a `synth_failed`
  meta. **Fix:** success needs rc==0 AND a netlist FRESHER than the invocation; a failed rerun
  quarantines the prior generation's mapped_netlist/netlist_graph/cell_stats; the index rebuilder
  treats design_meta status as authoritative (artifacts can only CONFIRM, never overrule).
- **#4 — `design_action=reject` was publish-eligible.** The SHIPPED `publish_policy.json` listed
  `reject` in `allowed_design_actions` (the code honored the policy; the policy was the defect — and
  the test masked it by passing its OWN policy). **Fix:** `reject` dropped from the policy; the loader
  refuses any policy carrying a reserved terminal action; a test now loads the ACTUAL shipped policy.
- **#5 — sequential designs silently promoted under a virtual clock.** `detect_clock_port` knew only a
  fixed name list (missed ethmac's `Clk`: 119 unclocked registers, STA-0450, meaningless timing labels
  downstream). **Fix:** shared `common/clock_infer.py` ranks the top module's edge-driven INPUT ports
  (reset-like excluded); a single candidate auto-adopts, ambiguity stays unresolved; `promote_one`
  REFUSES `rejected_unconstrained_clock` when seq_cells>0 falls back to virtual (overrides:
  `--clock-port`, `--allow-virtual-clock`); expand's `make_minimal_sdc` prepends the inferred names.
- **#6 — graph regeneration not atomic/invalidating** (def-graph). `run_graphs.sh::skip()` exited 0
  leaving a prior green `dataset/graph_manifest.json` + stale `.pt` mix. **Fix:** a signoff-gate BLOCK
  now exits **7** and atomically stamps the manifest `blocked_unsigned` (benign venv/DEF skips stay
  exit 0, manifest untouched — the old dataset still matches its manifest); `build_graphs.py` deletes
  stale `[b-f]_graph*.pt` not in the new manifest before the atomic manifest commit; the verifier
  fails a `blocked_unsigned` manifest with a clear check.
- **#7 — signoff restaging pinned to the FIRST staged run.** `.r2g_restaged` was an empty boolean +
  `cp -n`, so a newer backend run never restaged. **Fix:** the marker is identity-bearing (records the
  picked RUN basename); a differing/legacy identity clobber-restages and restamps; same identity stays
  the fast-path no-op. The older-layout `final/` fallback is identity-aware too.
- **#8 — no filesystem containment on acquisition.** `tarfile.extractall` unvalidated (recreates
  `../`, absolute paths, symlinks) and discovery followed symlinks out of the repo. **Fix:** safe
  extraction (`filter="data"` + manual validator fallback; zip symlink members rejected); discovery
  requires every candidate path to RESOLVE inside its own repo root (counted + warned, never silent).
- **#9 — ORFS run/workspace identity collisions.** 1-second `RUN_TAG` + `mkdir -p` merged same-second
  runs into one backend dir; default `FLOW_VARIANT` from project basename collided across parents;
  fix_signoff.sh never forwarded a variant to run_drc/run_lvs/run_orfs. **Fix:** `RUN_<ts>_<pid>_<rand>`
  minted with create-must-succeed (+8 retries); run-meta.json records `flow_variant`; a non-blocking
  per-workspace `flock` fails FAST with the hard-rule message before clean_all; fix_signoff gains
  `--variant` + recovery from the newest run-meta and forwards it at all six call sites.
- **#10 — dependency closure silently truncated at 16 files.** The cap returned silently; the missing
  SAME-REPO module then failed synth as `low_value_failure` (recall biased against big designs —
  ethmac's `eth_random.v`). **Fix:** `bundle_closure` returns the unresolved-module list; the notes
  carry `bundle_incomplete=<n>; unresolved=<mods>`; the classifier turns a missing-module failure on a
  marked candidate into `retry,missing_local_module`.
- **#11 — quality scorer read a never-emitted `cell_histogram`.** Entropy/unique/rare/redundancy
  silently zeroed (redundancy=0 dropped the −0.5 penalty → redundant designs kept). **Fix:**
  `graph_stats` emits the histogram it already computed; the scorer BLOCKS assessment
  (action `conditional` + `quality_notes=stats_schema_missing:cell_histogram`) when it is absent —
  never scores from fabricated zeros.
- **#12 — generic diagnosis misclassified PPL-0024 + clean timing text.** `'utilization'+'100%'`
  matched Yosys's healthy info line (while the REAL codes DPL-0036/FLW-0024/GPL-0053 don't say
  "utilization"); `'setup violation'` substring-matched "No setup violations found". **Fix:**
  code-anchored utilization rule; clean phrases scrubbed before the timing scan; first-class
  `io_pin_capacity_overflow` kind for PPL-0024 (leading, with current/required perimeter parsed by the
  same regex engineer_loop's recovery uses) — diagnosis.json now agrees with the repair path.
- **#13 (found by the /r2g-debug verification of these fixes) — an EXPLICIT platform arg overrode the
  DEF's build provenance.** The #30 guard was deliberately skipped when the caller passed a platform
  arg, so `run_graphs.sh design_cases/iir sky130hd` on an sky130hs-built DEF silently stamped a
  wrong-platform manifest — sky130hd liberty resolved against hs masters, every lib-derived verifier
  check failing (283/293) while the build itself looked clean. **Fix:** the `_provenance.sh` guard now
  runs for explicit args too (run-meta wins with a loud WARNING; `R2G_PLATFORM_FORCE=1` is the
  deliberate escape hatch); proven red→green on the exact reproduction (294/294 restored). Guard:
  `def-graph/tests/test_platform_provenance.py::test_guard_runs_even_for_explicit_platform_arg`.

### 2026-07-15 agent-logic issue-report audit (failure-patterns #49 — 16 issues)

An external adversarial audit (`docs/superpowers/plans/2026-07-15-issue-report.md`) probed A/B
causality, lifecycle transitions, learning isolation, evidence validity, negative-memory scope, repair
termination, and dataset provenance. Two of the reported issues (P0-4 identical-run-id promote, P1-6
pooled-as-promoted) were ALREADY closed by #48; the other 14 were confirmed against the live tree and
fixed TDD. The committed store gained two nullable columns (`ab_trials.trial_uuid`,
`fix_trajectories.provenance`) but **0 verdicts moved** (the subject-dedup judge was verified to give an
identical promote/shadow decision on every one of the 114 committed decisive keys).

- **P0-10 / P1-11 — A/B evidence not bound to real, independent runs.** `record_trial` stamped
  `provenance_complete` from string distinctness alone, so a decisive win citing FABRICATED run_ids (not in
  `runs`) promoted; and `judge_recipe` counted raw rows, so N pseudo-replicated trials on ONE subject read
  as N-fold corroboration. **Fix:** `provenance_complete` is now computed authoritatively against `runs`
  (both run_ids must EXIST); `judge_recipe` collapses decisive trials to INDEPENDENT SUBJECTS
  (`_trial_subject` strips the `_ab[AB]_` suffix; legacy NULL-run_id rows fall back to a per-row key so
  committed verdicts are unchanged). Guards: `test_ab_runner.py::test_{fabricated_run_ids_never_promote,
  pseudo_replication_cannot_overturn_a_genuine_loss,pseudo_replicated_wins_count_once_in_evidence}`.
- **P0-11 / P0-12 — no causal isolation / spec equality.** The judge read only success + wall-clock, so
  arm B could win by carrying an unrelated CLOCK_PERIOD edit or by relaxing the clock / enlarging the die
  (reward hacking). **Fix:** `_arm_spec_mismatch` — a SIGNOFF trial (drc/lvs/route/both) must keep the
  design SPEC (CLOCK_PERIOD/DIE_AREA/CORE_AREA + SDC period) IDENTICAL across arms; timing/place/synth
  recipes are exempt. A mismatch forces the trial to non-promoting `inconclusive`.
- **P0-13 — target-clear hides a new regression.** `_drc_symptom_cleared` tested only the target class, so
  arm B could clear M1_SPACING while opening 8 NEW_FATAL_SHORT and still win. **Fix:**
  `_ab_new_drc_regression` vetoes a win when arm B's newly-introduced DRC-class count EXCEEDS arm A's total
  residual (materially worse) — a benign unrelated residual that merely became visible in B (arms reach
  different flow stages) does NOT veto. Guards: `test_ab_causal_guards.py`.
- **P0-14 — A/B arms fed the ordinary learner.** `learn_heuristics` excluded only `is_bench`, so a fix
  episode run INSIDE an A/B arm (`_ab[AB]_` dir, or `eval_arm` set) drove both the ab_trials lifecycle AND
  recipe ranking (circular evidence). **Fix:** `_fetch_learnable_rows` + `_ab_arm_project_paths` firewall
  arm runs/trajectories out of learning. Guard: `test_learn_recipes_indexed.py::test_ab_arm_episodes_
  excluded_from_recipe_learning`.
- **P0-15 — lifecycle safety fail-open.** When the lifecycle store was UNREADABLE, `_annotate_live_gates`
  returned an unannotated plan and `_live_auto_strategy` proceeded with un-gated static selection. **Fix:**
  the annotator stamps `plan['lifecycle_gate_ok']`; the selector fails CLOSED (no blind auto-apply) when it
  is False. A missing path auto-creates an empty store (a clean read), so cold-start still works.
- **P0-16 — non-atomic trial insert.** A crash/retry re-recorded the same trial (2 rows, inflated
  evidence). **Fix:** a deterministic `trial_uuid` (engineer_loop derives it from the arm run_ids) makes
  `record_trial` idempotent. Guard: `test_ab_runner.py::test_trial_uuid_makes_record_idempotent`.
- **P0-17 — dataset gate did not bind the DEF to its reports.** `signoff_gate.evaluate` checked report
  contents independently, so a clean R1 report bundle certified an R2 `6_final.def`. **Fix:** the gate now
  takes the selected DEF and requires it to live UNDER the reports' run dir (`_check_binding`); an unbound
  DEF is a hard block, and a `def_fingerprint` rides the manifest. Guards: `def-graph/tests/test_signoff_
  gate.py::test_def_{bound_to_run_passes,from_other_run_is_blocked}`.
- **P0-6 — arbitrary no-op candidate could enter A/B.** Only the static `NONDIVERGENT_STRATEGIES` denylist
  was checked. **Fix:** `_known_apply_strategy` PARKS a candidate whose strategy is neither a catalog/backend
  strategy NOR present in `fix_events` (a genuinely-learned strategy always has a fix_event, so it is never
  mis-parked — only a fabricated/unapplyable one).
- **P1-10 — candidate leaked into live auto-apply.** `_live_auto_strategy` skipped only `shadow`. **Fix:**
  it now blocks `candidate` too (`_LIVE_BLOCKED_LIFECYCLE`); `parked` (a harmless no-op recipe) stays
  applicable. Guard: `test_negative_evidence_gates.py::test_live_auto_strategy_skips_candidate`.
- **P1-12 / P1-14 — negative evidence too broad by symptom, too narrow by strategy id.** The dead-evidence
  query keyed `(project, check, strategy)`. **Fix:** it now keys by `symptom_id` when known (a strategy dead
  on symptom A is not dead on symptom B) AND propagates the dead flag across strategy aliases that share an
  `_effect_fp` (byte-identical config/env/sdc edits).
- **P1-13 — regression auto-demotion had no caller.** `ab_runner.auto_demote_on_regression` existed but
  nothing invoked it. **Fix:** `learn()` sweeps every promoted recipe on each rebuild (the deterministic
  production boundary). Guard: `test_ab_causal_guards.py::test_learn_auto_demotes_regressed_promoted_recipe`.
- **P1-15 — stale planned arms.** Arms carried no recipe generation. **Fix:** each arm stamps
  `recipe_generation`; a trial whose recipe was re-learned between planning and judging is CANCELLED, not
  judged against a moved target.
- **P1-16 / P1-17 — evidence validation + provenance.** The judge did arithmetic on negative/NaN samples and
  `json.dumps` stored bare `NaN`; and trajectories dropped `fix_events.provenance`. **Fix:** `_finite_nonneg`
  / `_is_true` sanitize samples, metrics serialize with `allow_nan=False` (non-finite → null), and
  `_build_trajectory` carries provenance into a per-strategy `provenance_sources` so live and backfilled
  evidence stay distinguishable. Guards: `test_ab_runner.py::test_{negative_wall_never_wins_cost_tiebreak,
  nan_metrics_serialize_without_crash}`, `test_learn_recipes_indexed.py::test_provenance_preserved_live_vs_backfill`.
- **P1-18 — cross-check repair cycles unrecognized.** Two individually-successful repairs (DRC-clears-
  timing-breaks ↔ timing-clears-DRC-breaks) never became dead_here. **Fix:** `_global_repair_state` /
  `_detect_repair_cycle` fingerprint the whole (DRC-vector, LVS, timing) signoff state; a revisited state
  raises a `repair_cycle_nonconverged` escalation.
- **P1-19 — match level descriptive but not constraining.** A large-but-weakly-matched pooled recipe
  (90/100) outranked an exact local winner (2/2). **Fix:** `fix_model.rank_strategies` caps a pooled-only
  strategy below any local winner whose OWN observed clearance rate is at least as good (a pooled prior
  still wins when the local is weak). Guard: `test_confidence_floor.py::test_pooled_cannot_displace_exact_
  winner_it_does_not_beat_on_rate`.

### 2026-07-14 recipe-lifecycle audit (failure-patterns #48 — Patterns 17-21)

An external audit (`docs/superpowers/plans/2026-07-14-recipe-lifecycle-audit.md`) probed the
promote/demote lifecycle and reproduced two P0 fail-opens, one A/B design risk, and two
determinism/scope gaps. All five were confirmed against the live tree and fixed TDD; the committed
store was NOT mutated (0 rows were affected — see each pattern's "why non-disruptive").

**Pattern 17 — an incomplete-provenance decisive trial still PROMOTED (P0-1, fail-open).**
`ab_runner.record_trial` stamps `provenance_complete=false` and warns loudly when a decisive
(`win`/`loss`) trial lacks two DISTINCT arm run_ids (unverifiable evidence, #45) — but then
`judge_recipe` counted ALL `win`/`loss` rows with no provenance filter, so the unverifiable win
promoted the recipe anyway. **Fix:** `judge_recipe` excludes any decisive row whose
`metrics_json.provenance_complete` is EXPLICITLY `false`; an ABSENT key is a legacy pre-#45 trial,
grandfathered as countable (the committed store has 0 explicit-`false` rows, so no verdict moved).
The firewall stance: `record_trial` still WRITES the honest row + warning; the judge filters at the
consumer. Guard: `tests/test_ab_runner.py::test_incomplete_provenance_{win_does_not_promote,
loss_does_not_demote}`. (The reconcile tests were modernized to distinct run_ids: post-#48 a
noise-promotion needing reconcile is a provenance-COMPLETE trial with an old-judge verdict — a
None-run_id trial never promotes in the first place, so P0-1 SUBSUMES that reconcile case.)

**Pattern 18 — a missing `recipe_status` row FAIL-OPENS to promoted (P0-2, fail-open).**
`recipe_lifecycle.get_status` returns `GRANDFATHERED='promoted'` for an absent row. That is
load-bearing for the STATIC cold-start path (an un-A/B'd baseline strategy must run on a novel
symptom), but the LEARNED indexed-recipe path (`filter_promoted`) inherited it too — so a recipe
that `learn()` wrote into heuristics.json but whose candidate enqueue CRASHED (the swallow-all
try/except after the heuristics write) ranked live as if promoted, never A/B-validated. **Fix, three
layers:** (a) `get_status` gained a `default` param; `filter_promoted` passes `default=UNROSTERED`
so the LEARNED path FAILS CLOSED on an absent row (the STATIC `_annotate_live_gates` path keeps the
cold-start `promoted` default); (b) `learn()` writes heuristics ATOMICALLY (tmp+rename) and calls
new `recipe_lifecycle.ensure_rostered(conn, heur)` after `diff_and_enqueue` to guarantee every
concrete recipe key has a lifecycle row (a still-missing key is rostered as a CANDIDATE — never
fabricated promoted); (c) `unrostered_keys(conn, heur)` is the coverage invariant (0 on the committed
store — verify with it after any learn). Guards: `tests/test_recipe_lifecycle.py` (fail-closed
filter + ensure_rostered + get_status default) and `test_learn_recipes_indexed.py::
test_learn_rosters_every_recipe_key`.

**Pattern 19 — A/B arms inherit the subject's POST-repair config (P0-3, weakened experiment).**
`plan_arms_for_candidates` copytrees each arm from the subject dir. A previously-FIXED subject
carries the fixer's edits in its `config.mk` auto-block (`# >>> r2g signoff-fix (auto) >>>`, written
by `diagnose_signoff_fix.apply_edits` for every `config_edits` strategy — density_relief,
route_relief, antenna…). Both arms then inherit the treatment: arm A is no longer a clean control and
arm B's forced recipe may be a no-op (`_applied()`), so the trial ties `inconclusive` and the causal
reading is lost (not a false promotion, but a lost experiment). **Fix:** `_reset_arm_config_baseline`
strips the auto-block from each arm at materialization (canonical `BLOCK_START/END` from
diagnose_signoff_fix, so strip and apply never drift), restoring the human-authored baseline; each
arm re-derives its OWN edits at fix time. Each `ab_arm` ledger entry now stamps `baseline_config_sha`
for provenance. LIMITATION: place/synth backend-abort relief writes BARE exports (not the auto-block)
via `_apply_recipe_strategy`; those subjects self-limit (a place-fixed design no longer
place-aborts). Guard: `tests/test_plan_arms_isolation.py::
test_plan_arms_resets_arm_config_to_pre_recipe_baseline`.

**Pattern 20 — `mean_outcome_score` collapsed multiple runs/path ORDER-DEPENDENTLY (P1-1).**
`learn_heuristics` built `score_of = {project_path: outcome_score}` with NO `ORDER BY`, so a path with
>1 scored run (614/1206 of the corpus) kept whichever row SQLite returned LAST — `mean_outcome_score`
flipped with insertion order (0.1,0.9→0.9 but 0.9,0.1→0.1). **Fix:** pick the LATEST-ingested run per
path deterministically (`ROW_NUMBER() OVER (PARTITION BY project_path ORDER BY
julianday(ingested_at) DESC, run_id DESC)`), matching the "latest-ingested row per project is
canonical" rule ingest/repair already follow. `mean_outcome_score` is an advisory ranking tiebreaker,
so it self-heals on the next `learn()`. Guard: `test_learn_recipes_indexed.py::
test_mean_outcome_score_is_deterministic_latest_run`.

**Pattern 21 — the live route path can't reorder learned recipes (P1-2, documented single-strategy).**
`diagnose_signoff_fix` `--check route` sets `recipes=None` (no `load_indexed_recipe`, no lifecycle
filter) and `_route_strategies` emits a single static `route_relief`. This is INTENTIONAL:
route_relief is the sole live route fix and is deliberately NOT lifecycle-stripped (demoting the only
route fix would leave route failures unfixable), and learned route RANKING rides the
learner→heuristics `check=orfs_stage/class=route` path, not this live reader — with one strategy
there is nothing to reorder. **Resolution:** documented as an intentional single-strategy path PLUS a
self-announcing guard that WARNS loudly the moment the route catalog emits >1 strategy (at which point
indexed ranking + lifecycle filtering must be wired here like drc/lvs). Guard:
`test_route_ab_loop.py::test_route_live_path_is_single_strategy_and_guards_growth`.

**Pattern 22 — an undecidable WARN is an ignored WARN: J4 dangling run_ids (2026-07-18 /r2g-debug).**
`check_db_integrity.py`'s J4 reported a bare count and told the operator to interpret it themselves —
*"benign re-ingest residue if small/flat, a writer bug if growing"*. But the tool keeps **no history**,
so "growing vs flat" is undecidable from a single scalar: the WARN reads identically at 8 dangles and
at 800. On this store it had been yellow since the repo rename, and clearing it required hand-running
forensics (dump journal run_ids → anti-join `knowledge.runs` → eyeball timestamps → stat each
`project_path`). That is the same alarm-fatigue failure the DRC/LVS gates are designed to avoid: a
signal that can never go green is a signal that stops being read.

**Mechanism.** The evidence needed was already *in-band* — `j.actions` carries both `project_path` and
`ts`. Exactly two mechanisms benignly explain a dangle, and both are falsifiable:
- **wiped/renamed project** — the dir is gone, so nothing can ever re-mint that run (frozen history).
  Six of this store's eight dangles were `/proj/.../agent-r2g/...`, i.e. the *pre-rename* repo path.
- **re-ingest residue** — `run_id` keys on `ppa.json` mtime, so a re-ingest re-mints it and orphans the
  old row; the proof the writer recovered is a **newer action on that project that DOES resolve**.
  Compare with `>=`, not `>`: journal `ts` is second-resolution, so the re-ingest routinely *ties* the
  orphaned row's timestamp, and a contemporaneous resolving action is still proof of recovery.

What survives both — a **live** project whose newest `run_id`-bearing action resolves to no run — is
precisely the writer bug the WARN was written for, and is now counted apart, named, and printed as a
`chase:` lead. **Severity deliberately stays WARN, never ALARM**: the journal is a best-effort,
gitignored ledger (`J*` are trend signals by contract), so gating a wave driver or CI on it would trade
this alarm-fatigue failure for a worse one — a hard-failing campaign over scratchpad residue.

**Resolution.** J4 classifies in-band and self-diagnoses; on a fully-explained store it now says
`0 UNEXPLAINED -- flat residue, not a live writer bug` instead of an unclearable count. Guards:
`test_check_db_integrity.py::test_J4_classifies_dangling_by_mechanism` (all three buckets distinguished
in one run) and `::test_J4_all_benign_dangles_read_as_flat` (the explicitly-clearable case).
*Generalizable rule: a check whose own message says "you figure out which" is unfinished — either it can
name the lead from data it already holds, or it should not be a check.*

## Dataset-Extraction Silent-Value Defects (features/labels; found by the 2026-07-05 RTL2Graph audit)

A new failure class: the flow completes green, the CSVs materialize, and the VALUES are
wrong — nothing crashes, so only ground-truth cross-checks catch it. All four instances
below were found by verifying the external RTL2Graph pipeline against OpenDB/OpenROAD
ground truth (cordic nangate45 + aes_core sky130hd) and traced back into the skill's own
extractors (shared feature_test_v2/v3 ancestry). Verification recipe (reusable): dump
truth via `openroad -python` (odb counts, ITerm directions, placements), compare per-net
wirelength against `report_wire_length -net ... -detailed_route`, and diff per-cell slack
against `report_checks`.

### 1. Timing labels lost on EVERY register (STA-vs-ODB name-escaping join miss)

- **Symptom:** `timing_features.csv` has `INF,0.0,in_sta_path=false` for all bus-named
  cells — which is every register (`...\[16\]$_SDFF_PP0_`); `report_checks` shows those
  same cells as the worst endpoints. Measured: 0/56 registers labeled on cordic
  nangate45; 5/2476 on aes_core sky130hd. Downstream GNN datasets got timing label 0.0
  (not NaN!) on exactly the timing-critical nodes.
- **Root cause:** `extract_timing.tcl` keyed pin slacks by STA `get_full_name` (names
  UNESCAPED: `U.x_1[16]$_SDFF_PP0_/D`) but looked them up by odb `$inst getName` (names
  DEF-ESCAPED: `U.x_1\[16\]$_SDFF_PP0_`). Cells without escaped chars (yosys `_1503_`)
  matched, so the CSV looked plausible.
- **Fix (2026-07-05):** join on the backslash-stripped canonical form on both sides
  (`r2g_canon_name`); CSV rows keep the odb escaped name so feature-CSV joins still work.
  After fix: 56/56 cordic registers labeled; worst-cell slack matches `report_checks`.

### 2. sky130 RECT patch groups parsed as routing points (wirelength/congestion inflation)

- **Symptom:** on sky130hd, RECT-bearing nets (1283/30k nets on aes_core) report
  wirelengths 100-400x too long (`_00005_`: 1168 um vs OpenROAD's 3.29 um) and
  congestion "utilization" reaches a nonphysical 11x. nangate45 is untouched (its DEFs
  carry no RECT inside NETS) — which is why the original correspondence tests passed.
- **Root cause:** DEF 5.8 `RECT ( dx1 dy1 dx2 dy2 )` patch-metal groups (min-area/
  enclosure patches ORFS emits pervasively on sky130) carry RELATIVE offsets; the blind
  point regex in `techlib.def_parse.route_segments` read the first two as an absolute
  next point, adding a phantom segment to e.g. `(-70, -85)`.
- **Fix (2026-07-05):** strip well-formed 4-integer `RECT ( ... )` groups before point
  extraction (`_ROUTE_RECT_RE`). Note the fixed lengths are CENTERLINE lengths — OpenROAD
  counts patch metal too, so RECT nets read ~0.2 um below `report_wire_length`; that
  residual is patch metal, by-design excluded.

### 3. DEF PIN direction inverted in net driver/sink counts

- **Symptom:** every net touching a chip OUTPUT port counted 2 drivers / 0 sinks
  (`theta_o[10]` on cordic); INPUT-port nets leaned on the "no driver found" fallback.
- **Root cause:** DEF `PINS ... DIRECTION` is the port's direction from the CHIP's
  perspective — an INPUT port DRIVES the net internally, an OUTPUT port SINKS it.
  `nodes_net.py` used the raw direction as if it were a cell pin's.
- **Fix (2026-07-05):** invert the mapping for `PIN` connections. Also implemented the
  until-then hardwired-0 `connects_macro_flag` via `techlib.liberty.macro_cell_keys`
  (masters only present in R2G_LIB_FILES minus R2G_SC_LIB_FILES = per-design macro libs).

### 4. Driver max_capacitance summed into net "load" caps

- **Symptom:** `nodes_pin.csv` `sum_pin_cap_fF` dominated by a constant ~59 fF per net on
  nangate45 (NAND2_X1/ZN max_capacitance) — measured 62.54 fF where the true load is
  3.19 fF (cordic `_0062_`).
- **Root cause:** `get_pin_cap_fF` falls back to an OUTPUT pin's `max_capacitance` (a
  drive LIMIT) when it has no `capacitance` attribute; the per-net summing treated it as
  a load.
- **Fix (2026-07-05):** `get_pin_load_cap_fF` (input loads only) used for the per-net
  sums; `get_pin_cap_fF` kept for callers that want the legacy semantics.

**Tests:** `tests/test_feature_semantics_fixes.py` (synthetic DEF/liberty),
`tests/test_techlib_def_parse.py` (RECT cases). **Baseline note:** the machine-local
byte-for-byte extractor baseline (`tools/regen_extract_baseline.sh`) must be REGENERATED
after these fixes — nodes_net/nodes_pin/wirelength/congestion/timing outputs
intentionally diverge from the pre-2026-07-05 baseline.

### 5. sky130 quoted liberty attribute values — pin direction (and DFF clock flag) lost on EVERY std-cell pin

- **Symptom (aes_core sky130hd):** `nodes_pin.csv` `pin_type_id` collapsed to the
  catch-all id 14 for 93,390/98,343 pins (95%); `nodes_net.csv` `num_drivers` was 1 for
  every net (the "no driver found → assume 1" fallback firing universally — plausible
  values, which is what hid it); 390 nets carried wrong driver/sink counts and 1,065 pins
  a wrong `sum_pin_cap_fF` (load classification needs direction). nangate45 unaffected.
- **Root cause:** sky130hd/hs liberty writes QUOTED simple-attribute values —
  `direction : "input";`, `clock : "true";` (ihp macro libs: `clock : "true" ;`) — and
  `liberty.py`'s unquoted-only regexes (`direction\s*:\s*([A-Za-z_]+)\s*;`) never
  matched, so every sky130 std-cell pin parsed with `direction == ""` and `clock ==
  False`. Same class as the sky130 quoted-cell-name bug (commit 363a8b2) — that fix
  covered cell names/area/power/leakage `value` but missed the four pin attributes.
  Clock pins survived only via the `_looks_like_clock` NAME heuristic.
- **Fix (2026-07-05):** optional-quote tolerance on `direction` / `capacitance` /
  `max_capacitance` / `clock` in `techlib/liberty.py`. Verified on the real sky130hd tt
  lib: 1,771/1,771 pins now carry a direction (was 0), 69 clock pins flagged (was 0).
- **Lesson:** when a value-quoting bug is found for ONE liberty attribute, sweep ALL
  simple-attribute regexes in the parser — the format allows quotes on any of them.

### 6. Interrupted irdrop stage leaves the RAW PDNSim dump at the canonical ir_drop.csv path (silent all-NaN y2)

- **Symptom (aes_core sky130hd, 2026-07-05):** `labels/ir_drop.csv` contained PDNSim's
  raw `Instance,Terminal,Layer,X location,Y location,Voltage` format (no Design/Cell/
  label columns); the graph stage built all five variants with **y2 (irdrop) 100% NaN**
  and manifest `status: "ok"`; `reports/labels_stats.json` was missing entirely.
- **Root cause (chain of four):** (1) `extract_irdrop.tcl` had `analyze_power_grid`
  write its raw voltage file AT the canonical path and post-processed it IN PLACE — an
  external kill (here: a 120s harness-timeout kill of the whole `run_labels.sh` process
  group, landing between the raw write and the rewrite) leaves a valid-looking wrong
  file; (2) `compute_label_stats.py` reported `status: "ok"` for a CSV with zero
  parseable `label` values; (3) `graph_lib.build_*_label_values` silently `continue` on
  missing Design/Cell/label columns → all-NaN y with no warning; (4) `run_graphs.sh`
  judged label freshness by `wirelength.csv` alone, so the half-finished labels dir
  passed as fresh on the next graph build.
- **Fix (2026-07-05):** (1) PDNSim writes to `ir_drop.csv.raw`; the processed CSV is
  published by atomic `file rename`; any pre-existing canonical file is deleted at stage
  start (an interrupted run now leaves an honestly-missing CSV); (2) stats report
  `status: "invalid"` + reason when rows exist but no label parses; (3) new
  `graph_lib.label_health()` — build_graphs warns per unusable file and records
  `label_health` + `status: "ok_with_label_gaps"` in the manifest; (4) `needs_stage`
  requires the stage-completion marker (`features_stats.json`/`labels_stats.json`,
  written LAST) to be present and DEF-fresh.
- **Lesson:** every fail-soft fallback needs a loud, machine-readable trace. All four
  links produced *plausible* outputs; only a ground-truth NaN-fraction check caught it.

**Tests (5+6):** `tests/test_techlib_liberty.py` (quoted attributes, real-lib direction
coverage), `tests/test_compute_label_stats.py` (raw/non-numeric → invalid),
`tests/test_graph_stage.py` (label_health, manifest gap flag, duplicate-key guards).

### 7. Congestion vertical demand keyed TRANSPOSED — ~80% of congestion labels wrong

- **Symptom (aes_core sky130hd):** fixing the key changes `cell_congestion` for
  151,335/189,774 cells (79.7%); mean |Δ| 0.052, max 0.323 on a 0–0.44 scale (e.g.
  ANTENNA_30 read 0.009 where the true value is 0.096 — 10×). A cell physically ON a
  vertical wire could read 0.0 while its diagonal-mirror cell read the phantom demand.
- **Root cause:** `extract_congestion.add_split_segment` keys demand `(main_grid,
  fixed_grid)`. For horizontal wires main=x → `(x, y)` ✓; for vertical wires main=y →
  `(y, x)` ✗ — transposed vs. the `(x_gcell, y_gcell)` convention that
  `build_grid_utilization` (which `max()`es h/v at the SAME key) and the cell mapper
  use. Every cell's v_util came from its diagonal-mirror gcell; on non-square grids
  transposed keys also fall off-grid (demand silently orphaned). Latent since the
  original RTL2Graph ancestor; ORTHOGONAL to the #2 RECT fix (that validated point
  extraction, which is upstream and was correct). The demand grid had zero test
  coverage — `test_extract_congestion.py` only exercised LEF layer parsing.
- **Fix (2026-07-05):** `add_split_segment(..., vertical=True)` emits `(fixed, main)`
  = `(x, y)` for vertical wires; directional demand-grid tests added (a vertical wire
  must fill a COLUMN; on-wire cell sees congestion, mirror cell sees none).

### 8. capacitive_load_unit quoted unit — every sky130 pin cap 1000× too small

- **Symptom:** sky130 `sum_pin_cap_fF` ≈ 0.002 fF per pin (impossible); nangate45 fine.
- **Root cause:** the #5 quote sweep missed its sibling: sky130 writes
  `capacitive_load_unit(1.0000000000, "pf");` and the bare-`[A-Za-z]+` unit regex
  rejected `"pf"`, so `cap_scale_ff` stayed 1.0 and pf→fF scaling never happened
  (nangate45's bare `(1,ff)` correctly needs no scaling — which is why only sky130
  broke). The #5 direction fix *widened* the damage: output pins became correctly
  OUTPUT, so the `max_capacitance` fallback (also unscaled) started firing too.
- **Fix (2026-07-05):** optional quotes on the unit token. Real-lib check:
  `cap_scale_ff` 1.0 → 1000.0; nand2_1/A = 2.315 fF (physically sane).
- **Lesson (upgrade of #5's):** when a quoting bug is found, sweep the WHOLE file for
  every value-capturing regex — including complex attributes — in ONE pass; fixing
  only the reported sites left this one live through two audit waves.

### 9. `parse_nets` drops `+ USE` on the net's dash line — `use` an artifact of line-wrapping

- **Symptom (aes_core sky130hd):** `use` populated for only 1,666/30,345 nets — exactly
  the ones whose connection list happened to wrap to a continuation line. ORFS emits
  `+ USE` ON the `-` declaration line for single-line nets (28,679 of them here).
- **Root cause:** the `-` branch in `techlib/def_parse.parse_nets` extracted conns and
  `continue`d without scanning USE; the USE regex ran only on continuation lines.
  Masked because `infer_net_type_id` falls back to name tokens (all 329 USE=CLOCK nets
  are named `*clk*`) — a USE=CLOCK net with a non-clocky name would misclassify as
  signal. `nodes_net.parse_pin_dirs` already scanned its dash line (the #3 fix) —
  `parse_nets` simply never mirrored it.
- **Fix (2026-07-05):** the same `m_use` search inside the `-` branch before `continue`.
  Real-DEF check: USE coverage 1,666 → 30,345/30,345 (30,016 SIGNAL + 329 CLOCK).

**Tests (7-9):** `tests/test_extract_congestion.py` (directional demand grid),
`tests/test_techlib_liberty.py` (quoted cap unit), `tests/test_techlib_def_parse.py`
(USE on dash line). **Regeneration note:** waves 1 AND 2 of the 2026-07-05 fixes both
change sky130 outputs — congestion labels (#7), `sum_pin_cap_fF` (#8), `pin_type_id` /
`num_drivers` / `num_sinks` (#5), irdrop labels (#6). ALL sky130 feature/label CSVs and
graph datasets predating BOTH waves are wrong; regenerate before training.

**Known modeling choices (documented, not bugs):** `extract_timing.tcl` constrains only
the clock — no `set_input_delay`/`set_output_delay` — so pure I/O paths are
unconstrained (input→reg slacks optimistic; cells feeding only output ports get
`in_sta_path=false`, 4% of aes_core logic cells; reg↔reg labels unaffected). sky130
physical-only fill/tap/decap cells are absent from the timing liberty → `cell_area`/
`cell_power` 0 + `cell_type_id` UNKNOWN in nodes_gate.csv (84% of rows are such cells;
the graph stage filters them — nangate45's curated map differs, a platform asymmetry).
Power/ground iopin→net edge rows reference nets absent from nodes_net.csv (power nets
live in SPECIALNETS) — the graph stage's net_type filter + inner joins drop them.

### 10. `connects_macro_flag` ≡ 0 on every macro design — SC-lib list already contained the macro libs (2026-07-06 nangate45 round)

- **Symptom:** on a fakeram45 design (mem_soc, 2 SRAM macros, ~230 macro-connected
  nets) `nodes_net.csv` had `connects_macro_flag=0` for ALL 1,248 nets. No error
  anywhere — pure std-cell designs legitimately have all-0, so the column looked alive.
- **Root cause:** ORFS's resolved `LIB_FILES` **already folds `ADDITIONAL_LIBS` in**,
  so `run_features.sh`'s `R2G_SC_LIB_FILES="$LIB_FILES"` (the "std-cell subset")
  contained the macro libs too. `macro_cell_keys()` = lib_files − sc_lib_files = ∅ →
  the flag can never fire, on ANY platform. Same wiring also fed macro cells into the
  runtime cell-type map's "std-cell-only" id space — a LATENT cross-design id reshuffle
  (nangate45 escaped only because uppercase std names sort before `fakeram45_*`).
- **Fix:** `run_features.sh` now subtracts `ADDITIONAL_LIBS` from `LIB_FILES` when
  exporting `R2G_SC_LIB_FILES`. Verified live: mem_soc regenerated with 237 flag=1
  nets matching the DEF∩LEF-BLOCK truth (verifier check
  `ext.net connects_macro_flag == DEF∩LEF-BLOCK truth`).

### 11. Liberty `bus()`/`bundle()` groups unparsed — every macro bus pin unclassified with cap 0 (all platforms)

- **Symptom:** fakeram45 bus pins (`addr_in[3]`, `wd_in[5]`, `rd_out[0]`, …) got
  `pin_type_id=14` (the untyped fallback) and contributed 0 to `sum_pin_cap_fF`,
  while the SAME macro's scalar pins (clk/we_in/ce_in) were typed with real caps —
  the mixed result made the CSV look plausible.
- **Root cause:** macro liberty declares direction/capacitance ONCE at the `bus()`
  group level with NO per-bit `pin()` members; `techlib/liberty.py` only matched
  `pin (...) {`. The DEF connects per-bit, so every lookup missed. Std-cell-only
  platforms never exposed this (no bus groups in std liberty).
- **Fix:** the pin-group regex now also enters `bus()`/`bundle()` groups, and
  `get_pin_info` falls back from `name[idx]` to the bus base entry. Regression:
  `test_bus_group_members_resolve` + verifier check
  `ext.macro pins classified (no type-14 bus fallout)`.

### 12. nangate45 curated cell-type map drifted 22 masters behind the deployed liberty (RETIRED → runtime map)

- **Symptom:** every instance of AOI211_X1/AOI222_X1/OAI211_X1/OAI222_X1/OAI222_X4,
  ALL scan FFs (SDFF*/SDFFR*/SDFFS*/SDFFRS*), ALL clock gates (CLKGATE*/CLKGATETST*),
  and TLAT_X1 took `cell_type_id=95` — the literal UNKNOWN sentinel — silently aliased
  onto genuinely-unknown masters. Reverse diff proved version drift: the map still
  carried DFFSR/DLHR/DLHS/TINV_X2 keys the current liberty no longer ships. Dormant on
  the default corpus (no DFT/clock-gating ⇒ 0.001% of 46.7M instances), a cliff the
  moment SYNTH clock gating, scan, or latch inference is enabled.
- **Root cause:** nangate45 was the ONLY platform hardwired to a frozen curated map
  (`resolve_cell_type_map` special case); every other platform builds the map from the
  resolved liberty at runtime and cannot miss a cell.
- **Fix:** curated special-case retired — nangate45 uses the runtime liberty-derived
  map like everyone else (self-heals future liberty drift, covers all 23 fakeram
  sizes). Macro-lib cells now take a dedicated shared `MACRO` id (= UNKNOWN+1) instead
  of collapsing into UNKNOWN. **Any nangate45 dataset built against the curated ids
  must be regenerated** (they were already invalidated by #7). Regression:
  `test_nangate45_runtime_map_heals_drifted_masters` + verifier checks
  `ext.macro masters share one dedicated id` / `ext.distinct std masters get distinct ids`.

### 13. `metadata.csv` tracks_per_layer was a pipe-joined STRING — `global_feat[12]` = 0.0 on every platform

- **Symptom:** `global_feat[12]` was 0.0 in every graph of every platform; the CSV
  column held `metal1:228|metal10:12|…`, which `load_global_feat`'s `pd.to_numeric`
  coerced to NaN → 0.0. Same mechanism zeroes PLACE_DENSITY/CORE_UTILIZATION when they
  hold non-numeric strings like "Default" (those are accepted as honest absences).
- **Fix:** `metadata.py` emits the numeric MEAN per-layer track count in
  `tracks_per_layer` and moved the per-layer detail to a new trailing
  `tracks_detail` column (loaders read by column name — inert). Regression: verifier
  checks `ext.metadata tracks_per_layer numeric mean` + `ext.global_feat[12] tracks nonzero`.

### 14. `classify_pin_type` name-heuristic ordering — FA/HA sum output `S` labeled "select"; ICGs not sequential

- **Symptom:** nangate45 FA_X1/HA_X1 declare the SUM output as `pin (S) { direction :
  output }`; `_looks_like_select` ran before the OUTPUT branch, so adder sum pins got
  pin_type_id 10 (select input) instead of 4 (output) — platform-agnostic, hits
  arithmetic-heavy designs. Related: CLKGATE*/CLKGATETST* hold state via `statetable()`
  (not ff/latch), so `is_sequential` mis-flagged ICGs combinational.
- **Fix:** select classification now requires `direction != OUTPUT` (MUX2_X1's S input
  still classifies 10); `statetable(` marks sequential. Regression:
  `test_select_name_on_output_pin_is_output`, `test_statetable_marks_sequential`.

### 15. Liberty `ff_bank`/`latch_bank` (multibit sequential) not detected — `is_sequential` false on every asap7 multibit flop (2026-07-06 nangate45 verification round)

- **Symptom:** `load_liberty_db` marks a cell sequential when its body opens an
  `ff(`/`latch(`/`statetable(` group, but MULTIBIT flops/latches declare state via
  `ff_bank(...)`/`latch_bank(...)`. The old regex `(ff|latch|statetable)\s*\(` fails to
  match `ff_bank(` (after `ff` comes `_`, not `\s*\(`), so `is_sequential` stayed False.
  asap7 ships 27 liberty files using `ff_bank`; nangate45/sky130/gf180/ihp have none.
- **Impact:** currently **inert** — `is_sequential` is written but consumed by no
  feature/label (dead field). Fixed defensively so a future consumer (or an asap7 graph
  build) is correct, and because it is a genuine shared-parser defect surfaced by the
  synthetic corner-case suite (real nangate45 files never exercise `ff_bank`).
- **Fix:** regex widened to `(ff_bank|ff|latch_bank|latch|statetable)\s*\(` (longer
  alternatives first so the match is unambiguous). Regression:
  `test_ff_and_latch_bank_are_sequential`, `test_ff_latch_statetable_still_sequential_combinational_not`.

### 16. `compute_feature_stats` had no honesty gate — a raw/truncated feature CSV summarized as "ok" (X-side mirror of #6)

- **Symptom:** `compute_label_stats.py` flags a label CSV `invalid` when it has rows but
  no numeric label column (the #6 irdrop raw-dump lesson). `compute_feature_stats.py` had
  **no** equivalent: it returned `status:"ok"` for ANY non-empty CSV. A worker killed
  mid-write (truncated rows) or a stale/raw CSV left at the canonical path was reported
  fresh and healthy — the exact honesty failure the label side already guards. Asymmetric
  and silent; not triggered on a clean nangate45 run (all workers succeed).
- **Fix:** `compute_feature_stats.summarize` now checks each CSV's identity columns
  against a `REQUIRED_COLS` schema — a missing column ⇒ `invalid` (raw/wrong-schema
  dump), a required column left unset on any row ⇒ `invalid` (truncated write) — and
  `main()` warns to stderr, mirroring the label gate. Behavior-neutral on well-formed
  nangate45 CSVs (all 8 feature sets stay `ok`). Regression:
  `test_feature_stats_flags_missing_columns_invalid`,
  `test_feature_stats_flags_truncated_rows_invalid`, `test_feature_stats_ok_on_complete_csv`.

### 17. `netlist_graph` tie-off constants in a concatenation leaked a phantom net (`{1'b0, sig}` → net `b0`)

- **Symptom:** `extract_signal_names` tokenizes a concatenation with the plain-id regex;
  a Verilog sized constant `1'b0` inside `{...}` is split by the `'` and its fragment
  `b0` was emitted as a (fake) net node/edge. ORFS mapped netlists tie constants through
  `LOGIC0_X1`/`LOGIC1_X1` cells (verified: zero literal constants across the sampled
  corpus), so this never fired in production — a latent defect a hand-written or
  non-ORFS netlist would trip.
- **Fix:** sized constant literals (`\d+'[bodh]…`) are stripped from a concatenation
  before tokenizing (standalone-constant normalization to CONST0/CONST1 unchanged).
  Regression: `test_netlist_constants_in_concat_are_dropped`.

### 18. Minor latent parity fixes (2026-07-06 audit) — no nangate45 impact

- `run_labels.sh` did not export `R2G_PLATFORM` to `extract_congestion.py`, which fell
  back to asap7's routing-layer profile — harmless today (all platforms share one
  fallback table AND it only fires when the tech LEF yields no routing layers) but a
  cross-platform hazard once the fallback becomes platform-specific. Now passed through.
- `edges_iopin_net.py` did not `rstrip(';')` a DIRECTION captured on a *continuation*
  line (the dash-line branch already did), so `+ DIRECTION OUTPUT;` with no space would
  store `OUTPUT;`. DEF normally spaces the `;`; fixed for parity.

### 19. Congestion 2-vector method (radius-4 Gaussian) merged without re-running the corner guardrail (2026-07-07)

The congestion-label merge (`c9b9e3a`, "adapt `Congestion_Parse.py` method") replaced the retired
manual **3×3 (radius-1)** smoothing with a faithful port of the reference's scipy
`gaussian_filter(util, sigma=1.0)` — a **radius = `int(4*sigma+0.5)` = 4** (9-tap) separable
reflect convolution — and made `cell_congestion.csv` a **2-vector** per cell:

- `cell_congestion = mean(gaussian_util)` (smoothed utilization),
- `label           = mean(sqrt(gaussian_util))` (the canonical training target `graph_lib` gate `y1`
  reads == reference `node_label[1]`),
- `label_raw       = mean(sqrt(util))` (raw target == reference `node_label[0]`, new column),

each averaged over the cell's **orientation-aware bbox** GCells (origin-GCell fallback when no cell
`SIZE`). The port is correct: on sky130+nangate45 the pre-gaussian `util` and the label formula match
the reference (verified <1e-8 on DMA_top/gcd/Md5Core), and the anisotropic `PITCH` trap is benign —
sky130's only two-value-`PITCH` routing layer is `li1` (VERTICAL), where the reference's `split()[1]`
and the port's per-direction x-pitch coincide.

**The defect was process, not math:** the merge changed the kernel but did **not** re-run the
corner-case guardrail, leaving `tests/test_corner_case_pipeline.py::test_congestion_label_is_sqrt_and_
fill_is_empty` **RED on `main`**. That test baked in the *retired* radius-1 kernel's locality — it
asserted a fill cell far from any wire has `cell_congestion == 0.0 ± 1e-9`. Under the wider radius-4
kernel that is **false and correct**: the fixture's `i_fill` (GCell (4,4), STEP 2000) reads `0.0104`
because the Gaussian spreads congestion in from routed GCells up to **4** cells away.

**Fix (2026-07-07):** the test is renamed `test_congestion_two_vector_raw_and_smoothed` and now
asserts the NEW semantics — `label_raw` is **exactly 0** for a cell whose own GCell carries no routed
demand (raw, un-smoothed), while `cell_congestion` is **small-but-nonzero** from the radius-4 spread —
turning a rotted assertion into a guardrail that actively documents the 2-vector method.

### 21. RC parasitic labels: SPEF↔DEF name-escaping join miss (silent drop of ~8% nets / ~21% pins) (2026-07-07)

The new RC-label stage (`extract_rc.py` + `techlib/spef.py` → `graph_lib.attach_rc_labels`) adds
ground cap (net-node `y5`), coupling cap (net-pair edge), and equivalent resistance (pin-pair edge)
from the post-route SPEF. During real-design integration on **aes_core (sky130hd)** the RC→feature-CSV
join was only **91.9% (nets) / 78.8% (pins)** — a *silent* loss (the flow was green, CSVs materialized,
`build_graphs` joined by name and just left the non-matching nets/pins without RC).

- **Root cause (same class as #1, different tool pair):** `write_spef` escapes `.`, `$`, `:` etc. with
  a backslash (`keymem\.key_mem\[12\]\[54\]\$_DFFE_PN0P_`), while `write_def` / `techlib.def_parse`
  escape **only** the bus brackets `[` `]` (`keymem.key_mem\[12\]\[54\]$_DFFE_PN0P_`). Every
  hierarchical net (has a `.`) and every double-bus register (`$`) therefore failed the by-name join —
  exactly the timing-critical/high-fanout nodes.
- **Fix:** `techlib.spef._deesc` de-escapes SPEF names to the DEF convention (strip backslash **except**
  before `[`/`]`) at parse time, so every emitted RC name matches `nodes_net`/`nodes_pin`. Measured
  after fix: **100.00%** join on all of ground_cap / coupling / equiv_res / net_driver (aes_core, 30,333
  nets, 768,115 pin endpoints). Pinned by `tests/test_spef.py::test_deesc_matches_def_convention` +
  `test_names_are_deescaped_end_to_end`.
- **Also caught by a test (the "no silent caps" honesty rail):** `equiv_res_pairs` was first written as a
  generator, so its `return {"skipped": n}` fanout-guard sentinel was swallowed by `StopIteration` — a
  capped net would drop silently with no WARN. Rewritten as a plain function returning a list-or-dict;
  `tests/test_spef.py::test_max_fanout_guard` locks it.

Verification recipe (reusable, and the reason both bugs were caught pre-merge): after building RC CSVs
from a real `6_final.spef`, assert the fraction of `Net`/`(Inst,Pin)` keys that join the design's
`nodes_net.csv`/`nodes_pin.csv` is ~100% — a join < 100% means an escaping/format skew, not "some nets
just have no parasitics." RC is a **label** (Y): it rides `y5` + a **separate** parasitic edge set
(`rc_edge_*`), never `x` — see label-extraction.md "RC parasitic labels" + graph-dataset.md.

**Two siblings to watch:**
- `label == sqrt(cell_congestion)` holds **only when a cell's bbox is a single GCell** (the fixture
  supplies no cell LEF, so every cell falls back to its origin GCell). For a **multi-GCell macro**
  `mean(sqrt(g)) ≠ sqrt(mean(g))` (Jensen) — do NOT re-introduce that as a universal invariant.
- `scipy` is **not** on the graph venv (the label stage's pure-python `gaussian_filter_2d` is the
  whole point of having no runtime scipy dep), so `test_gaussian_matches_scipy` **SKIPS** on this
  machine — the pure-python↔scipy bit-match claim is asserted only where a scipy env exists. Run it
  under a scipy python after any change to `gaussian_filter_2d`/`_reflect_index`/`_gaussian_weights`.

**Lesson:** an extractor-semantics change MUST re-run `test_corner_case_pipeline.py` (5c of
`/r2g-debug`) in the same commit — a guardrail that isn't re-run rots into either a CI blocker or a
`-k`'d vacuous test. See `/r2g-debug` Step 5c.

**Fallout fixed in the same 2026-07-07 audit (the merge changed one extractor; three consumers rotted):**

- **`tools/verify_graph_dataset.py` — the ground-truth harness itself false-FAILed every correct
  new-method build.** Its independent congestion recompute (`gaussian(util, x//gs, y//gs)`) used the
  *retired* radius-1 (3×3), zero-boundary, single-origin-GCell kernel, and it asserted
  `label == sqrt(cell_congestion)` for ALL cells. Both are false under the new radius-4 separable
  reflect Gaussian averaged over the cell's bbox (`mean(sqrt(g)) != sqrt(mean(g))` by Jensen). Measured:
  292/400 cells "mismatched" on a correct `aes_core` build; DMA_fsm went 83/85 → exit 1. A verifier that
  false-reds correct output is worse than none — the rational response is to mute it, and then real
  regressions sail through; `--batch` reporting the whole corpus red also *masks* real failures. Fixed:
  the recompute is now an independent radius-4 separable REFLECT Gaussian (`dense_gaussian_r4`) over a
  dense grid, averaged over each cell's orientation-aware bbox (`_lef_macro_sizes` from SC_LEF +
  ADDITIONAL_LEFS), checking all three columns (`cell_congestion`, `label`, `label_raw`); the demand/util
  grid stays re-derived in `read_def_truth` so a transpose/dbu/capacity bug still shows. Now 86/86 on
  DMA_fsm and the three congestion checks PASS on `aes_core` (its stale 2026-07-05 `.pt` still — correctly
  — fails the tensor checks). Helpers pinned in `test_verify_graph_dataset_helpers.py`.
- **`run_graphs.sh` + `netlist_graph.py` — netlist cell-type vocabulary diverged from the feature stage
  on macro designs (the #12 class, re-opened).** `run_graphs.sh` exported only
  `R2G_SC_LIB_FILES="$LIB_FILES"` (the FULL list, macro libs folded in) and `netlist_graph.py` loaded its
  liberty DB from that subset, so `build_runtime_map` interleaved each macro into the sorted std-cell
  vocabulary (drifting every std id after it) — or, loaded std-only, dropped macros to UNKNOWN. Latent on
  pure-std-cell designs (LIB_FILES == std libs) but wrong on any fakeram/macro design. Fixed to mirror
  `nodes_gate.py`: `run_graphs.sh` now exports BOTH `R2G_LIB_FILES` (full) and `R2G_SC_LIB_FILES`
  (std-cell-only subset), and `netlist_graph.py` builds `lib_db` from the full liberty but keys the id
  space on the std-only subset → macros get the shared MACRO id, std ids match the b–f feature graphs.
  Regression: `test_corner_case_units.py::test_netlist_macro_gets_shared_macro_id_not_interleaved`.
- **`compute_label_stats.py` / `compute_feature_stats.py` — the honesty gate passed vacuously on an
  all-NaN column.** `float("nan")` does NOT raise, so `_col_floats` returned a non-empty NaN list: the
  label gate reported `status:"ok"` with NaN summary stats and `json.dump` emitted invalid-JSON `NaN`
  tokens. Latent today (the wirelength/timing/irdrop extractors guard against NaN) but a hole in the
  stated backstop. Fixed: `_col_floats` drops NaN (`v == v`), so an all-NaN column reads as "no numeric
  values" → `invalid`, and the report stays strict JSON. Regression:
  `test_compute_label_stats.py::test_summarize_all_nan_label_is_invalid_not_ok`.

### 20. Verifier irdrop label check ignored the `has_irdrop` noise floor — false-red on low-IR designs (2026-07-07)

- **Symptom:** `verify_graph_dataset.py`'s `ext.irdrop label == log1p(IR/P95)` FAILs (`--batch` exit 1)
  on small/low-IR designs though extraction is correct. `iir`: 85/86 (the one fail is irdrop, max diff
  0.7484); `DMA_Controller_DMA_fsm`: 86/86. Same cries-wolf class as #19 — a verifier that false-reds
  correct output erodes the honesty gate and, via `--batch`, *masks* real regressions in the same corpus.
- **Root cause — the VERIFIER, not the extractor.** `extract_irdrop.tcl:208` gates the label on a
  PDN-noise floor: `has_irdrop = (P95_mV >= 0.05)`, forcing `label = 0` below it (:220-224) — sub-0.05mV IR
  is numerical noise, not signal. `iir` P95 = 0.044mV → `has_irdrop=false` on all 95 rows → all labels
  legitimately 0. The verifier (v_gd:789-794) asserted `label == log1p(IR/P95)` for EVERY row, never
  reading the `has_irdrop` column, so it expected `log1p(0.049/0.044)=0.748` where the extractor correctly
  wrote 0. `DMA_fsm` P95 = 0.065mV (≥ floor) → passed, which is why it hid. The extractor, the graph
  builder, AND `graph_manifest` label_health (`ir_drop.csv status:"ok"`, y2 zeros not NaN) all AGREE; only
  the verifier disagreed → unambiguously a verifier bug. (Found by the Step-5 RTL→Graph verification pass.)
- **Fix (2026-07-07):** extracted a pure helper `irdrop_label_ok(ir)` mirroring the extractor gate —
  `label == log1p(IR/P95)` only where `has_irdrop` (or, for legacy CSVs lacking the column, `P95>=0.05`)
  AND `P95>0`; `label == 0` below the floor. Now `iir` 86/86, `--batch` green. Helpers pinned by
  `test_verify_graph_dataset_helpers.py` (below-floor-all-zero, above-floor-log1p, mixed, corrupted-active,
  nonzero-below-floor, legacy-no-column). **Lesson:** a Y-label verifier MUST mirror the extractor's own
  gating contract, not just its happy-path transform — else it false-reds exactly the designs where the
  gate fires, and small/low-power designs (where it fires) are common.
- **Doc gap noted (not a code bug):** the /r2g-debug "nangate45 direct-verify recipe" (export
  `TECH_LEF`/`SC_LEF`/`R2G_LIB_FILES`/`R2G_PLATFORM` then run the verifier) is NOT implemented — the CLI
  takes only `case_dir`/`--batch` and `resolve_platform_files` reads `case_dir/constraints/config.mk`,
  ignoring those env vars. Real nangate45 dataset verification needs an ORFS-built dataset; today nangate45
  coverage rests on the synthetic corner suite (`corner_synth.py:311` sets `R2G_PLATFORM=nangate45`).

### Corner-case verification infrastructure (2026-07-06)

The bugs above (and the 2026-07-05 batch) live in code paths the REAL nangate45 designs
never exercise, so `tools/verify_graph_dataset.py` (which cross-checks a built dataset
against the raw liberty/LEF/DEF) stays green while they hide. Two new suites close that
gap by exercising corner cases on inputs the extractors control:

- `tests/fixtures/corner_synth.py` + `tests/test_corner_case_pipeline.py` — a
  hand-computable synthetic nangate45-style design (std cells + a bus-pin macro, a
  clock/reset/multi-layer/RECT-patch/2-driver net mix) driven through the REAL feature
  workers → label extractors → PyG builder, asserting every output against
  independently hand-derived ground truth, across **all five graph views b–f** (node/edge
  counts, folded-entity `edge_attr` features + `edge_y` labels, clock-tree/FILL/TAP
  exclusion, undirected symmetry).
- `tests/test_corner_case_units.py` — focused unit corners: ff_bank/latch_bank/statetable
  sequential detection, INOUT/FEEDTHRU + power/ground + multi-digit bus-index pin
  classification, pf→fF cap scaling, CUT-vs-ROUTING LEF layers + VIA re-declaration, the
  congestion demand-key (x_gcell, y_gcell) convention under an ASYMMETRIC grid (catches
  the #7 transpose), netlist constant handling, and the #16 feature-stats honesty gate.

**Lesson:** a raw-file cross-check and a corner-case fixture suite are complementary — the
first proves the extractors match the tools on the inputs you HAVE, the second proves they
handle the inputs you might GET. A liberty fixture MUST be one-attribute-per-line (the
parser uses anchored `re.match`); a crammed pin silently drops direction/clock/cap and
tests nothing.

### 22. Verification-infra blind spots — the verifier/gates themselves lied (2026-07-07 audit; FIXED 2026-07-08)

The extractor defects #5–#21 are only caught if the *verification infrastructure* actually
fails on wrong data. A silent-lie audit of `tools/verify_graph_dataset.py` + the stats gates
+ `graph_lib.label_health` found the last line of defense had holes of its own (full report:
`docs/superpowers/plans/verifier-silent-lies-audit-2026-07-07.md`). Root cause is structural:
the chain validates *tensor == CSV* and *CSV == its own header*, but **"the label VALUES are
real numbers"** was checked only in `labels_stats.json` (which the verifier never reads;
`run_graphs.sh` uses it for mtime only), and every re-check rested on IEEE `NaN` semantics
(`abs(NaN − x) > tol` is always `False`, and `checked > 0` guards count `NaN` rows).

- **#22a (HIGH):** an all-NaN / NaN-producing **congestion or timing** label shipped fully
  green (`label_health` checked only column/row presence; `verify_y` + extended congestion
  were NaN-vacuous). Fix: `label_health` → `all_nan` status (degrades manifest); `verify_y`
  and extended congestion are now NaN-safe; wirelength/RC-ground/irdrop already had non-NaN
  backstops.
- **#22b (parked techs):** `ext.gate power` passed vacuously on asap7/gf180 — the verifier's
  liberty parser matched only scalar `cell_leakage_power`, but those techs write block-form
  `leakage_power(){value:X}`, so `lc["power"]=None` → zero comparisons. Fix: parse block-form
  leakage + require `power_checked>0`/`area_checked>0`. (asap7/gf180 parked, so low live risk.)
- **#22c (MED):** no CSV-row-count-vs-DEF check → a cleanly-truncated node CSV read `ok`. Fix:
  `ext.nodes_{gate,net,iopin} rows == DEF {COMPONENTS,NETS,PINS}`.
- **#22d (MED):** the SPEF de-escape oracle is byte-identical to the extractor's and
  `continue`d past dropped nets, so a two-sided escaping regression (the #21 class) was
  invisible. Fix: a ≥0.8 join-rate floor over escape-sensitive nets (`.`/`$`/`\[`), skipped
  on flat designs.
- **L1:** the congestion value-vs-DEF block silently skipped (no `check()`) when
  GCELLGRID/layers/DIEAREA didn't resolve → now an explicit FAIL.

Validated: pristine `iir` 106/106 (no false-FAIL); injections (NaN'd congestion, truncated
`nodes_gate`, simulated de-escape regression) flip PASS→FAIL as intended; full def-graph suite
312 passed. Regression tests: `test_label_health_flags_all_nan_label`,
`test_read_liberty_truth_block_form_leakage` (+ non-false-fire guards). **Lesson:** a verifier
check that can't fail is worse than no check — it manufactures false confidence. Any
`abs(a-b) > tol` value check must treat `NaN` as a mismatch, and any `checked>0` guard must
count only rows where a real comparison happened.

### 23. `R2G_DEF` honored by features but NOT labels — X/Y could key off different DEFs (2026-07-08)

The data contract's load-bearing invariant is *"X (features) and Y (labels) read the SAME
`6_final.def`, so rows join on `graph_id`+`inst_name`/`net_name`."* `run_features.sh` locates the
DEF via the namespaced `R2G_DEF` override first (then backend/results); **`run_labels.sh` did NOT
— it discovered the DEF/ODB ONLY from `$PROJECT_DIR/backend/RUN_*` or the live ORFS `results/`
dir, ignoring `R2G_DEF` entirely** (it honored `R2G_SPEF` for the SPEF but not `R2G_DEF` for the
DEF). Two consequences:
- A verification / override build with **no backend** (e.g. driving the extractors against a bare
  reference DEF — the nangate45 Step-5c workflow) **skipped the entire labels stage** with
  `reason:"no backend artifacts"` while features built cleanly. Surfaced by an r2g-debug tick
  building nangate45 `cordic` from `6_final.def` via `R2G_DEF`.
- The silent-value trap: when `R2G_DEF` **was** set and a backend also existed, features read the
  override DEF while labels read the backend DEF → X and Y keyed off **different** DEFs and the
  name-join silently misaligned, with no error (the exact failure class this skill exists to
  prevent; the skill even documents `R2G_DEF` as "the namespaced override ONLY").

**Fix** (`run_labels.sh`): honor `R2G_DEF` / `R2G_ODB` as the highest-priority DEF/ODB source,
mirroring `run_features.sh`; backend discovery still fills in whichever is *not* overridden, and
with both unset the production path (631-design sky130hd corpus) is byte-identical (the
`( -z "$ODB" || -z "$DEF" )` guard is true when both are empty). `R2G_ODB` pairs the ODB for the
ODB-only label (IR drop); the DEF-derivable labels (congestion, wirelength, timing via the DEF
fallback, RC via `R2G_SPEF`) work from `R2G_DEF` alone. Re-validated: nangate45 `cordic` built
from `R2G_DEF`+`R2G_ODB`+`R2G_SPEF` → 7/7 labels `ok`, full b–f dataset, `rc_health=ok`. TDD:
`test_label_stage_def_override.py` (control = no-override honest skip; experiment = override
honored → not skipped; proven RED without the fix).

### 24. Verifier assumed a backend + ignored `R2G_DEF`/`R2G_SPEF` — crash + RC false-FAIL on override builds (2026-07-08)

Fixing #23 *enabled* the nangate45 reference-DEF verification workflow to reach
`tools/verify_graph_dataset.py` for the first time — which then exposed the same "assumes a
backend, ignores the `R2G_*` overrides" blind spot in the *verifier*:
- **Hard crash:** `extended_checks` and the netlist section did a bare
  `os.listdir(case+"/backend")`, raising `FileNotFoundError` on a no-backend override project —
  **after 100+ checks had already passed** (the `os.path.isdir` guard had been applied to the two
  *other* `os.listdir` sites in the 2026-07-07 hardening but missed these two). A verifier must
  degrade to a clear SKIP when an input is legitimately absent, never abort the whole run.
- **RC false-FAIL:** the SPEF finders (`read_spef_truth`, `_spef_resistances`) globbed
  `backend/RUN_*/…` only and ignored `R2G_SPEF`, so a legitimately RC-populated override dataset
  tripped `rc: no SPEF -> rc_health=no_rc_labels`.

**Fix:** a shared `_find_spef(case)` helper that honors `R2G_SPEF` first (then the backend glob);
`extended_checks` honors `R2G_DEF` for its independent raw DEF re-parse; the two unguarded
`os.listdir` sites now use the `if os.path.isdir(...) else []` guard. All additive — with `R2G_*`
unset and a backend present (the production `--batch` path) behavior is byte-identical.
Re-validated: nangate45 `cordic` override build **156/156 checks passed**; sky130hd `--batch`
7/7 designs 164–168 each; full def-graph suite 336 passed. TDD (added to
`test_verify_comprehensive.py`): `test_find_spef_honors_r2g_spef_override`,
`test_find_spef_empty_and_rc_absent_without_env_or_backend`,
`test_extended_checks_survives_missing_backend` (proven RED without the fix). **Lesson:** when you
add an override that lets a new input shape reach a pipeline, the *verifier* for that pipeline
inherits the same override obligation — fixing the producer half-enables the workflow until the
checker honors the same knobs.

### 25. Verifier read the clock period from `updated_clks.sdc` (Fmax intermediate), not `6_final.sdc` — 100% spurious timing-label FAIL (2026-07-08)

Surfaced by an r2g-debug tick extending Step-5 coverage to a large **bus-heavy** design
(`wb2axip_axivfifo`, 103K cells). The verifier's `_sdc_clock_period` reads the clock via
`_find_backend_file(case, "results/6_final.sdc", "results/updated_clks.sdc")`, and
`_find_backend_file` is **newest-run-major** (iterate `RUN_*` newest-first; within a run try each
candidate in order, return the first hit). axivfifo had a newer *incomplete* probe run holding
**only** `updated_clks.sdc` (an Fmax-search intermediate, `period=6.2416`) and no `6_final.sdc`, so
the newer run's lower-priority candidate beat the older run's authoritative `6_final.sdc`
(`period=10.0`). The timing LABELS anchor to the run that actually has `6_final` (`run_labels`
picks the newest run with a `6_final.odb/def`), so they used 10.0 correctly — but the verifier
recomputed `Path_Delay=max(0, 6.2416−slack)` against them and reported **76986/76986 in-path
labels "bad"** (a pure false-positive; the labels were right).

**In-contract exposure:** not axivfifo-specific — every clean design carries an `updated_clks.sdc`
too (iir/aes_core/bm_sfifo all do); they pass only because their *newest* run also has
`6_final.sdc`. A clean design whose newest run lacks `6_final.sdc` (a re-run that stopped after
writing `updated_clks.sdc`) would false-fail timing identically.

**Fix** (`_sdc_clock_period`): prefer `6_final.sdc` across ALL runs before ever considering
`updated_clks.sdc` — `_find_backend_file(case, "results/6_final.sdc") or
_find_backend_file(case, "results/updated_clks.sdc")`. Targeted (the other `_find_backend_file`
caller — the ODB — is unaffected: `final/6_final.odb` vs `results/6_final.odb` are the same file).
Re-validated: `_sdc_clock_period("…/wb2axip_axivfifo")` 6.2416 → **10.0**; TDD
`test_sdc_period_prefers_6final_over_newer_updated_clks` (two-run fixture, proven RED without the
fix); full def-graph suite green. **NB** — axivfifo is an *escalated* (`drc=stuck`,
`signoff_stuck_scan`) design, out of the def-graph clean-design contract; its remaining
`signoff.drc clean` FAIL is the provenance gate working correctly (do not build training datasets
on non-signed-off designs — that dataset was removed). **Lesson:** a "find the artifact" helper
whose relnames are *priority-ordered* must honor that priority ACROSS the whole search space, not
let proximity (newest run) override rank.

### 26. eda-install pin regeneration silently DROPPED `R2G_GRAPH_PYTHON` — machine-wide graph-stage SKIP (2026-07-09)

Surfaced by the first /r2g-debug tick after the rtl-acquire ingestion: `check_env.sh` showed
`skip R2G_GRAPH_PYTHON` on a machine whose torch venv exists and was previously pinned. Root
cause chain in `eda-install/scripts/setup/write_env_local.sh`: (1) the graph pin comes ONLY from
the caller's `R2G_GRAPH_PYTHON` env or `--graph-python` — the shared `_env.sh` has **no venv
autodetection**; (2) the writer sources its OWN skill's `env.local.sh`, never the TARGETS', so an
existing pin in a target file does not survive regeneration; (3) `emit_export` silently omits
empty values. Net: any bootstrap-wide pin regeneration from a shell without the variable strips
the pin from every target (observed 2026-07-09 07:19 — signoff-loop + def-graph lost the pin
while a later `--target rtl-acquire` run kept its own). Consequence is the `graph_skipped` lie:
`run_graphs.sh` and rtl-acquire's expansion SKIP the PyG stage "cleanly" on a fully provisioned
machine. Same regeneration-clobbers-state family as the sandbox-autolearn `heuristics.json`
clobber (904fa52) and the rtl-acquire KB refresh gotcha.

**Fix** (`write_env_local.sh`): self-heal — when the caller supplies no pin, recall
`R2G_GRAPH_PYTHON` from the targets' existing `env.local.sh` (first match validated with `-x`;
an explicit `--graph-python` always wins and is never filtered), and when no pin can be resolved
emit a **loud HINT block** in the generated file ("graph stage will SKIP … `graph_skipped` is
NOT success") instead of silence. TDD (both proven RED first):
`test_write_env_local_preserves_existing_graph_pin`,
`test_write_env_local_hints_when_graph_pin_absent`; eda-install suite 26/26. Machine re-pinned
(`--graph-python /proj/workarea/user5/pyenvs/rtl2graph/bin/python` → all three targets,
`check_env.sh` `ok`). **Lesson:** a generated pin file is curated state — a regenerator that
cannot re-derive a value must RECALL it from what it is about to overwrite, and an intentionally
absent value must be loud, because the downstream stage's honest per-design SKIP aggregates into
a silent machine-wide no-op.

### 27. Test fixture positional-arg drift — stats dump leaked into pytest's CWD (2026-07-09)

`test_verify_comprehensive.test_feature_stats_json_honesty` called
`build_report(dir, DZ, "sky130hd")` against the signature `build_report(dir, OUT_PATH, design,
platform)` — the forgotten `out_path` made `"tiny"` a CWD-relative output file (a stats dump
named `tiny` appeared in the repo root on every suite run) and shifted the fixture's identity
fields to `design="sky130hd"/platform="unknown"`. The test still passed (the stats-honesty check
compares distributions, not identity fields), so the drift was invisible until the stray file
was noticed. Fixed: out_path now targets the fixture's `reports/` dir and design/platform sit in
their right slots. **Lesson:** a worker API whose 2nd positional is an OUTPUT PATH is easy to
misuse from fixtures — when a repo-root artifact appears after a test run, diff its content
against worker output formats to identify the caller.

### 28. Legacy quoted symptom ids stranded PROMOTED experience — normalization fixed writes, never the index (2026-07-09)

Found by the first /r2g-debug tick's Step-4 evidence pull: the newest `density_relief` win
carried `judged_on: "symptom:drc:'m3.2'"` (embedded quotes) while older trials read
`symptom:drc:m3.2`. The 2026-07-04 `normalize_class` fix (symptom.py) stopped NEW writes from
storing KLayout's verbatim quoted classes — but the 7 pre-fix symptom rows (`'m3.2'`,
`'GATE.S.1'`, `'M1.S.6'`, `'M4.S.5'`, `'V1.S.4'`, a quoted-whitespace class, and a 100-char
quoted LISD rule sentence; first_seen 2026-06-13..06-30) stayed in the index under their old
ids. Consequence on sky130hd m3.2: TWO live symptom ids for one physical class — the
**promoted** `density_relief` rows sat under the legacy quoted id while every new occurrence
looked up the canonical id (`get_status` → `candidate`, not `promoted`), so the promoted recipe
never fired for new runs and the A/B queue kept re-validating what was already promoted
(trials split 20 vs 7).

**Fix** (`knowledge_db.ensure_schema` → `_migrate_legacy_symptom_ids`, self-healing on any
operator's store): re-key each symptom whose class changes under `normalize_class` to its
canonical id — straight `UPDATE` for the six plain symptom_id tables (fix_events(+archive),
fix_trajectories, run_violations, escalations, ab_trials); collision-resolved merge for
`recipe_status` (judged/terminal states `promoted>demoted>parked>candidate>shadow` win; loser
row deleted); symptoms rows merged keeping the earliest `first_seen`. The remap is then applied
to the sibling `journal.sqlite` `actions.symptom_id` (best-effort) so check_db_integrity's
L1/L2 cross-book joins stay green. Idempotent; TDD RED→GREEN
`test_ensure_schema_merges_legacy_quoted_symptom_ids`. Committed store migrated: 0 quoted
classes remain, both promoted rows on canonical `m3.2`, 27 trials pooled; honesty 5/5,
integrity L1/L2/L3 ok. **Lesson:** normalizing a WRITE path without migrating the existing
index splits the memory at the normalization boundary — the promoted half goes dark precisely
because lookups moved to the new key. A key-derivation fix must ship with an index re-key.

### 29. Pin regeneration dropped ALL pin-only values (conda signoff tools + staged PDK) — sky130 signoff would silently skip (2026-07-09)

The #26 fix was symptom-level; the class bit again the same day. The 19:12 regeneration
(epoch 1783649567 backups) stripped `IVERILOG_EXE`/`VVP_EXE`/`MAGIC_EXE`/`NETGEN_EXE`
(conda `eda` env, relocated that morning to `/proj/workarea/$USER/miniconda3`) **and**
`PDK_ROOT` (`/proj/workarea/$USER/sky130_pdk/share/pdk`) from every target — only
`R2G_GRAPH_PYTHON` survived, via #26's variable-specific recall. Found by the sky130hs
/r2g-debug tick's Step-1 gate: `check_env.sh` red (`MISS IVERILOG/VVP`, PDK/magic/netgen
skip) on a fully provisioned machine. Had the campaign launched anyway, sky130 DRC/LVS
would have silently *skipped* and taught the loop a lie (the exact failure mode the Step-1
gate exists for). Two-layer root cause: (1) `write_env_local.sh` resolves through the
**eda-install** copy of `_env.sh`, and eda-install has no `references/env.local.sh` of its
own (it is not a pin target) — so values that exist ONLY as pins in the TARGETS' files
never enter resolution and are dropped on rewrite; (2) `_env.sh` autodetect had no probe
for either relocated path (`/proj/workarea/$USER/miniconda3`, `…/sky130_pdk/share/pdk`),
so the pins were load-bearing with no fallback.

**Fix** (both layers, TDD RED→GREEN): (a) `write_env_local.sh` pre-sources the first
existing TARGET `env.local.sh` before `_env.sh`, generalizing #26's recall to EVERY pin —
values enter at resolution order #1, `_r2g_detect`'s `-x` / the contains-`sky130A` gate
still drop stale pins fail-closed, and a `-d` guard stops a deleted `PDK_ROOT` from being
re-pinned forever; (b) `_env.sh` (byte-identical ×4, new md5 `9fa599b7…`) gains a shared
conda-base probe list (`+ /proj/workarea/$USER/miniconda3`) feeding tool candidates
(`<base>/envs/$R2G_CONDA_ENV/bin/{iverilog,vvp,verilator,magic,netgen}`) and a hand-staged
PDK probe (`/proj/workarea/$USER/sky130_pdk/share/pdk` et al). Tests:
`test_write_env_local_preserves_all_pins`, `test_write_env_local_drops_stale_tool_pin`,
`test_env_sh_detects_relocated_conda_tools_and_staged_pdk`; `ENV_COPIES` extended to all
four skills (rtl-acquire's copy was unchecked). **Lesson:** when a "recall what you're
about to overwrite" self-heal is added for one variable, the defect class is *the loop
that didn't recall* — patch the loop, not the variable; and every pinned path must also
be autodetectable, or the pin file is a single point of silent environmental collapse.

### 31. Crash-orphaned transient ledger states stranded designs FOREVER — round could end "ALL_DONE" with non-terminal designs (2026-07-09)

Found by the sky130hs /r2g-debug tick's Step-0 gate after a host reboot: the ledger held
8 designs in `state='flow'` with no driver alive. Every drain entrypoint
(`run`/`fmax-drain`/`ab-drain`) selects ONLY `state='pending'`, and
`campaign_resume_waves.sh`'s ALL_DONE gate counted ONLY `pending` — so a driver killed
mid-wave (reboot, `kill -9` without the group, OOM) left its in-flight designs in a
transient state (`flow`/`signoff`/`fixing`) that nothing ever re-drained. Worst case is
the LAST wave: pending hits 0 while N designs sit transient → the driver prints
`ALL_DONE platform=… pending=0` and the round is reported complete over designs that
never reached a terminal state (a campaign-completion lie the DB honesty gates cannot
see — those designs may have ingested nothing at all).

**Fix** (TDD RED→GREEN): `Ledger.reclaim_orphans()` resets every transient entry to
`pending` (appending an `orphan_reclaim:<state>` event, dropping stale `judged` per the
add()/reload invariant so a re-run A/B arm is RE-judged), called at the start of
`run()`, `fmax_drain()` and `ab_drain()` — safe exactly there because the per-ledger
single-instance guard (flock + pgrep, 2026-07-04) proves no live worker can own a
transient state at command start. `campaign_resume_waves.sh`'s `pending_count()` now
counts OPEN work (`pending|flow|signoff|fixing`), so a crash on the final wave triggers
one more wave (which reclaims) instead of a false ALL_DONE. Tests:
`test_reclaim_orphans_resets_transient_states`,
`test_run_drains_crash_orphaned_designs` (test_engineer_loop.py). **Lesson:** an
append-only state machine drained by state-equality needs a crash-recovery sweep for
every worker-owned intermediate state; "resumable ledger" only held for the states a
command actually selects.

### 30. Campaign platform re-point could key an EXISTING dataset to the wrong platform's libs — build + verify trusted the mutable config.mk (2026-07-09)

Surfaced by the sky130hs round bootstrap: Step 1b (`setup_rtl_designs.py --platform
sky130hs --force`) rewrites `constraints/config.mk` for the WHOLE corpus — including
designs like `iir` whose backend (`run-meta.json: platform=sky130hd`) and built dataset
are sky130hd. Both the def-graph stage scripts (`run_labels/run_features/run_graphs`
derive `$PLATFORM` from config.mk) and `tools/verify_graph_dataset.py`
(`resolve_platform_files` read config.mk) would then resolve the OTHER platform's
liberty/LEF for an existing DEF. `cell_type_id` and every `*_type_id` vocabulary are
per-platform, so this is the skill's signature silent-value mode: a rebuild produces
plausible CSVs with wrong categorical ids; verification either false-FAILs a good
dataset or — subtler — vacuously passes checks against the wrong ground truth.

**Fix**: authority order is *explicit arg > build provenance > config.mk* everywhere.
(a) Stage scripts consult the discovered backend's `run-meta.json` via the SHARED
`scripts/flow/_provenance.sh` (one copy — a worker-local inline guard is the techlib
drift mode; an explicit platform arg skips it, preserving reference-DEF overrides);
(b) `build_graphs.py --platform` stamps the platform into `graph_manifest.json` at
build time; (c) the verifier's `_platform_provenance` prefers manifest > newest
`run-meta.json` > config.mk, and passes the result to the resolver as a make
command-line var (which overrides config.mk's export inside ORFS). Tests:
`test_platform_provenance.py` (shell guard + verifier + wired-once drift guard);
corner fixture now stamps + asserts `manifest.platform`. **Lesson:** any artifact
keyed to per-platform vocabularies must carry its own build-time provenance; a
campaign that re-points shared mutable config for the NEXT round silently re-keys
every artifact of the PRIOR round that resolves through it.

### 32. sky130hs had NO KLayout DRC deck wired — ORFS "DRC not supported" (exit 0) filed as a phantom design DRC failure (2026-07-09)

First sky130hs campaign wave: every design's DRC came back
`status=failed, reason=no_count_report, exit_code=0` and the fixer burned catalog
iterations (`recheck_unparsed` → `catalog_exhausted` escalations) on a violation load
that never existed. Root cause: `run_drc.sh`'s main path defers to ORFS `make drc`,
whose recipe is gated on `ifneq ($(KLAYOUT_DRC_FILE),)` — and this ORFS checkout sets
that variable only for sky130hd (`platforms/sky130hs/` has no `drc/` dir at all), so
the else-branch echoes "DRC not supported on this platform" into `6_drc.lyrdb` and
exits 0. The skill's Platform Support Matrix promises sky130hs DRC=Yes (KLayout), so
this was a missing wiring, not an honest platform truth (contrast: asap7 LVS).

**Fix**: `run_drc.sh` resolves a **sibling-tech deck** for sky130hs — the
`sky130hd.lydrc` deck is pure sky130A process-layer geometry (dnwell/nwell/metal/via
rules from the SkyWater tech docs; zero hd-specific content), and hd/hs are the same
sky130A tech — passed as `KLAYOUT_DRC_FILE=` on the make command line (parse-time
conditional ⇒ the real KLayout run fires). Deliberate pair only; a missing sibling
deck WARNs and keeps the loud no_count_report path (never a silent skip).
**Lesson:** a support-matrix "Yes" must be backed by an executable deck resolution on
that platform, and an exit-0 "not supported" echo from a vendor flow is a *phantom
symptom generator* — classify infra absence apart from design failure before the
fixer spends iterations.

### 33. sky130hs def2stream DROPPED all DEF geometry — GDS with labels only ⇒ portless magic extraction ⇒ 100% false Netgen LVS "top pin mismatch" (2026-07-09)

Same wave: every sky130hs design failed Netgen LVS with
`Final result: Top level cell failed pin matching` (`mismatch_count=null`,
`top_pin_mismatch`) while the SAME designs are LVS-clean on sky130hd. Diagnosis chain:
extracted.spice top subckt had ZERO ports → the 6_final.gds top cell held ONLY text
labels (met2/met3/met5 texttype 5) — no routing (69/20, 70/20), no pin rects (x/16),
no special routing — while the 6_final.def carried full routed wires + placed pin
geometry. The ORFS merge log was clean: `def2stream` dropped every DEF-derived shape
SILENTLY. Root cause: `platforms/sky130hs/sky130hs.lyt` still carries the LEGACY
KLayout lefdef reader option names with wrong datatypes (`<routing-suffix>` /
`<routing-datatype>0`, `<pins-suffix>` / `<pins-datatype>2`) and lacks
`<produce-special-routing>`, while sky130hd.lyt was rewritten to the modern names
(`<routing-suffix-string>.drawing` + `<routing-datatype-string>20`,
`<special-pins-…>16`, `<special-via_geometry-…>44`). KLayout 0.30.x ignores the legacy
names — an upstream ORFS hd/hs platform-parity gap, present unmodified in this checkout.

**Fix** (three layers, TDD): (a) `tools/patch_sky130hs_lyt.py` ports sky130hd's modern
`<lefdef>` block into sky130hs.lyt (keeping the hs layer-map; idempotent; `--check`
exits 2 while unpatched; backup `.orig`; re-run after any ORFS update); (b) the
`run_netgen_lvs.sh` guard: a PORTLESS top-level extraction (`_spice_top_ports.sh` = 0)
is written as json `status:"error"` ("GDS lost DEF geometry") and never reaches Netgen
— the extractor then reports `error`, not `mismatch`, so the learner is never taught a
false design symptom; (c) re-merge + re-LVS of affected designs. Validated on
Control_logic: patched merge restored 69/20:120 + 69/16:19 + met3/4/5; Netgen →
**CLEAN — circuits match**. Tests: `test_sky130hs_gds_geometry.py` (patch semantics,
idempotence, --check gate, port counting incl. `+` continuations).
**Lesson:** a signoff verdict is only as honest as the artifact chain that feeds it —
guard each hand-off (DEF→GDS→SPICE) with a cheap structural invariant (geometry
present, ports > 0), because a vendor config regression upstream converts every
downstream check into a plausible, ingestible lie.

### 34. Dataset stages built on ANY `6_final.def` — DRC-dirty / LVS-mismatched / route-residual designs sailed into training data (2026-07-10)

The only precondition all three def-graph stages enforced was "does a
`6_final.def` exist". DRC/LVS run in a SEPARATE post-finish step, route/antenna
residuals survive a "completed" flow, and `orfs_status` lives in ppa.json that
no flow step emits by default — so a design that completed ORFS but was never
signed off (or failed signoff) produced a plausible dataset whose manifest read
`status:"ok"`. The verifier's Group-C DRC/LVS gate was fail-OPEN: it fired only
`if os.path.isfile(reports/{drc,lvs}.json)` — a design that never ran signoff
passed VACUOUSLY. Batch builders selected designs purely by finding a 6_final;
the "filter by drc/lvs=clean" rule was an operator convention living in memory.

**Fix** (def-graph `scripts/flow/signoff_gate.py`, one shared copy — same rule
as `_provenance.sh`): before building, read `reports/{drc,lvs,route}.json` +
the DEF-run's `stage_log.jsonl`/`run-meta.json`. Required (fail-closed —
MISSING = blocked): drc ∈ {clean, clean_beol}, lvs ∈ {clean, skipped}, ORFS
complete, route residuals 0 when provable. Advisory (recorded, never blocks):
timing (negative slack is a legitimate training label). `run_graphs.sh`
enforces by default; `run_labels.sh`/`run_features.sh` default to warn;
`R2G_SIGNOFF_GATE=enforce|warn|off` overrides; an explicit `R2G_DEF` override
downgrades to warn (deliberate, recorded). The verdict is always written to
`reports/signoff_gate.json` and embedded in `graph_manifest.json` as
`signoff_health`; the verifier now FAILS a dataset whose provenance is
unrecorded (no reports AND no gate verdict) or whose gate verdict is dirty.
Tests: `def-graph/tests/test_signoff_gate.py` (26 cases incl. the vacuous-pass
regression). **Lesson:** an artifact's *existence* is not its *validity* —
every consumer of a multi-step pipeline must gate on the recorded verdict of
the steps between, and a missing verdict is a "no", never a "yes".

### 35. Every fix iteration rebuilt synth→finish — `config.mk` is NOT a make prerequisite, so a plain resume silently NO-OPed the edit (2026-07-10)

`run_orfs.sh` with no `FROM_STAGE` always ran `clean_all` (full rebuild), and
`engineer_loop._run_fix` never passed `--resume` — so every fix iteration paid
a complete synth→finish rebuild even when diagnose declared `rerun_from:
"route"`. The trap that made the full rebuild "necessary": ORFS's Makefile
lists NO stage dependency on `DESIGN_CONFIG`/config.mk, so resuming
`FROM_STAGE=route` over intact artifacts made `make route` a NO-OP and the
just-applied config edit silently never took effect — plain `--resume` was
only sound for crash-resume, never for applying an edit.

**Fix**: `run_orfs.sh` now runs `make clean_<FROM_STAGE>` before a resume
(after the stage-name validity guard), forcing exactly the resumed stage — and,
via the odb dependency chain, everything downstream — to rebuild while every
earlier stage's artifacts are REUSED. `R2G_RESUME_NO_CLEAN=1` restores the
pure crash-resume (unchanged config, e.g. the finish-stage GDS resume).
`fix_signoff.sh` now resumes from the strategy's `rerun_from` BY DEFAULT
(`--resume` is a no-op alias); `R2G_FIX_FULL_REFLOW=1` restores the full
rebuild for edits that affect a stage earlier than the declared rerun_from.
Tests: `test_antenna_nonconverged.py` (FROM_STAGE default + kill switch +
invalidation-after-guard). **Lesson:** artifact reuse is only safe when
invalidation is EXPLICIT — a build system that doesn't know about your config
file will happily "reuse" its way into never applying your fix.

### 36. Antenna repair looped diodes+reroute with no improvement exit — the same 1–2 residuals burned full reflows forever (SHA-1/SHA-256, 2026-07-10)

ORFS's inner DRT loop (`detail_route.tcl`) re-inserts diodes and re-runs FULL
detailed route up to `MAX_REPAIR_ANTENNAS_ITER_DRT` times gated only on
`[check_antennas]` — no improvement check. OpenROAD's antenna model can
disagree with the signoff deck (jumpers satisfy PAR; the KLayout deck sums
whole-net metal and credits only diodes), so the same 1–2 violations can
survive every round. Each outer antenna strategy then cost a full reflow, and
NOTHING persisted the futility — every later fix session (each engineer_loop
visit) re-burned the same diode+reroute rounds on the same residual.

**Fix** (`fix_signoff.sh`): after 2 non-improving antenna iterations (strategy
id `antenna*` or dominant class matching *antenna*) the iteration verdict
becomes the terminal **`antenna_nonconverged`** (ingested as `no_change` —
negative evidence, not inconclusive), the loop STOPS, and
`reports/antenna_nonconverged.json` persists {residual_count, strategies_tried,
hint}. Later sessions auto-exclude the proven-futile strategies (loud NOTE);
`R2G_FIX_RETRY_NONCONVERGED=1` retries deliberately (e.g. after a toolchain
update); the marker self-clears the moment the check reaches CLEAN. Tests:
`test_antenna_nonconverged.py`. **Lesson:** a bounded loop is not a converging
loop — any repair cycle needs (a) an improvement-based exit, (b) a persistent
record that it didn't converge, or an autonomous driver will re-discover the
same dead end at full price forever.

### 37. Wave driver's single-instance guard false-matched its OWN launching shell — refused every `sleep`-and-confirm relaunch (2026-07-11)

Found by the sky130hs /r2g-debug tick after a host reboot killed the wave-3 driver:
relaunching `campaign_resume_waves.sh` the documented way exited *immediately* with
`ERROR: another campaign_resume_waves.sh is already running (pgrep)` — while `pgrep`
showed no driver alive. The driver's own single-instance guard (2026-07-04 audit H1,
failure-patterns #31's cousin) used an **un-anchored** `pgrep -f "campaign_resume_waves\.sh"`,
which matches on the full command line of *any* process — including the operator's
launching shell, whose `-c` command literally contains the driver path (the
/r2g-debug Step 2 block runs `setsid bash tools/campaign_resume_waves.sh`). When that
launching shell OUTLIVES the guard check — precisely the natural `... & sleep N; pgrep`
confirm-it-came-up pattern — `pgrep` returns the launcher's PID, `grep -vw "$$"` sees a
PID that isn't the driver's own, and the guard concludes a rival is running and exits 1.
Fire-and-exit launches (no trailing `sleep`) slipped through only because the launcher
died before the guard ran — a latent race, not a working guard. The DB honesty gates
cannot see this: they were all green while the round sat dead-in-the-water since the
reboot, because "the driver never started" is invisible to a store that only records
runs that happened.

**Fix** (`tools/campaign_resume_waves.sh`, TDD RED→GREEN): END-ANCHOR the pattern —
`pgrep -f "campaign_resume_waves\.sh$"` matches only a process EXEC'd on the script
(cmdline ends in the script name), never a launching shell whose `-c` string merely
mentions the path (trailing redirects/commands push the launcher's cmdline past `.sh`).
`$PPID` (the launcher) is additionally excluded for the residual case of a launcher
whose cmdline ends exactly at the script name; a real rival driver is never a child's
parent, so this can never mask a true double-launch, and the robust per-ledger `flock`
remains the primary guard underneath. This is the SAME end-anchoring the operator-side
guard and the /r2g-debug Step 0 note already prescribe ("pgrep is END-ANCHORED —
un-anchored -f false-matches your own shell"); the driver's internal guard simply hadn't
adopted it. A `R2G_GUARD_SELFTEST=1` hook runs the guard in isolation (report + exit
before any wave work) so it is unit-testable. Tests: `test_campaign_driver_guard.py`
(`test_guard_ignores_self_mentioning_launcher_shell` reproduces the false-positive;
`test_guard_still_detects_a_real_second_driver` proves end-anchoring keeps catching a
genuine rival). **Lesson:** a `pgrep -f` liveness guard must match on the process's
*exec identity* (anchored path), not on any command line that happens to *name* it — or
the very launch command that mentions the tool becomes indistinguishable from a second
copy of it, and the guard blocks the thing it was meant to protect.

### 38. Codex robustness-suggestion audit — 5 latent bugs + per-metric/observability hardening (2026-07-12)

An audit of 7 Codex robustness suggestions against the *actual* code (full grading in
`docs/superpowers/plans/2026-7-12-codex-suggestion.md`) confirmed the 2026-07-10 sweep had
already shipped the big items (risk-flag screening #1-partial, one-click promote #2,
antenna auto-exit #36/#4, signoff gate #34/#6, stage-scoped reflow #35/#3) but surfaced
**five latent bugs** and four **partial** gaps. All closed here with tests. Each is a
distinct sub-mode; they share the trait that the surface behavior looked fine.

**(a) `fix_signoff.sh` antenna_noimp was CUMULATIVE, not consecutive.** The
`antenna_nonconverged` auto-exit (#36) counts non-improving antenna strategies, but the
counter was only ever incremented — never reset on an *improving* antenna iteration
(unlike the generic `noimp` beside it). A design converging via interleaved wins and
no-ops (10→5 win, 5→5 no-op, 5→3 win, 3→3 no-op) hit `antenna_noimp==2` at the 2nd
*cumulative* no-op and was falsely declared non-converged despite clear 10→3 progress —
a premature over-abort that escalates a design another iteration might have cleared. Fix:
reset `antenna_noimp=0` on every improving antenna iteration (consecutive semantics).
Test: `test_antenna_nonconverged.py::test_converging_antenna_not_declared_nonconverged`
drives the interleaved sequence and asserts it reaches CLEAN, not a marker.

**(b) `build_diagnosis.py` synth-error scan was un-scoped → false-positive diagnosis.**
`parse_synth_errors` flagged any `ERROR`/`Error:` line, but `main()` fed it the FULL
concatenated log text (flow.log + drc/lvs/rcx/route logs), so a `[ERROR GRT-…]` routing
line or an LVS-mismatch line was mislabeled a *synthesis* error — a wrong-lever diagnosis
in a big failed run. Fix: scope it to the `synth.log` section only (a new `section_text`
helper), exactly as the DRC (#8) and make-error (#9) checks already scope; genuine synth
failures still fire via their dedicated signatures (empty_synthesis, make_error, …).
Test: `test_build_diagnosis.py` (route/LVS ERROR lines produce NO synthesis_errors;
a real synth.log error still does).

**(c) `signoff_gate.py` `_check_route` trusted the status string over the count.**
`if st == "clean" or tv == 0` short-circuited: a foreign `route.json` with
`status="clean"` but `total_violations>0` read clean, and a genuine `status="unknown"`
(route stage never reached) was mislabeled `dirty` (a spurious blocker). Fix: gate on the
COUNT (`tv==0` → clean, `tv>0` → dirty regardless of status), map `unknown`→unknown
(caveat). Tests in `test_signoff_gate.py`.

**(d) `promote_candidates.py` wrote the manifest BEFORE the `--run` flow outcome.**
`reports/promote.json`/`metadata.json` were dumped at step 6, then the optional `--run`
block (step 7) flipped `result["status"]` to `promoted_flow_failed` only in memory — the
on-disk manifest kept `status="promoted"`, so a later reader trusted a manifest that
missed the flow failure. Fix: re-dump both after the `--run` block.
Test: `test_promote_candidates.py::test_run_flow_failure_updates_on_disk_manifest`.

**(e) `run_expansion_round.py` high-mem round guard scanned ALL rows pre-filter.** The
`resource_tier=high` guard blocked the entire round if *any* CSV row was high-mem, even a
row the `--priorities` filter would drop (e.g. `priority=low` while running `--priorities
high`) — so a high-mem candidate that was never going to run stalled the round with
`blocked_high_mem`. Fix: `runnable_high_mem_designs(rows, priorities)` mirrors the
expander's `--priorities` filter and only counts rows that would actually run.
Test: `test_candidate_deferral.py::ResourceGuardScopeTests`.

**Feature-securing (partial gaps closed, all additive):**
- **Antenna is now its OWN gate dimension** (codex #5). `signoff_gate._check_antenna`
  reads `antenna_nonconverged.json` + antenna-named `drc.json` categories and records
  antenna as clean/fail/nonconverged/not_covered/unknown — decoupled from routing-DRC, so
  a routing-clean-but-antenna-dirty design is visible in `signoff_health`. A caveat, never
  a new blocker (a full-deck antenna fail already blocks via `drc`).
- **Graph SKIP manifests carry the SPECIFIC upstream reason** (codex #6).
  `def-graph/scripts/flow/graph_skip_manifest.py` threads the antenna-nonconvergence
  marker / ORFS `orfs_fail_stage` / signoff blockers / newest `stage_log.jsonl` failing
  stage into `graph_dataset.json`'s `upstream` object instead of a bare "no 6_final.def".
- **ORFS resume provenance** (codex #3). `run_orfs.sh` stamps per-stage `ts_start`/`ts_end`
  + output `artifact` into `stage_log.jsonl` (additive — the `{stage,status,elapsed_s}`
  contract is preserved for every reader), tees the reuse/rerun decision to `flow.log` with
  its concrete `R2G_RERUN_REASON` (supplied by `fix_signoff.sh`), and writes
  `resume_meta.json` (reused stages + reason).
- **Consolidated run summary** (codex #7). `build_diagnosis.py` now emits a `run_summary`
  unifying stage durations (`stage_log.jsonl`) + repair repetitions (`fix_log.jsonl`) +
  DRC/LVS/route/timing status — the single structured summary the suggestion asked for.
- **Low-priority deferral queue** (codex #1). `expand_candidates.py` stable-sorts
  risk-flagged / `resource_tier=high` candidates to the tail of the round (a `risk_deferred`
  stage marker makes it observable), so a memory-heavy design runs AFTER the clean ones
  instead of blocking them; `--no-defer-risky` opts out. (The deeper static analysers —
  inferred-memory-bit estimate, module-dependency-completeness tagging — remain a documented
  follow-up: they need an HDL array-size parser.)

**Lesson:** a shipped robustness feature can still lie at the edges — a counter that never
resets (a), a parser fed the wrong scope (b), a status string trusted over its own count
(c), a manifest written before the outcome it claims (d), a guard applied before the filter
that bounds it (e). Audit the *edges* of "already done" features, not just their happy path.

### 39. Skill-relocation left stale absolute `POST_GLOBAL_PLACE_TCL` hook paths in 84 A/B-arm config.mks — place aborted "couldn't read file", mislabeled `unseen_crash` (2026-07-12)

Found by the sky130hs /r2g-debug tick: 8 escalations under reason `unseen_crash`, all on
`8_bit_Microcontroller_*_pdn_die` A/B **arm** dirs. Their backend `flow.log` showed the
real cause — global place aborted because ORFS `source`d a stage hook that no longer
exists:

```
source .../r2g-rtl2gds/scripts/flow/orfs_hooks/buffer_port_feedthroughs.tcl
Error: global_place.tcl couldn't read file ".../r2g-rtl2gds/.../buffer_port_feedthroughs.tcl": no such file or directory
make: *** [do-3_3_place_gp] Error 1 ; ERROR: Stage 'place' failed (exit code 2)
```

The 2026-07-07 skill split moved the tree `r2g-rtl2gds/` → `r2g-skills/signoff-loop/`, so
the **absolute** `export POST_GLOBAL_PLACE_TCL = .../r2g-rtl2gds/.../buffer_port_feedthroughs.tcl`
baked into config.mk was orphaned. Primary designs were regenerated with the new path
(`setup_rtl_designs.py` re-writes primary config.mk each round), but **84 pre-split A/B-arm
config.mk copies were not** — an arm's config.mk is cloned from the primary at ab-launch and
then re-used verbatim across rounds. When ab-drain re-ran those arms, place aborted on the
dead hook; the crash classifier had no pattern for a missing-hook `source` failure, so it
fell through to the terminal `unseen_crash` bucket. Compounding harm: **an arm that dies on a
dead hook path never diverges**, so the `pdn_die` recipe's A/B evidence on those subjects was
silently starved — the loop burned 8 escalations learning nothing. All DB honesty gates stayed
green (the crash *was* honestly recorded; it was just under-classified and self-inflicted).

**Fix** (`run_orfs.sh`, TDD RED→GREEN + one-time data migration):
- **Self-heal at the choke point.** Before copying config.mk into the ORFS design dir,
  `_heal_hook_paths` scans for any `export *_TCL = <path>` whose file is MISSING and repoints
  it to the same-basename file under the script's canonical `orfs_hooks/` sibling
  (`$(dirname BASH_SOURCE)/orfs_hooks`). It is **conservative** — a path that resolves is left
  untouched (even outside `orfs_hooks/`), and a dead path with no same-basename match is left
  in place with a loud WARNING (never blanked). Atomic in-place (`mktemp`+`mv`) so a
  concurrently-spawned reader never sees a truncated config.mk. This fixes all 84 arms lazily
  as they re-run AND any future skill relocation, for primaries and arms alike, at the single
  point every flow passes through.
- **One-time migration** of the 84 on-disk arm config.mks via the same tested code path
  (`R2G_SELFTEST_HEAL_HOOKS` hook) so inspection/provenance is clean immediately.
- Tests: `test_run_orfs_hook_heal.py` — dead→canonical repoint, valid-path-untouched,
  no-canonical-match-warns-not-blanks, non-`_TCL`-lines-untouched, idempotency, and
  `test_default_hooks_dir_resolves_real_canonical` (proves the default `BASH_SOURCE`-based
  `HOOKS_DIR` resolves to the REAL hook in a clean driver-spawned bash — an interactive
  shell's `cd`-that-lists hook, which pollutes `$(cd…&&pwd)`, is absent in production).

**Lesson:** an absolute path baked into generated config is a time bomb across any repo/skill
relocation — the generator gets fixed, but every *copy* made before the move keeps pointing at
the grave. Resolve tool-relative resources from the running script's own location, and make the
runner **self-heal a moved-but-still-present resource** rather than trusting a stored absolute
path. And when a crash class is a catch-all (`unseen_crash`), a *cluster* of it on one strategy
is a lead, not noise: the true cause was one grep of the arm's `flow.log` away.

### 40. `setsid timeout` defeated timeout's process-group kill — a TERM-ignoring tool (klayout DRC) outlived its 2h timeout and hung the whole campaign for 6+ hours (2026-07-12)

Found by the sky130hs /r2g-debug tick: the campaign made **zero progress for 6+ hours** — one
143K-cell design (`fpga_fft_verilog_butterfly_top_module_16_point`) sat in `fixing` while 508
designs waited, `run` age 6h50m, load ~1. The fixer had already recorded `stop_residual` on its 64
DRC violations, yet a KLayout DRC process was still **Running at 2h13m elapsed — past the 2h
`ORFS_TIMEOUT`** — with `PPID=1` (orphaned) but `PGID=`the timeout's PID, while the `timeout`
wrapper itself was **gone**. The orphaned DRC held the stage's stdout pipe open, so the `tee`
reader never saw EOF and `run_orfs.sh` (and behind it the whole driver) blocked forever. All DB
honesty gates stayed green — a store only records runs that *happen*, and this one never returned.

Root cause — `run_orfs.sh` ran each stage as:

```bash
setsid timeout --signal=TERM --kill-after=60 "$ORFS_TIMEOUT" bash -c "$MAKE_CMD $stage" 2>&1 | tee -a flow.log
```

GNU `timeout` reaps the **whole child tree** by forking the command into a NEW process group and
signalling `-pgid` — but **only when it is not itself a process-group leader**. The `setsid`
prefix (added under the mistaken comment "so timeout can kill the entire process group") makes
`timeout` a session/group leader, so its `setpgid(0,0)` fails and it falls back to signalling only
its **direct** `bash -c` child. On a stage that actually hit `ORFS_TIMEOUT`, that meant: SIGTERM →
`make` (klayout, an EDA tool that **ignores SIGTERM**, keeps running); SIGKILL (`--kill-after`) →
`make` again (never the tool). `make` dies, klayout orphans (still in the timeout's group, but the
group was never signalled) and runs until the heat death of the campaign. The bug only bites when a
stage genuinely exceeds `ORFS_TIMEOUT` — i.e. only on giant designs — which is why it lay latent
until a 143K-cell FFT.

**Fix** (`run_orfs.sh`, TDD): **drop the `setsid`**. Plain `timeout` (not a group leader) forks the
command into a new group and group-kills it, so the `--kill-after` SIGKILL reaches the whole tree —
a SIGTERM-ignoring tool included (SIGKILL cannot be ignored). This also *improves* exit-status
fidelity (no more `| tee` PIPESTATUS ambiguity is introduced; the pattern is otherwise unchanged).
Immediate remediation: killed the orphaned DRC process group (`kill -KILL -<pgid>`) — the design
then escalated `catalog_exhausted` in seconds and the wave advanced to ab-drain. Tests:
`test_run_orfs_timeout_reaping.py` — a static guard that `setsid timeout` never returns as a
command, plus a behavioral test that plain `timeout` reaps the whole stage tree (no orphaned
grandchildren survive).

**Lesson:** a stage timeout is a lie if it doesn't reap the tool it bounds. `timeout`'s tree-kill
is silently disabled the moment `timeout` becomes a process-group leader — so **never wrap
`timeout` in `setsid`** (or any group-leader-making construct). And the honesty DBs are blind to a
*hang*: "the flow never returned" leaves no row, so a frozen ledger + a live tool process past its
timeout is a first-class alarm, invisible to `honesty.py`. When a campaign flat-lines, look for a
process older than `ORFS_TIMEOUT` with `PPID=1` before assuming "legit slow."

### 41. A CTS-stage crash (TritonCTS segfault) fell through to `unseen_crash` — the classifier had no cts branch (2026-07-12)

Found by the sky130hs /r2g-debug tick: `unseen_crash` ticked 8→9, and the 9th was a NEW primary
design (`i2c_master_i2c_master`, escalated post-#39-fix), distinct from the 8 `pdn_die` A/B arms.
Its `flow.log` showed the true cause — a **TritonCTS segfault** at clock-tree synthesis:

```
cts::TritonCTS::initOneClockTree(...) → ... → make[1]: *** [do-4_1_cts] Error 245
ERROR: Stage 'cts' failed (exit code 2) after 11s
```

The `process_one` crash classifier has honest stage-specific branches (synth memory/timeout/missing-
header, place FLW-0024/PPL-0024, floorplan PDN-strap, route) that refine the line-1044 `unseen_crash`
default — but **no `cts` branch**. So every cts-stage crash fell through to the catch-all, telling the
learner/operator a *recognizable tool crash* was a *novel mystery* (the exact misclassification the
2026-06-28 unseen_crash audit set out to eliminate). cts crashes are a real if small class (3 in the
committed store, vs synth 122 / place 83 / route 73 / floorplan 12). NB the knowledge side was already
honest — `runs.orfs_fail_stage='cts'` + an `orfs-fail-cts-*` event — so honesty.py stayed green; only
the *ledger escalation reason* was imprecise.

**Fix** (`engineer_loop.py` + `escalations.py`, TDD): add an `elif _fail_stage(entry) == "cts"` branch
that labels the abort `cts_crash` with an honest note. It is a **label, not a recovery** — a TritonCTS
`initOneClockTree` segfault is an OpenROAD internal crash the loop cannot fix by re-config, so it
escalates as a distinct, groupable class (no speculative re-flow). **Critically**, `cts_crash` had to be
added to `escalations.REASONS` — the systemic `test_all_loop_emitted_reasons_are_registered` guard (and
runtime `open_escalation`) reject an unregistered reason with a ValueError that *crashes the worker* and
buries the honest reason under `worker_exc:ValueError` — the exact latent-crash class that bit
place_density_residual/synth_memory_residual/pdn_strap_residual/incomplete_missing_header/synth_timeout
before. Tests: `test_escalations.py::test_cts_crash_is_valid_reason` (registration) +
`test_cts_crash_branch_present_in_classifier` (the branch stays). Suite 827 passed / 2 skipped.

**Lesson:** the crash classifier is only as honest as its stage coverage — a stage with no branch
silently collapses into `unseen_crash`, and every new honest reason is a worker-crash landmine until it
is registered in `escalations.REASONS`. Add the branch AND the reason together, and let the systemic
registration guard prove they agree.

### 42. `build_diagnosis` reported `kind:none` for a backend stage abort/timeout with no `make` error line (codex-debug 2026-07-13 #4)

Found by the 2026-07-13 codex-debug audit (raw findings + full grading:
`docs/superpowers/plans/2026-07-13-codex-debug.md`). `build_diagnosis.detect_issues()` is entirely
**text-log driven** (17 signature rules over `flow.log`/`synth.log`/`6_lvs.log`/…). A stage killed at
`ORFS_TIMEOUT` — the failure-patterns #40 class — is SIGKILLed by `timeout`, so `flow.log` often has
**no `make: *** Error` tail line** for the `make_error` rule to catch. Every text rule then misses, and
`main()` fell straight through to the `kind:none` fallback ("No known failure signature detected") — even
though the very same file's `build_run_summary()` had already computed `run_summary.signoff.orfs_status='fail'`
+ `orfs_fail_stage` from `ppa.json`. So `diagnosis.json`'s top-level `kind` (and the dashboard diagnosis
panel keyed on it) rendered blank for a *real* backend abort.

**Honesty note (why this was cosmetic, not a learner lie):** `ingest_run.py` derives `orfs_status`/`fail_stage`
from `stage_log.jsonl` **independently** of `diagnosis.json`, and writes the `orfs-fail-<stage>-<errcode>`
`failure_event` from that — it builds `failure_events` *solely* from `diag['issues']`, never the top-level
`kind`. So the learner was never blind (honesty.py stayed green); only the human/dashboard summary was
uninformative.

**Fix** (`build_diagnosis.py`, TDD). `main()` now builds `run_summary` **before** the `kind` decision; on
an empty `issues` list `_orfs_fallback_kind(run_summary)` consults `signoff.orfs_status` and emits a
stage-named `kind` — `orfs_stage_failed` (status `fail`, e.g. `orfs_fail_stage='finish'` ⇒ the reviewer's
"route completed but finish missing") or `orfs_stage_incomplete` (status `partial`) — instead of `none`.
Crucially it leaves `issues: []` (presentation-layer only, so ingest fabricates **no** duplicate
failure_event). Also `build_run_summary` now echoes the terminal `reports/antenna_nonconverged.json`
verdict into the summary (read-only echo; the marker is already the source of truth for `signoff_gate.py`
+ the ingest fix-event). Tests: `test_build_diagnosis.py` (`_orfs_fallback_kind` fail/partial/clean units;
`main()` route-pass+finish-timeout ⇒ `orfs_stage_failed` w/ `issues:[]`; regression: clean run stays
`kind:none`; antenna echo). Suite 833 passed / 2 skipped, honesty 5/5.

**Phantom findings from the same audit — DO NOT re-chase** (full evidence in the plan doc):
- **"`finish` re-runs tapcell/place via a `2_4` vs `2_3` tapcell filename mismatch"** — WRONG. In this ORFS
  checkout `scripts/tapcell.tcl` writes `2_3_floorplan_tapcell.odb` and `Makefile` consumes the same name;
  `2_4_floorplan_tapcell` does not exist (`2_4` is the *pdn* step). `run_orfs.sh` runs **explicit per-stage
  make targets** in a loop, and `clean_finish` removes only `6_*`, so `finish` cannot rebuild upstream. No
  `finish_rerun_previous_stages` / `orfs_target_output_mismatch` diagnosis rule was added — it would fire on
  a non-existent condition.
- **"Route artifacts lost if a later stage hangs (copy only after all stages finish)"** — WRONG. ORFS writes
  `5_route.*`/`6_final.*` **in-place** into `results/…` as each stage completes; the end-of-flow
  `backend/RUN_*/` copy is a redundant archive reached even on failure/timeout, and every downstream consumer
  (`run_rcx.sh`/`run_drc.sh`/`run_lvs.sh`/`_restage_for_signoff.sh`/def-graph) reads the in-place dir. No
  per-stage snapshot was added.
- **"Antenna repair retries forever"** — ALREADY HANDLED (#36 + #38a): the consecutive-no-improvement detector
  (`antenna_noimp>=2 ⇒ antenna_nonconverged`) halts cleanly and the marker rides gate + DB + tests.
- **"Move the runtime knowledge store to a gitignored dir"** — WRONG FIX (breaks the tracked shipped-store
  invariant D14; the 2026-06-23 bundle-as-source migration was already tried and reverted). The real, unrelated
  pollution was `tools/_*_resume_logs/` (~370 MB of campaign wave logs) — now gitignored; `.gitattributes`
  marks the churning `knowledge.sqlite` blob `binary` (cross-operator sharing stays via `knowledge_sync.py`).

**Lesson:** an external reviewer sees *symptoms* from outside and infers *causes* it cannot verify — grade
each finding against the actual code before acting. Here 1 of 5 instance-findings was a real (cosmetic)
gap; the other 4 were phantom causes or already-shipped features whose "fixes" would have added dead code
that lies. The `kind:none`-over-a-known-fail-stage gap is the general trap: when two code paths in the same
file compute the same fact (text rules vs the stage ledger), reconcile them so the human-facing summary
can't disagree with the structured one.

### 43. `analyze_execution` spoke a stale MemoryStore dialect — string-only status + wrong `orfs` recipe key (2026-07-13 memorystore audit #1/#2)

Found by the 2026-07-13 MemoryStore/A-B evidence-chain audit
(`docs/superpowers/plans/2026-07-13-memorystore-audit.md`). Two components that MUST agree drifted apart:
the *canonical* ingest path evolved, the *backend analyzer* did not.

- **Integer/bool status blindness.** `analyze_execution._derive_status()` compared each stage's `status`
  against the strings `"pass"`/`"fail"` only. The production writer `run_orfs.sh` records the shell **exit
  code** as an int (`"status": 0`), so `0 == "pass"` was always False, every stage was skipped, and EVERY
  run — clean, aborted, or synth-only — collapsed to `partial` with no `fail_stage`. This is the identical
  class `ingest_run._norm_stage_status` already fixed on the canonical side; the analyzer just never got the
  memo. 10 of 13 sampled historical stage logs classified differently between the two. It was also blind to
  `flow_scope`, so a synth-only rtl-acquire run read `partial` even though it passed within its declared scope.
- **`orfs` vs `orfs_stage` key drift.** A backend-stage abort recipe is keyed by canonical ingest under
  `check='orfs_stage'` (the STAGE as the class), and the learner writes it under
  `fix_recipes["orfs_stage"][<stage>]` — 91 such records in the shipped store. But `rank_proposals()` read
  `fix_recipes["orfs"][<stage>]` (the legacy `orfs` family is keyed by the literal `"full"`, not a stage), so
  the lookup could NEVER hit: every stage recipe was unreachable and the ranker silently cold-started. Worse,
  `analyze()` never CALLED `rank_proposals()` at all, so the 91 `orfs_stage` recipes had no live reader.

**Fix** (`analyze_execution.py`, TDD). `_derive_status` now DELEGATES to `ingest_run._derive_orfs_status`
(single normalizer; honors `flow_scope` read from the parsed `config.mk`), so the two paths can't drift again.
`rank_proposals` + a shared `_load_stage_recipe` read the canonical `orfs_stage` family with a legacy `orfs`
fallback, and `analyze()` now attaches `learned_stage_ranking` (the ranked `orfs_stage` recipe for the failing
stage) to its output — the operator triage tool finally CONSUMES the recipes. Advisory only; never auto-applied.
Tests: `test_analyze_execution.py` (integer-fail ⇒ `fail`; all-zero ⇒ `pass`; synth-only scope ⇒ `pass`),
`test_analyze_ranking.py` (canonical `orfs_stage` key consumed; `analyze()` surfaces the ranking). The live
DRC/LVS ranking (`diagnose_signoff_fix.py`) already consumes symptom-indexed memory — this fix is scoped to the
backend-stage triage reader, not generalized to the whole agent.

**Lesson:** when two components must agree on a decision (here: "did this stage pass?" and "where do stage
recipes live?"), give them ONE source — a shared normalizer + a shared key schema — never a copied
re-implementation. A copy that drifts doesn't crash; it silently misclassifies down the whole evidence chain.

### 44. Trajectory rollup lost partial wins and cross-symptom credit (2026-07-13 memorystore audit #8)

Same audit, `learn_heuristics._build_trajectory`. The per-episode Tier-2 rollup had two attribution bugs:

- **Partial wins recorded `abandoned`.** The episode outcome enum was `resolved | abandoned | not_attempted`
  and `win` (the trajectory-level "did it clear?") only matched `verdict == "cleared"`. A real partial
  improvement (`verdict == "win"` — a genuine violation reduction, already half-credited at the STEP level by
  `_recipes_from_trajectories`) had nowhere to go and fell into `abandoned` with `winning_strategy=NULL`,
  erasing the improving strategy from trajectory-level evidence. 46 episodes in the shipped store were affected.
- **Cross-symptom merge.** Trajectories grouped by `(fix_session_id, check_type)` and attributed the WHOLE
  path to the FIRST event's symptom. A session that shifts symptom mid-episode (m1 spacing cleared, then m3
  spacing surfaces) credited the clearing strategy to the wrong symptom — polluting the cross-platform
  symptom-indexed memory. 250 sessions in the store genuinely span >1 symptom.

**Fix** (`learn_heuristics.py` + `schema.sql` + `knowledge_db.py`, TDD). Added an `improved` outcome: no full
`cleared` but ≥1 `win` ⇒ `outcome='improved'` with the winning strategy preserved (kept strictly BELOW
`resolved` so the strict-`resolved` consumers — `eval_heuristics`, `observe` (trace), `build_strength_report`
— never mistake a partial win for a full clear). Grouping + the `fix_trajectories` PK now include `symptom_id`
`(fix_session_id, check_type, symptom_id)`, so each symptom yields its own trajectory. `fix_trajectories` is a
PURE re-derivable projection (`learn()` DELETEs + rebuilds it), so `_migrate_drop_stale_fix_trajectories` drops
a legacy-PK copy (zero data loss) for recreation — idempotent once the new PK is in place. A shared
`_resolve_event_symptom` keeps grouping + per-episode symptom resolution consistent. Tests:
`test_learn_heuristics.py` (partial win ⇒ `improved`+winner; multi-symptom session splits per symptom),
`test_learn_fix.py` (s2 partial-win assertion corrected from `abandoned` to `improved`). Rebuilt store:
`improved:46` (all with a winner), 250 sessions split; honesty 5/5.

**Lesson:** an outcome enum too coarse to name a real state (partial improvement) doesn't drop the state —
it mislabels it as its nearest neighbor (`abandoned`), which then lies to every downstream reader. And a
grouping key coarser than the thing being learned (symptom) silently cross-contaminates the learned index.

### 45. A/B trials + fix_events shipped with NULL provenance — no run_ids, no tool versions (2026-07-13 memorystore audit #7/#10)

Same audit. Promotions could not be traced to the evidence or the toolchain that produced them:

- **`arm_a_run_id`/`arm_b_run_id` hardcoded `None`.** `engineer_loop.judge_finished_trials` called
  `record_trial(..., arm_a_run_id=None, arm_b_run_id=None)` literally, so all 376 `ab_trials` stored NULL run
  ids — a decisive `win`/`loss` could not be pinned to two distinct arm runs, defeating experiment-level dedup
  and replay. The run ids were RIGHT THERE: each arm entry carries a `project_path`, and `_arm_metric` already
  SELECTs the arm's `run_id` — it just wasn't returned or passed through.
- **`tool_versions_json` had no writer.** The `fix_events` column existed since the symptom-index migration
  but NOTHING ever populated it — 100% of historical events had a null toolchain fingerprint.

**Fix** (`engineer_loop.py`, `ab_runner.py`, `ingest_run.py`, new `knowledge/tool_versions.py`, TDD).
`_arm_metric` now returns `run_id`; `judge_finished_trials` resolves each arm's first judgeable `run_id`,
passes them, stamps the full per-repeat lists + `provenance_complete` + `tool_versions` into `metrics_json`.
`record_trial` stamps `provenance_complete` when absent and WARNS loudly on a DECISIVE verdict lacking distinct
run ids (the case that would otherwise promote a recipe on unverifiable evidence — it does not refuse the write,
since a real inconclusive/one-armed trial still carries information). `tool_versions.collect()` is a cached,
fail-safe collector (missing/hanging tool ⇒ null, never raises; `R2G_TOOL_VERSIONS_JSON` overrides for
reproducible re-ingest/tests) fingerprinting openroad/yosys/klayout + ORFS/agent git HEADs; `ingest_run` stamps
it onto every fix_event (`COALESCE` on re-ingest so a first fingerprint is never clobbered). Tests:
`test_ab_runner.py` (provenance_complete true/false; decisive-without-run-ids warns), `test_ingest_fix_events.py`
(tool_versions stamped). Old trials keep their NULL run ids — mark them `provenance_incomplete` on audit rather
than trusting them as current evidence.

**Lesson:** an evidence table without a back-reference to the runs that produced it is unfalsifiable — it can
neither be reproduced nor refuted. Provenance (run ids + tool fingerprint) is not metadata polish; it is what
makes a promotion an experiment instead of a claim.

### 46. rtl-acquire dual-memory honesty check read an empty projection as convergence (2026-07-13 memorystore audit #10)

Same audit, `project_frontend_diagnosis.check_honesty`. The fast honesty check asserted `count(synth-fail
runs) == count(with a frontend failure_event)`. For an EMPTY projection (no `synth_only` runs at all) this is
`0 == 0` — a vacuous pass that read as "dual memory converged / healthy", masking a missing source or an
unpopulated shared projection. An empty set proves nothing; it is not a pass.

**Fix** (`project_frontend_diagnosis.py`, TDD). `check_honesty` now also reports COVERAGE (total `synth_only`
runs) and treats the empty set as UNPROVEN: it prints a `COVERAGE EMPTY … convergence is UNPROVEN (an empty
set is NOT a pass)` caveat, and a new `--check --require-nonempty` makes the empty set a hard failure (exit 2,
distinct from 0=consistent and 1=real violation). Default stays exit 0 for backward compatibility (a
signoff-only checkout with no rtl-acquire activity must not spuriously fail), but the message no longer claims
convergence. Test: `test_flow_scope_ingest.py` (empty projection ⇒ default 0 + `COVERAGE EMPTY`; strict ⇒ 2).

**Lesson:** an equality honesty gate (`A == B`) is trivially satisfied by the empty set. Any convergence check
must also assert non-empty COVERAGE, or "nothing measured" is indistinguishable from "everything agrees".

### 47. RTL2Graph_v3 reference alignment — raw-label twins + num_drivers no-fill + LEF pin geometry; 4 reference bugs NOT ported (2026-07-14)

A fresh `RTL2Graph_v3` reference drop ("updated after debugging") was compared subsystem-by-subsystem against
`def-graph`. Result: on correctness the reference is **behind** ours (it never absorbed our 2026-07 silent-value
fixes), while it made three deliberate changes worth adopting. Three were adopted; four reference bugs were
reported and deliberately NOT aligned backward.

**Adopted from the reference (TDD, regenerated cordic sky130hs → verifier 204/204):**
- **Raw-label twins.** The reference switched its label extractors to the raw "EDA-Schema / CircuitNet" value.
  Rather than replace our normalized targets, we now emit BOTH: `data.y_raw` / `edge_y_raw` / `rc_edge_y_raw`
  mirror the normalized tensors slot-for-slot with the raw physical value (sourced from the label CSVs' raw
  columns, which already existed). A trainer picks either convention with no regen. Verifier gates: shape==,
  NaN-parity per slot, and the clean `log1p` identities (wirelength y4, ground cap y5, coupling/resistance edges).
- **`num_drivers` force-fill removed** — see #47-adjacent note in this section / CLAUDE.md. The extractor no
  longer fabricates `num_drivers=1` (which also corrupted `num_sinks`); a parse-miss now honestly reads 0. The
  verifier's `>= 1 on ALL nets` assert (depended on the fill) was relaxed to `>= 1 on SOME net`.
- **LEF pin-center geometry** — `techlib.lef.macro_pin_geometry` + `apply_orient` place each pin at its true
  orientation-aware in-cell center, so `hpwl_um` / `pin_x/y_std_um` are real geometry (matters for macros).
  `run_features.sh` now exports `SC_LEF`/`ADDITIONAL_LEFS`; the verifier reproduces pin-center HPWL with an
  independent geometry parse. Empty cell LEF ⇒ instance-origin fallback (old behavior).
  - **`apply_orient` FN/FS swap (code-review catch).** The initial port carried the RTL2Graph original's
    transposed FN/FS: FN returned MX `(x,H-y)` and FS returned MY `(W-x,y)` — the reverse of DEF/OpenDB
    (FN=MY reflect-X, FS=MX reflect-Y). FS is the alternating-row vertical flip = **~half of all std cells**
    (cordic: 2488/5105; aes_core ~49%), so `hpwl_um`/`pin_x/y_std_um` were wrong for every net touching a
    flipped cell. Worse, the verifier's `_v_apply_orient` AND the unit test replicated the *same* swap, so
    the firewall was illusory and the build verified 204/204 green. Fixed by swapping FN↔FS, then **validated
    against OpenDB placed pin locations** (cordic: FS=MX matched 2488/2488) — the only true oracle for
    orientation math. **Lesson:** an "independent" re-derivation copied from the same source is NOT
    independent; validate geometry/transform math against the tool's own output, and encode the oracle's
    values (not a hand-derivation) in the test.

**Reference bugs found (present in `RTL2Graph_v3`, already fixed in ours — DO NOT "align" ours toward the
reference here, it would REGRESS us):**
1. **Congestion vertical-demand transpose** — the reference keys vertical demand `(y,x)`; ours forces `(x,y)`
   (the 2026-07-05 ~79.7%-wrong defect). The reference's new standard-RUDY congestion is *also* transposed.
2. **Wirelength/congestion don't strip RECT patch metal** — inflates length ~100–400× on RECT-bearing DEFs
   (sky130); ours uses `techlib.def_parse.route_segments`.
3. **Timing TCL joins STA name to ODB name without de-escaping** — drops every bus-named register
   (`slack=INF`); ours joins on a backslash-stripped canonical name.
4. **c/d/e/f `build_directed_edges` misalignment (our "bug #5")** — the reference concatenates `edge_index` as
   `[all-fwd | all-rev]` but `repeat_interleave`s the attrs pairwise, misaligning `edge_attr`/`edge_y` for every
   folded edge past the first; ours interleaves `[fwd,rev]`. (The reference kept this even after its debugging.)

**Lesson:** an upstream "debugged" reference is a *hypothesis*, not ground truth. Diff it subsystem-by-subsystem,
separate genuine improvements (adopt) from its own bugs (report, never port). Where the fork already fixed a
class of defect, aligning naively re-introduces it.

### 48. Real-DEF correspondence guards went INERT — pinned RUN dirs rotted, and the oracle drifted behind a production fix (2026-07-19 /r2g-debug Step 5c)

**Symptom.** `def-graph/tests/test_techlib_def_parse.py` reported "13 passed, 4 skipped" — green. The 4 skips
were its only tests that touch a REAL DEF: the correspondence guards proving `route_segments` reproduces the
wirelength and congestion walks. They had been skipping for months on a machine holding **3858** usable
`6_final.def` files.

**Root cause (two failures stacked).**
1. **Pinned campaign paths rot.** The DEFs were hardcoded to `design_cases/aes_core/backend/RUN_2026-04-12_18-04-55`
   and `cordic/backend/RUN_2026-05-17_05-58-40`. A `backend/RUN_<timestamp>/` is *campaign output* — wiped,
   re-run and re-dated constantly (and `design_cases/` is gitignored, so CI never sees it). Both pins were
   deleted; `pytest.skip("DEF absent (machine-local)")` fired and the suite stayed green. **The skip was
   indistinguishable from a pass** — the exact failure `/r2g-debug` warns about ("never trust a SKIP as a pass").
2. **The oracle drifted behind a production fix — masked by (1).** The congestion side compares against an
   inline copy of the *pre-consolidation* congestion walk. The 2026-07-05 RECT-patch fix (defect #2 above)
   deliberately changed `route_segments` to strip `RECT ( dx dy dx dy )` patch groups, and `route_segments`'
   own docstring says the sky130hd behavior "intentionally *diverges* from the originals (they were wrong)".
   Nobody updated the inline oracle — because the pins had ALREADY rotted before that fix landed, so the test
   that would have caught the drift never ran. Re-activating the guards surfaced it immediately:
   `NEW met1 ( 641700 522750 ) RECT ( -365 -70 0 70 )` → oracle expected the phantom point
   `(641700, 522750, -365, -70)`, `route_segments` correctly yielded `[]`.

**Not a production defect.** `extract_congestion.py` and `extract_wirelength.py` BOTH import
`techlib.def_parse.route_segments`, so the shipped extractors were always correct; only the test's frozen
copy encoded the old buggy semantics.

**Resolution.** DEFs are now RESOLVED, never pinned (`_resolve_def`): prefer the purpose-built machine-local
reference DEFs (`rtl2graph_verify/{aescore_sky,cordic_ng45}_*.def` — stable, one per platform family, not
campaign output), else glob the newest built `design_cases/*/backend/RUN_*/{final,results}/6_final.def`; skip
only when NEITHER exists. The congestion oracle gained an **independently written** RECT stripper
(`_strip_rect_patches`, a token scan — deliberately not def_parse's single regex, so it stays a second opinion
that disagrees if `_ROUTE_RECT_RE` ever mis-anchors). Parametrize ids corrected (they had the platforms
backwards: it is aescore=sky130, cordic=nangate45). Result: **17 passed, 0 skipped**, both platform families
actually exercised.

**Still inert, deliberately left alone:** `test_techlib_crossplatform.py`'s baseline gate skips for the same
reason, but *self-announces* with its remedy ("run `tools/regen_extract_baseline.sh` to restore") — the honest
form of this bug. Its remedy is however ALSO stale: that script pins the same two deleted RUN dirs. Re-pinning
would change a byte-for-byte baseline guarding a long-completed migration (the techlib restructure), so it is
an explicit operator decision, not a drive-by fix.

*Generalizable rule: a test input that lives under campaign output must be RESOLVED at run time, and a skip
that can fire silently is a guard you no longer have. Prefer a loud, self-announcing skip that names its
remedy — and re-check that the remedy still works.*
