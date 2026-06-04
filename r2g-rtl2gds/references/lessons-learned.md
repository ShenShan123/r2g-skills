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

## Full-Corpus Validation (70 designs → 7 families) — 2026-04-03

Re-ran the 70-design batch after the bp_multi_top fixes landed (DIE_AREA up to 2000x2000 for SYNTH_HIERARCHICAL+ABC_AREA configs, LVS auto-timeout scaling, FROM_STAGE resume).

| Family | Configs | ORFS | LVS | RCX | All-Pass | Notes |
|--------|---------|------|-----|-----|----------|-------|
| aes_xcrypt (aes128_core) | 10 | 10/10 | 10/10 | 10/10 | **10/10** | Medium design, ~35 min/config |
| ibex (ibex_core) | 10 | 10/10 | 10/10 | 10/10 | **10/10** | Large design, ~20 min/config |
| riscv32i (riscv_top) | 10 | 10/10 | 10/10 | 10/10 | **10/10** | Macro design (fakeram45_256x32), CDL fix |
| tinyRocket (RocketTile) | 10 | 10/10 | 10/10 | 10/10 | **10/10** | Macro design, CDL fix |
| vga_enh_top | 10 | 10/10 | 10/10 | 10/10 | **10/10** | Memory inference, ~60 min/config |
| swerv (swerv_wrapper) | 10 | 10/10 | 10/10 | 10/10 | **10/10** | ~145K cells, LVS auto-scaled 4200s |
| bp_multi_top (black_parrot) | 10 | 10/10 | 10/10 | 10/10 | **10/10** | ~200K cells, LVS auto-scaled 7200s |

**Result:** 70/70 designs through ORFS+LVS+RCX. Never run two configs with the same DESIGN_NAME and FLOW_VARIANT concurrently. Never run multiple LVS jobs concurrently for >100K-cell designs (3-5GB RAM each, 2-3x wall-time inflation under memory pressure).

## Full-Corpus Validation (682 designs) — 2026-05-27

The 682-design corpus state after the May 2026 sweeps. Establishes baseline expectations for nangate45 on this OpenROAD/ORFS install.

| Signoff      | Count        | Notes                                                   |
|--------------|--------------|---------------------------------------------------------|
| ppa.json     | 682 / 682    | every design with a backend run has PPA                 |
| DRC clean    | 381 (55.9%)  |                                                         |
| DRC stuck    | 232 (34.0%)  | known FreePDK45.lydrc polygon-op deadloop; GDS still valid |
| DRC fail     | 29           | actual violation counts in `reports/drc.json`           |
| LVS clean    | 582 (85.3%)  | after FreePDK45.lylvs deployment (commit c5770d5)       |
| LVS unknown  | 72 (10.6%)   | large designs whose deep-mode netlist phase didn't finish under the 1h cap |
| LVS fail     | 16 (2.3%)    | real netlist mismatches                                 |
| RCX complete | 681 / 682    | all but boom_smallseboom (intractable at route)         |

**Key lessons from this campaign:**
- Upstream ORFS doesn't ship a nangate45 LVS rule; the skill now bundles one at `r2g-rtl2gds/assets/platforms/nangate45/lvs/FreePDK45.lylvs`. Install with `tools/install_nangate45_lvs.sh`.
- `extract_lvs.py` used to prefer the `lvs/lvs_result.json` skip-marker over real logs, masking 529 successful LVS runs as "skipped". Now the marker is ignored when a real `6_lvs.log`/`lvs_run.log` is present (commit c5770d5).
- `_restage_for_signoff.sh` makes DRC/LVS idempotent from preserved `backend/RUN_*/`. Without it, the `find … -name 6_final.gds` fallback silently picked up unrelated designs' GDS when DESIGN_NAME collided (59 of 454 use `DESIGN_NAME=top`).
- DRC stuck classification must trust `stuck_at_rule` regardless of exit code — observed 124 (timeout), 137 (SIGKILL), 2 (make-target failed) for the same polygon-op pattern depending on how klayout was killed.
- `3_4_place_resized`'s `repair_design` is a DIFFERENT hang from `3_3_place_gp`'s timing-driven iteration. PLACE_FAST=1 only fixes the latter. No ORFS knob currently skips `repair_design` at place stage; arm_core hit this 2026-05-26 and is intractable here.
- boom_mediumboom and boom_mediumseboom recovered to GDS via `FROM_STAGE=route ROUTE_FAST=1 ROUTE_FAST_DRT_ITERS=1` resuming from preserved 5_2_route.odb / 6_1_fill.odb. Always check preserved scratch before declaring a route-stuck ChipTop intractable.

## nangate45 Antenna DRC — From "Inert/Unfixable" to Real Diode Repair (2026-06-02)

The single largest fixable DRC class in the corpus is nangate45 METAL*_ANTENNA. Two earlier
campaigns gave up on it: the 2026-05-30 sweep *relaxed* the deck 300→400 (masking, since
retired), and 2026-06-01 Finding B declared it an honest "residual" because OpenROAD's
`repair_antennas` was inert. Re-investigating, the inertness had **three** distinct causes in
the stock nangate45 LEFs — fixing only one (as prior attempts implicitly assumed) does nothing:

1. **tech LEF has no antenna ratios.** `NangateOpenCellLibrary.tech.lef` has zero `ANTENNA*`
   keywords, so `check_antennas` has no threshold and reports 0 — nothing to repair.
2. **SC LEF has gate areas stripped.** ORFS uses `NangateOpenCellLibrary.macro.mod.lef` as
   `SC_LEF`; its std-cell pins have **no `ANTENNAGATEAREA`** (the full model survives in the
   sibling `.macro.lef`). Without a gate area there is no metal/gate ratio — `check_antennas`
   finds 0 even at ratio 1. This was the non-obvious one; the tech-LEF fix alone is useless.
3. **Diode unusable.** `ANTENNA_X1` has `ANTENNADIFFAREA 0.0`; OpenROAD only uses a
   `CORE_ANTENNACELL` when `diffArea > 0` (RepairAntennas.cpp:559) → `GRT-0246 No diode found`.

Fix: `tools/install_nangate45_antenna.sh` (patcher `scripts/flow/antenna_lef_patch.py`) adds
`ANTENNAAREARATIO 300` per routing layer (matches the signoff deck — not a relaxation), merges
the per-pin gate areas from `.macro.lef`, and sets a positive diode `ANTENNADIFFAREA`. With the
model in place OpenROAD's per-net PAR equals KLayout's ratio to the decimal (stream_register
488.80 vs 489.17).

**The decisive trap — jumpers vs diodes.** With the model installed, OpenROAD's *default*
repair "fixes" the antenna with a **jumper** (layer hop): its PAR drops below 300 and it prints
`Found 0 antenna violations`, but KLayout *still flags it* (stream_register only improved
489→472). The FreePDK45 `antenna_check(gate, metalN, 300, diode)` sums the **whole net's** metalN
area connected to the gate — a jumper doesn't reduce that — and credits **only a connected
diode** (`#adiodes`/`#diode_factors`). So the working strategy disables jumper repair
(`SKIP_ANTENNA_REPAIR=1`) and forces physical diode insertion (`MAX_REPAIR_ANTENNAS_ITER_DRT=10`).
stream_register: 489:1 → **DRC CLEAN** with one inserted `ANTENNA_X1`. Codified as the
`antenna_diode_repair` strategy in `diagnose_signoff_fix.py`; KLayout 300:1 signoff deck never
touched. **General principle:** when an OpenROAD in-flow checker and the KLayout signoff deck
disagree on whether a fix worked, the signoff deck wins — and you must use the repair *modality*
the deck actually credits, not the one the flow finds cheapest.

## Dead Learning Loop — Repaired (2026-06-04)

The knowledge store's learn→suggest loop had been shipping **zero** learned config:
`heuristics.json` was `"families": {}`. Root cause was a definition mismatch, not missing data.
`learn_heuristics._is_success` required `orfs_status == 'pass'`, but **0 of 750** runs had it
(747 `partial`, 3 `unknown`) because `ingest_run._derive_orfs_status` only marks `pass` when all
six stage names appear in an often-incomplete `stage_log.jsonl`. Meanwhile the real signoff signal
sat unused: **607 LVS-clean, 417 DRC-clean (+264 `clean_beol`), 699 RCX-complete**.

**The fix went in the learner, not ingest.** The plan's primary suggestion was to relax
`_derive_orfs_status`, but that would (a) require re-ingesting 750 project dirs and (b) make
`orfs_status` lie about the stage log. Instead we added a shared `knowledge_db.is_success(row)`:
strict 6-stage pass **OR** relaxed (≥1 *positive* clean signoff — LVS `clean` / `symmetric_matcher`,
DRC `clean` / `clean_beol`, RCX `complete` — **and** no failed signoff; absence of all signoff data
is *not* success). `learn_heuristics.py` and `monitor_health.py` both import it, so the learner and
the health monitor can never drift. Re-running `learn_heuristics.py` against the existing DB —
no re-ingest needed — took **heuristics.json from 0 → 48 learned family/platform pairs** (631 runs
now learnable). Commits `356d517` + `7d429ac` (branch `fix/dead-learning-loop`).

**Guardrails that mattered:**
- The CLAUDE.md `PLACE_DENSITY_LB_ADDON ≥ 0.10` floor was *named but not actually enforced* in
  `suggest_config` — added it as a hard post-filter (defense-in-depth; never fires on current data
  where learned medians are all 0.20, but protects future learned medians). Verified every learned
  `place_density` median ≥ 0.10 (all 0.20 on the current corpus) and learned
  `core_utilization` medians span 10–25 (e.g. aes_xcrypt 15, riscv32i 17.5) — all safe,
  since CU has no floor (a lower CU is the conservative, routable choice).
- The `bus_heavy` CU→15 clamp already protects against family-median pollution: `axi_crossbar`
  inherits the `axi`-family learned median of 25 but is still clamped to 15 (verified before/after).
  Safety rails beat empirical medians — so `families.json` curation could stay conservative.
- `families.json` curation used **anchored** `^prefix_` patterns so it doesn't silently swallow
  future designs (`spider` → `spider`, not `spi`); ambiguous run-together names fall through to the
  honest `split('_')[0]` singleton fallback. The fuzzy/Jaccard family fallback was **rejected**
  (name-token similarity ≠ design behavior; poisons safety-critical medians).

**Two follow-ons absorbed the same observability lens (read-only projections over our own
structured outcomes):**
- *Observability* (`feat/knowledge-observability`, `a9cdf26` + `5c8833b`):
  `scripts/reports/build_lineage_view.py` is a `mode=ro`, deterministic projection over
  `runs.sqlite` + `config_lineage` + `heuristics.json` → two dashboard index panels (a health strip
  that would have screamed "747/750 partial, heuristics empty", and a tuning-provenance table). It
  reuses `is_success` so the health numbers match the learner, and is **never** wired into
  `suggest_config`.
- *Payoff A/B harness* (`feat/heuristics-payoff-eval`, `c49c52c` + `39b55f2` + `8984fed`):
  `knowledge/eval_heuristics.py` `emit`s paired naive/learned arms (via a new `suggest_config
  --no-learned`) and `summarize`s them into a deterministic `eval_summary.json`. **Honest cost:**
  the flow's `stage_log.jsonl` captures only wall-clock `elapsed_s` — CPU-hours/peak-RAM are *not*
  recorded anywhere, so the harness reports wall-clock, records `cost_metric`, and never fabricates
  CPU-hours (forward-compatible to `cpu_s`/`peak_rss_kb`). A `win` requires a *usable* signed-off
  learned arm that is also cheaper; cheaper-but-both-fail is `inconclusive`. The frozen `eval_set`
  excludes bus families on purpose — their `bus_heavy` clamp forces CU=15 in both arms, masking the
  learned difference. The multi-hour A/B *run* is operator-driven; the harness is built + unit-tested
  against fixtures.

**General principle:** when a self-improvement loop ships nothing, suspect the *success definition*
before the data — here the outcomes existed all along; the gate was reading the wrong column.

## Fmax Search — Post-Place Timing Is Optimistic vs Signoff (2026-06-04)

`scripts/reports/fmax_search.py` roots on **placement-stage** worst setup slack
(`detailedplace__timing__setup__ws`, falling back to `floorplan__timing__setup__ws`) as a cheap
proxy for signoff Fmax — each probe runs only `ORFS_STAGES="synth floorplan place"` instead of a
full place-route-finish-signoff flow. The whole approach hinges on one corpus fact: **post-place
timing is systematically optimistic relative to signoff**, so the raw proxy Fmax would over-promise
unless corrected.

**Where the gap actually lives — placement, not routing.** Decomposing the slack drop across stages
(`d_fp_pl` = floorplan→place delta, `d_pl_fin` = place→final delta) on the corpus shows:

- **Placement is the dominant gap:** `d_fp_pl` p90 ≈ **0.41 ns** — most of the optimism is baked in
  between floorplan and the end of placement (buffering, legalization, detailed-placement timing
  repair changing the picture vs the floorplan estimate).
- **Routing is ≈ neutral:** `d_pl_fin` median is **negative** — going from post-place to final
  signoff does *not*, on the median design, eat further slack; routing/CTS/final-opt roughly break
  even. The big, predictable correction is the place-stage one, which is exactly the stage the proxy
  reads, so a per-family deterioration offset learned at place is a sound predictor for the median
  design.

This is why the search corrects the proxy with a learned **per-family slack-deterioration model** and
labels its output **predicted (UNVERIFIED)** rather than signed-off, and why `--verify` runs exactly
one full flow at the winning period to confirm and feed the model back.

**Archetypes where the proxy lies (median ≠ tail).** The "routing is neutral" result is a *median*
statement; specific archetypes blow the tail and make the place-stage proxy optimistic:

- **Congestion / route-limited** designs — routing detours + added buffers erase slack the proxy
  reported (see `failure-patterns.md` → "Routing Congestion (GRT-0116)").
- **Macro / CTS-skew-dominated** designs — clock-tree skew and macro-pin access aren't modeled at
  place; the proxy can't see them (see the macro placement / CTS sections).
- **Hold cliffs** — hold violations are essentially invisible at the place stage and only surface
  after CTS/route, so a period the proxy "closes" can still fail signoff on hold.

For these, treat a `predicted`/`proxy-only` label as a loose upper bound and prefer `--verify`.

**Model provenance:** the deterioration model is **nangate45-backfilled** (seeded from the existing
nangate45 corpus, which has the most signed-off runs); **other platforms are forward-learned** — the
per-family offset accumulates only as verified runs land for those platforms, so early non-nangate
predictions lean on conservative defaults until the model fills in.
