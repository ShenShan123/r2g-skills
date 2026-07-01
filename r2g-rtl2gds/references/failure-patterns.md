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
- **Symptom:** Magic DRC script fails or produces no output
- **Common causes:**
  - sky130A tech file missing at `$PDK_ROOT/sky130A/libs.tech/magic/sky130A.tech`
    (set `PDK_ROOT` in `references/env.local.sh`; `/opt/pdks` is only the fallback default)
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
trigger: {check: drc, class: METAL1_ANTENNA, platform: nangate45}
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

## ASAP7 residual-DRC-by-design — `asap7.lydrc` is NOT flow-achievable-clean (deck-vs-flow truth, 2026-06-30)

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
(nangate45 router-DRC / sky130hd Magic DRC), where the store's genuine promotions already live. The 12
asap7 `drc=clean` rows that existed were fabrications (arm copytree inherited the nangate45 subject's clean
`drc/`+`reports/` before the 2026-06-30 copytree-exclude-stage-dirs fix) — reconciled out; genuine asap7
`drc=clean` count = 0.

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
the fake promotion never reverts. FIX: `knowledge/reconcile_ab_verdicts.py` re-derives each verdict
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
