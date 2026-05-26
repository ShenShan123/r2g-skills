# agent-r2g

A Claude Code skill that drives an open-source RTL-to-GDS flow — from natural-language spec (or existing RTL) through synthesis, place-and-route, and full signoff (DRC, LVS, RCX) — using Yosys, OpenROAD-flow-scripts, KLayout, and OpenRCX.

Install the `r2g-rtl2gds` skill, then ask Claude: *"synthesize this UART at 100 MHz on nangate45"* — it handles everything from RTL generation to GDSII.

---

## Prerequisites

The skill autodetects all tool paths on first use; no `source` or export is required. Run `check_env.sh` (see [Install](#install)) to verify what was found.

Tested on RHEL 8.10 with a pre-built toolchain. On other systems, set paths in `env.local.sh` (see [Environment setup](#environment-setup)).

| Tool | Required | Purpose |
|------|----------|---------|
| Python 3.10+ | yes | skill scripts |
| Yosys | yes | synthesis |
| iverilog / vvp | yes | simulation |
| OpenROAD + ORFS | yes | place & route, RCX |
| Verilator | optional | faster lint |
| KLayout | optional | GDS viewer, DRC, LVS |
| Magic + Netgen | optional | sky130 DRC / LVS |
| OpenSTA | optional | standalone STA |
| sky130A PDK | optional | sky130 signoff |

---

## Install

```bash
git clone https://github.com/ShenShan123/agent-r2g.git
cd agent-r2g
./install.sh --user          # copy to ~/.claude/skills/r2g-rtl2gds
```

Restart Claude Code (or run `/reload`) after install.

**Other options:**

```bash
./install.sh --user            # global — available in every Claude Code session (default)
./install.sh --project .       # local  — scoped to the current project directory
./install.sh --link --user     # symlink — edits are picked up without reinstalling
./install.sh --force --user    # overwrite an existing install
./install.sh --uninstall       # remove
```

**Verify tools were found:**

```bash
bash ~/.claude/skills/r2g-rtl2gds/scripts/flow/check_env.sh
```

**Installing from the skill directory alone** (no full repo clone needed):

```bash
cd r2g-rtl2gds
./install.sh --user
```

---

## First use

Open any Claude Code session and ask something like:

> *"Take this RTL through to GDS on nangate45"*
> *"Synthesize this UART at 100 MHz"*
> *"Run DRC and LVS on my design"*
> *"Generate a simple arbiter and produce a GDS"*

Claude matches these requests to the `r2g-rtl2gds` skill and drives every stage automatically — spec normalization, RTL generation, lint, simulation, synthesis, place-and-route, timing gate, and signoff.

The skill works from **existing RTL** (drop your file into `rtl/design.v`) or from a **natural-language spec** (Claude writes the RTL for you).

---

## Environment setup

No manual sourcing is required — every flow script runs autodetection on entry. To pin specific paths, copy the template:

```bash
cp ~/.claude/skills/r2g-rtl2gds/references/env.local.sh.template \
   ~/.claude/skills/r2g-rtl2gds/references/env.local.sh
# uncomment and set ORFS_ROOT, OPENROAD_EXE, etc.
```

Or point to an external file: `export R2G_ENV_FILE=/path/to/your-env.sh`.

Resolution order (first hit wins per value):

1. Caller env var (e.g. `ORFS_ROOT=/opt/ORFS ./run_orfs.sh ...`)
2. `$R2G_ENV_FILE`
3. `references/env.local.sh` (in the installed skill directory)
4. `$ORFS_ROOT/env.sh`
5. `/opt/openroad_tools_env.sh`
6. `$PATH` + well-known install locations

Supported overrides: `ORFS_ROOT`, `PDK_ROOT`, `OPENROAD_EXE`, `YOSYS_EXE`, `KLAYOUT_CMD`, `MAGIC_EXE`, `NETGEN_EXE`, `STA_EXE`, `IVERILOG_EXE`, `VVP_EXE`, `VERILATOR_EXE`, `R2G_ENV_FILE`.

---

## Repository layout

```
agent-r2g/
├── r2g-rtl2gds/                    # ★ The skill — copy/symlink this into ~/.claude/skills/
│   ├── SKILL.md                    #   Claude Code entry point (metadata + workflow)
│   ├── install.sh                  #   Standalone installer
│   ├── scripts/                    #   ~30 stateless Python/Shell CLIs
│   │   ├── flow/                   #     stage runners (lint, sim, synth, orfs, drc, lvs, rcx)
│   │   ├── extract/                #     parse tool output → JSON
│   │   ├── project/                #     init / normalize / validate
│   │   ├── reports/                #     timing gate, diagnosis, run history
│   │   └── dashboard/              #     GDS preview + multi-project HTML dashboard
│   ├── knowledge/                  #   Empirical heuristics store (seed data + Python code)
│   ├── references/                 #   Failure patterns, ORFS playbook, PPA guide, spec template
│   ├── assets/                     #   config.mk / constraint.sdc templates + simple-arbiter example
│   └── tests/                      #   pytest suite
├── install.sh                      # ★ One-command installer (delegates to r2g-rtl2gds/install.sh)
├── .claude-plugin/plugin.json      #   Claude Code plugin manifest
├── tools/                          #   Batch orchestration helpers (not part of the skill install)
│   ├── setup_rtl_designs.py        #     scaffold design_cases/ from an RTL catalog
│   ├── batch_orfs_only.sh          #     parallel ORFS runner with per-case flock
│   ├── batch_flow.sh               #     full flow (ORFS + signoff)
│   └── fix_orfs_failures.py        #     log-driven config.mk rewriter
└── CLAUDE.md                       #   Project instructions for this repo (not part of the skill)
```

Everything under `r2g-rtl2gds/` is what gets installed. Everything outside it (`tools/`, `rtl_designs*/`, `design_cases/`) is workspace used to validate the skill at scale.

---

## Manual CLI usage

The scripts work directly without Claude Code. Example for an existing counter RTL:

```bash
SKILL=~/.claude/skills/r2g-rtl2gds
PROJ=design_cases/my_counter

# Scaffold
python3 $SKILL/scripts/project/init_project.py my_counter
cp my_counter.v  $PROJ/rtl/design.v
cp tb_counter.v  $PROJ/tb/testbench.v
cp $SKILL/assets/config-template.mk       $PROJ/constraints/config.mk
cp $SKILL/assets/constraint-template.sdc  $PROJ/constraints/constraint.sdc
# edit DESIGN_NAME, VERILOG_FILES, clk_port_name to match your RTL

# Frontend
bash    $SKILL/scripts/flow/run_lint.sh  $PROJ/rtl/design.v  $PROJ/lint/lint.log
bash    $SKILL/scripts/flow/run_sim.sh   $PROJ/rtl/design.v  $PROJ/tb/testbench.v  $PROJ/sim
bash    $SKILL/scripts/flow/run_synth.sh $PROJ/rtl/design.v  my_counter  $PROJ/synth

# Backend (place & route → GDS)
bash    $SKILL/scripts/flow/run_orfs.sh  $PROJ  nangate45

# Timing gate — auto-fixes minor violations; stops for moderate/severe
python3 $SKILL/scripts/extract/extract_ppa.py   $PROJ  $PROJ/reports/ppa.json
python3 $SKILL/scripts/reports/check_timing.py  $PROJ

# Signoff
bash    $SKILL/scripts/flow/run_drc.sh  $PROJ  nangate45
bash    $SKILL/scripts/flow/run_lvs.sh  $PROJ  nangate45
bash    $SKILL/scripts/flow/run_rcx.sh  $PROJ  nangate45
```

A worked example: `r2g-rtl2gds/assets/examples/simple-arbiter/`.

---

## Batch mode

`tools/` drives the skill across hundreds of designs in parallel. It is **not** installed with the skill — clone the full repo to use it.

```bash
# 1. Scaffold design_cases/ from an RTL catalog in rtl_designs/
python3 tools/setup_rtl_designs.py

# 2. Run ORFS across all projects (8-way parallel, 2 h per-stage timeout)
bash tools/batch_orfs_only.sh 8 7200

# 3. Classify failures and rewrite config.mk automatically
python3 tools/fix_orfs_failures.py

# 4. Retry failures
DESIGNS_LIST=failed.txt bash tools/batch_orfs_only.sh 8 7200
```

`fix_orfs_failures.py` handles six failure patterns: wrong top module, `SYNTH_MEMORY_MAX_BITS` overflow, IO pin overflow, place density overflow, PDN strap width, and stage timeout. Full catalog: `r2g-rtl2gds/references/failure-patterns.md`.

---

## Platform support

| Platform | KLayout DRC | KLayout LVS | Magic DRC | Netgen LVS | OpenRCX |
|----------|-------------|-------------|-----------|------------|---------|
| `nangate45` | yes | yes | — | — | yes |
| `sky130hd` | yes | yes | yes | yes | yes |
| `sky130hs` | yes | yes | yes | yes | yes |
| `asap7` | yes | — | — | — | yes |
| `gf180` | yes | yes | — | — | yes |
| `ihp-sg13g2` | yes | yes | — | — | yes |

LVS gracefully skips for platforms without `.lylvs` rules (reports `status: "skipped"`).

---

## Validated scale

Tested on **495 heterogeneous RTL designs** (ICCAD benchmarks, RISC-V cores, BOOM/Chipyard, VTR, zipcpu, verilog-ethernet, and more):

| Pass | Outcome | Cumulative |
|------|---------|------------|
| Initial ORFS sweep | 402 / 495 pass | 81.2% |
| `fix_orfs_failures.py` + retries | +59 rescued | 93.1% |
| Long-tail (timeouts, macros, includes) | +15 rescued | **96.2%** (476 / 495) |

19 remaining designs have understood root causes (megadesign synthesis budgets, missing netlists, zero-logic stubs). A curated 70-design subset (7 families including macro designs) achieves **100% ORFS + LVS + RCX pass rate**.

---

## License

See `LICENSE`.
