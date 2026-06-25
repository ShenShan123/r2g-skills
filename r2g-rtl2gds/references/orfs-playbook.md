# ORFS Playbook

Use ORFS only after RTL syntax checks, testbench simulation, and Yosys synthesis have all passed successfully.

## Environment Setup

Nothing to source manually. Every flow script sources `scripts/flow/_env.sh`, which autodetects the
ORFS checkout (`ORFS_ROOT`/`FLOW_DIR`) and the tool binaries (`OPENROAD_EXE`, `YOSYS_EXE`,
`KLAYOUT_CMD`, `MAGIC_EXE`, `NETGEN_EXE`, `STA_EXE`, `IVERILOG_EXE`/`VVP_EXE`/`VERILATOR_EXE`,
`PDK_ROOT`/`SKY130A_DIR`) from the PATH and well-known install locations. Verify what was discovered
with `scripts/flow/check_env.sh`.

Override autodetection only when needed — via `$R2G_ENV_FILE`, `references/env.local.sh`, or by
exporting the variable before the script runs. Optional override example:

```bash
export ORFS_ROOT=/opt/EDA4AI/OpenROAD-flow-scripts
export OPENROAD_EXE=/usr/bin/openroad
export YOSYS_EXE=/opt/pdk_klayout_openroad/oss-cad-suite/bin/yosys
export KLAYOUT_CMD=/usr/bin/klayout
```

## ORFS Root

The autodetected checkout (`$ORFS_ROOT`; a typical layout shown below):

```
$ORFS_ROOT/   # e.g. /opt/EDA4AI/OpenROAD-flow-scripts or ~/OpenROAD-flow-scripts
├── flow/
│   ├── Makefile          # Main flow driver
│   ├── platforms/        # PDK configurations
│   │   ├── nangate45/    # Default, fastest for testing
│   │   ├── sky130hd/
│   │   ├── sky130hs/
│   │   ├── asap7/
│   │   ├── gf180/
│   │   └── ihp-sg13g2/
│   ├── designs/          # Design configurations
│   └── scripts/          # ORFS internal TCL scripts
```

## Inputs to Prepare

1. **config.mk** - Design configuration
2. **constraint.sdc** - Timing constraints
3. **RTL file(s)** - Verilog source (absolute paths)

## config.mk Template

```makefile
export DESIGN_NAME = my_design
export PLATFORM    = nangate45

export VERILOG_FILES = /absolute/path/to/design.v
export SDC_FILE      = /absolute/path/to/constraint.sdc

# Optional: directories searched for ``include`` files. Required when the
# RTL uses `` `include "header.vh" `` and the headers live in a different
# directory than the source (e.g., Faraday DMA's `DMA_DEFINE.vh`).
export VERILOG_INCLUDE_DIRS = /absolute/path/to/rtl

export CORE_UTILIZATION = 30
export PLACE_DENSITY_LB_ADDON = 0.20
```

ORFS reads Verilog with `read_verilog -defer -sv`, which enables SystemVerilog mode.
That means RTL using SV-reserved words (`int`, `bit`, `logic`, `byte`, ...) as port or
wire names will fail at the canonicalize step with `syntax error, unexpected TOK_INT`
(or similar). Run `scripts/project/validate_config.py <project-dir>` first — it
detects reserved-keyword identifiers in both port and `wire`/`reg`/`logic` declarations
so you can rename them before kicking off the flow.

## constraint.sdc Template

```tcl
current_design my_design

set clk_name  core_clock
set clk_port_name clk
set clk_period 10.0
set clk_io_pct 0.2

set clk_port [get_ports $clk_port_name]
create_clock -name $clk_name -period $clk_period $clk_port

set non_clock_inputs [all_inputs -no_clocks]
set_input_delay  [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]
```

## Running ORFS

### Via Script (Recommended)
```bash
scripts/flow/run_orfs.sh <project-dir> [platform]
```

### Manually
```bash
cd /opt/EDA4AI/OpenROAD-flow-scripts/flow
make DESIGN_CONFIG=/path/to/config.mk
```

**Important:** Always pass `DESIGN_CONFIG` as a make argument, not an environment variable. The Makefile has a hardcoded default (gcd) that would override an env var.

## ORFS Output Directories

After a successful run:
- `results/<platform>/<design>/base/` - Final outputs (GDS, DEF, ODB, SPEF)
- `logs/<platform>/<design>/base/` - Stage logs
- `reports/<platform>/<design>/base/` - Timing, area, power, DRC reports
- `objects/<platform>/<design>/base/` - Intermediate objects

## Running Signoff Checks

After a successful ORFS backend run, run signoff checks using the ORFS results in-place:

### DRC (Design Rule Check)
```bash
scripts/flow/run_drc.sh <project-dir> [platform]
# or manually:
cd /opt/EDA4AI/OpenROAD-flow-scripts/flow
make DESIGN_CONFIG=/path/to/config.mk drc
```
- Invokes KLayout with platform-specific `.lydrc` rule file
- Outputs: `reports/<platform>/<design>/base/6_drc.lyrdb`, `6_drc_count.rpt`
- Script copies results to `<project>/drc/`

### LVS (Layout vs Schematic)
```bash
scripts/flow/run_lvs.sh <project-dir> [platform]
# or manually:
cd /opt/EDA4AI/OpenROAD-flow-scripts/flow
make DESIGN_CONFIG=/path/to/config.mk lvs
```
- Invokes KLayout with platform-specific `.lylvs` rule file and CDL netlist
- Not all platforms have LVS rules — script gracefully skips when unavailable
- Outputs: `6_lvs.lvsdb`, `6_lvs.log`
- Script copies results to `<project>/lvs/`

### RCX (Parasitic Extraction)
```bash
scripts/flow/run_rcx.sh <project-dir> [platform]
```
- Does NOT use ORFS Makefile; runs OpenROAD directly
- Generates `rcx/run_rcx.tcl` with commands:
  ```tcl
  read_db <6_final.odb>
  define_process_corner -ext_model_index 0 X
  extract_parasitics -ext_model_file <rcx_patterns.rules>
  write_spef <output.spef>
  ```
- Runs via `openroad -no_splash -exit rcx/run_rcx.tcl`
- Outputs: `rcx/6_final.spef`, `rcx/rcx.log`

## Default Assumptions for MVP

- Single top module
- No macros
- No custom floorplan beyond template defaults
- Focus on obtaining a runnable backend result with clean DRC and parasitic data

## Config Tuning Guidelines

### CORE_UTILIZATION Ranges
| Design Type | Recommended Utilization | Notes |
|-------------|------------------------|-------|
| Simple logic (UART, SPI, I2C) | 20-40% | Low routing demand |
| Medium (AES, SHA, FIR filters) | 15-30% | Moderate routing |
| Bus-heavy (crossbar, interconnect) | 10-15% | High routing demand |
| Macro-heavy (SRAM, CPU cores) | 30-40% | Macros occupy fixed area |

### PLACE_DENSITY_LB_ADDON Ranges
| Design Type | Recommended LB_ADDON | Notes |
|-------------|---------------------|-------|
| Small/simple designs | 0.10-0.20 | Low risk of divergence |
| Medium designs | 0.15-0.30 | Balanced |
| Large/macro-heavy designs | 0.20-0.45 | Prevents NesterovSolve divergence |
| **Minimum safe value** | **0.10** | Values below 0.10 risk placement stall |

**Never set PLACE_DENSITY_LB_ADDON below 0.05** — this reliably causes placement divergence on any non-trivial design.

### Backend-aware synthesis retune (post-route timing miss, clean routing)

When a design **routes clean** but **misses timing after route** (the synth-time WNS
estimate was optimistic), re-pick the ABC mapping strategy and re-synthesize instead of
relaxing the clock or the floorplan. `diagnose_signoff_fix.py --check timing` offers the
`backend_aware_synth_retune` recipe in this case (Win 6):

| Knob | Delta | Effect |
|------|-------|--------|
| `ABC_AREA` | → `0` | Timing-driven ABC mapping (area mode off) |
| `SYNTH_HIERARCHICAL` | → `0` | Flatten so ABC optimizes across module boundaries |

It reruns from `synth` (the already-paved `rerun_from:"synth"` path) and rechecks timing,
feeding the **real routed WNS** back as `outcome_score`. The recipe **enters as shadow**
(`requires_ab_promotion`): a blind live run never auto-applies it — only the A/B arm
(`--rank-first backend_aware_synth_retune`) exercises it until it wins an LCB-gated A/B
trial (`R2G_AB_REPEATS`, Win 2), after which the learned-recipe ranking surfaces it. It is
**never** hand-promoted and **never** auto-merged into `failure-patterns.md`. Fires only on
`moderate`/`severe` timing tiers with clean DRC.

### Safety Flags for Large Designs
For designs with > 50K instances or macros (swerv, black_parrot, ibex):
```makefile
export SKIP_CTS_REPAIR_TIMING = 1   # Prevents SIGSEGV in CTS timing repair
export SKIP_LAST_GASP = 1           # Prevents stalls in post-route optimization
```

### Route Acceleration for ChipTop-Class Designs (>1M nets)

For BOOM ChipTop and similar M-net designs, the GRT stage's *additional*
passes (post-`repair_design`, post-`repair_timing`, post-`recover_power`
incremental GRTs) dominate runtime — each one is a full-design GRT call.
Use `ROUTE_FAST=1` (handled by `run_orfs.sh`) which sets:

| Flag | Effect |
|---|---|
| `SKIP_INCREMENTAL_REPAIR=1` | Skip post-GRT repair_design + 2 incremental GRTs + repair_timing block. **Largest single speedup.** |
| `SKIP_ANTENNA_REPAIR=1` | Skip antenna repair iterations |
| `DETAILED_ROUTE_END_ITERATION=10` | Cap detail-route iterations (default 64) |
| `GLOBAL_ROUTE_ARGS='-congestion_iterations 5 -allow_congestion -verbose ...'` | Cap initial GRT extra-iter at 5 (default 30); accept congestion |

Last-resort fallback: also set `ROUTE_FAST_SKIP_DRT=1` to add `SKIP_DETAILED_ROUTE=1`
(produces DEF + global routes only, no GDS).

### Finishing a ChipTop After Route Completes

ORFS runs `route` (5_1_grt → 5_2_route → 5_3_fillcell) and `finish` (6_1_fill → 6_report → GDS merge) as **separate stages**. If a long route stage is killed externally **after** `5_3_fillcell.odb` is written but before `finish` runs (e.g., session reaped, batch wrapper timed out, host reboot), you can resume with `finish` alone:

```bash
ORFS_TIMEOUT=21600 FROM_STAGE=finish ORFS_STAGES=finish \
  r2g-rtl2gds/scripts/flow/run_orfs.sh \
  design_cases/<project> nangate45 <flow_variant>
```

`finish` reads `5_route.odb` (a copy of `5_3_fillcell.odb`) and produces:
- `6_final.odb` (final database)
- `6_final.def` (final DEF)
- `6_final.v` (gate-level Verilog)
- `6_final.spef` (parasitics — RCX runs inside `6_report` via `extract_parasitics` when `RCX_RULES` is set)
- `6_final.gds` (after the klayout def2stream merge)

ChipTop scale: each step in `finish` is single-threaded for the most part (RCX is the longest, ~30-60 min on a 6.5GB ODB). Budget 4-6 hours total; **don't** enable `ROUTE_FAST` env vars for the finish stage — those only apply to route. **Do** keep `SKIP_REPORT_METRICS=1` and `SKIP_CTS_REPAIR_TIMING=1` from the original config.mk (they don't affect `finish` but stay set as a no-op).

Pitfall — `5_route.odb` must exist: ORFS's `finish` target depends on the route stage's final `cp 5_3_fillcell.odb 5_route.odb`. If the route stage was killed *during* `5_3_fillcell` (filler placement) it leaves `5_3_fillcell.odb` but no `5_route.odb`, and `finish` will fail to read its inputs. Diagnose with `ls -la results/<plat>/<design>/<variant>/5_*.odb`; if `5_3_fillcell.odb` exists but `5_route.odb` doesn't, `cp 5_3_fillcell.odb 5_route.odb` and `cp 5_1_grt.sdc 5_route.sdc` manually before launching `finish`.

### Variable Propagation Through ORFS's Per-Step Scrub

ORFS's per-step Make rule (`do-N_M_*`) iterates Make's `.VARIABLES` and
`unset`s most of them before invoking `make` again. The list it builds
(`UNSET_VARIABLES_NAMES` in `scripts/variables.mk`) filters by `$(origin V)`
and **excludes** vars whose origin is `command line`, `environment`,
`default`, or `automatic` — meaning shell-exported env vars and make
cmdline args **do** survive the scrub. Only Makefile-internal `export`s
get dropped.

Verified-working ways to override a `variables.yaml`-defined variable:

- ✅ Pass on the make cmdline (`make VAR='val'`) — survives via `MAKEOVERRIDES`
- ✅ Set in `config.mk` with `export VAR = ...` — re-applied each invocation
- ✅ `export VAR=...` in the parent shell — survives the scrub (origin: environment)

If you don't see your override taking effect, look at the *new* `.tmp.log`
(not the old `.log` left over from a previous run). The two coexist in the
same logs directory and `tail -F` of `5_*.log` will read the stale one too.

## Fmax Search (loose-first)

`scripts/reports/fmax_search.py` characterizes a design's Fmax (the fastest
clock period that still closes) **before** you commit to a clock target. It is a
**loose-first** search: it starts from a known-loose period and tightens, using
cheap **placement-stage** timing as a proxy for signoff timing instead of a full
flow per probe.

### The placement-stage proxy

Each probe runs only the front of the flow:

    ORFS_STAGES="synth floorplan place"

and reads the resulting **post-place** worst setup slack. The proxy keys, in
preference order, are:

- `detailedplace__timing__setup__ws` — post-detailed-placement worst setup slack
  (the primary proxy; this is what the search roots on)
- `floorplan__timing__setup__ws` — post-floorplan worst setup slack, used as a
  fallback when the detailed-placement value is missing (e.g. a probe that
  floorplanned but didn't reach detailed placement)

Stopping at place is what makes the search affordable: a probe costs roughly a
synth+floorplan+place run rather than a full place-route-finish-signoff run.

### Variant cloning recipe

Each probe is a real ORFS run at a distinct clock period, so it gets its own
project variant cloned from the base project:

- Variant directory name: `<base>_fmax_p<NNNN>`, where `<NNNN>` encodes the
  probe's clock period (so the directory is human-readable and collision-free
  across probe periods).
- Each variant gets a **unique `FLOW_VARIANT`** (`run_orfs.sh` derives
  `FLOW_VARIANT` from the project-dir basename), so concurrent probes never share
  an ORFS results/scratch directory. Unique variant names ensure that concurrent
  invocations of `fmax_search.py` on different designs never violate the
  "never two configs with the same DESIGN_NAME and FLOW_VARIANT concurrently"
  hard rule. (The search itself is sequential; there is no `--max-parallel` flag.)

By default variants are cleaned up after the search; pass `--keep-variants` to
retain them for inspection.

### Root-find algorithm

The search treats "does this period close at place?" as a monotone predicate and
roots on the proxy slack:

1. Start from a loose period (large clock period, comfortably positive proxy
   slack) and confirm it passes.
2. Tighten (shrink the period) — expanding outward / bisecting — to bracket the
   crossover where the post-place proxy slack goes from positive to negative.
3. Bisect within the bracket until the period interval is within tolerance,
   yielding the tightest period whose proxy slack is still `>= 0`.

This is a 1-D root find on a noisy-but-monotone signal; the loose-first start
keeps early probes cheap and avoids wasting full-flow runs on periods that were
never going to close.

### Deterioration model + `--verify` self-correction

Post-place timing is **optimistic** relative to signoff: routing, CTS skew, and
final optimization eat into the slack that looked fine at place. The search
corrects for this with a learned **per-family slack-deterioration model** (how
much worse signoff slack tends to be than the post-place proxy for designs of
this family/platform). The reported number is the **predicted-signoff Fmax**, not
the raw proxy Fmax.

Because the prediction is a model, the reported Fmax is a **proxy (UNVERIFIED)**.
Pass `--verify` to run **one** full flow at the chosen winning period; the
verified signoff slack is then fed back to tighten the deterioration model
(self-correction). `--verify` does **not** replace the step-8 `check_timing`
gate — that still runs on the real final backend.

The model is **nangate45-backfilled** (seeded from the existing nangate45
corpus); other platforms are **forward-learned** (the model accumulates as
verified runs land for those platforms), so early Fmax predictions on non-nangate
platforms lean more heavily on conservative defaults.

### Honest-label taxonomy

The result (`reports/fmax_search.json`) labels its Fmax with how trustworthy it
is, so downstream consumers never mistake a proxy for a signed-off number:

- **verified** — confirmed by a full flow (`--verify`) at the winning period; the
  signoff timing gate actually passed.
- **predicted** — proxy Fmax corrected by the deterioration model, but **not**
  run through full signoff. UNVERIFIED.
- **proxy-only / raw** — the post-place crossover period with no deterioration
  correction applied (e.g. no model available for the family yet); the least
  trustworthy and most optimistic.

### Where the proxy lies (least-reliable archetypes)

The place-stage proxy is least reliable on designs whose slack collapses *after*
placement. Cross-reference `failure-patterns.md`:

- **Congestion / route-limited** designs (see "Routing Congestion (GRT-0116)"):
  post-place timing looks healthy but routing detours and added buffers erase the
  slack the proxy reported.
- **Macro / CTS-skew-dominated** designs (see the macro placement and CTS
  sections): clock-tree skew and macro-pin access aren't modeled at place, so the
  proxy is optimistic.
- **Hold-cliff** designs: hold violations are essentially invisible at the place
  stage and only appear after CTS/route, so a period the proxy "closes" may fail
  signoff on hold.

For these archetypes prefer `--verify`, and treat a `predicted`/`proxy-only`
label as a loose upper bound on real Fmax rather than a commitment.

## When Backend Fails

Check issues in the following order:

1. Wrong top module name or missing Verilog file
2. Malformed config.mk or constraint.sdc
3. Invalid clock port name or clock period
4. Design too large for default utilization target
5. Routing congestion (reduce utilization — see config tuning table above)
6. Placement divergence (increase PLACE_DENSITY_LB_ADDON to at least 0.15)
7. OpenROAD crash in CTS/repair_timing (add SKIP_CTS_REPAIR_TIMING = 1)
8. Environment or tool installation issues

Do not immediately rewrite RTL unless reports indicate an RTL-caused issue.

## When Signoff Checks Fail

1. **DRC violations:** Review `6_drc.lyrdb` for categories. Reduce density or increase area.
2. **LVS mismatch:** Review `6_lvs.lvsdb` for specifics. Check port names and connections.
3. **RCX failure:** Check `rcx.log` for OpenROAD errors. Verify ODB is valid and RCX rules exist.

For automated, real-layout DRC/LVS fixing, drive `scripts/flow/fix_signoff.sh` (and
`check_timing.py --journal` for timing). These feed a **fix-learning loop**: every fix
iteration is recorded losslessly into the knowledge store's `fix_events`, distilled into
per-episode `fix_trajectories` and per-family `fix_recipes`, and replayed so
`diagnose_signoff_fix.py` proposes the same-violation strategies in evidence-ranked order
on the next run. See `references/signoff-fixing.md` ("Fix-Learning Loop") and
`knowledge/README.md`.

## Platform Selection Guide

| Platform | Node | Speed | DRC | LVS | RCX | Use Case |
|----------|------|-------|-----|-----|-----|----------|
| nangate45 | 45nm | Fast | Yes | Yes | Yes | Quick testing, default, full signoff |
| sky130hd | 130nm | Medium | Yes | Yes | Yes | Open-source PDK, tapeout-ready, full signoff |
| sky130hs | 130nm | Medium | No | No | Yes | High-speed variant |
| asap7 | 7nm | Slow | Yes | No | Yes | Advanced node testing |
| gf180 | 180nm | Medium | No | No | Yes | GF open PDK |
| ihp-sg13g2 | 130nm | Medium | Yes | Yes | Yes | IHP SiGe BiCMOS, full signoff |
