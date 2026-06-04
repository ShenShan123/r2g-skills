# agent-r2g

A Claude Code skill that drives an open-source RTL-to-GDS flow — from natural-language spec (or existing RTL) through synthesis, place-and-route, and full signoff (DRC, LVS, RCX) — using Yosys, OpenROAD-flow-scripts, KLayout, and OpenRCX.

Install the `r2g-rtl2gds` skill, then ask Claude: *"synthesize this UART at 100 MHz on nangate45"* — it handles everything from RTL generation to GDSII.

---

## Prerequisites

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

## Install the skill

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

**Installing from the skill directory alone** (no full repo clone needed):

```bash
cd r2g-rtl2gds
./install.sh --user
```

---

## Configure the OpenROAD toolchain

The skill autodetects every tool on first use. No `source` or `export` is required if your tools land in standard locations. Use `check_env.sh` to see exactly what was found (see [Verify](#verify-the-setup)).

Choose the path that matches your situation.

---

### Path A — Shared EDA server with a pre-built toolchain (fastest)

If `/opt/openroad_tools_env.sh` exists (e.g. a shared EDA workstation), the skill sources it automatically. Jump straight to [Verify](#verify-the-setup).

---

### Path B — Build ORFS from source (recommended for a clean Linux install)

OpenROAD-flow-scripts builds its own `openroad` and `yosys`, so one clone gives you the whole flow.

**1. System packages** (run once as root — Debian / Ubuntu):

```bash
sudo apt update
sudo apt install build-essential cmake git python3 python3-pip \
     iverilog klayout tcl-dev libboost-dev flex bison
```

For RHEL / Fedora / CentOS, replace `apt` with `dnf` / `yum` and adjust package names accordingly.

**2. Clone and build ORFS** (~30 min on 8 cores):

```bash
git clone --recursive https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts \
    ~/OpenROAD-flow-scripts
cd ~/OpenROAD-flow-scripts
sudo ./etc/DependencyInstaller.sh   # installs remaining build deps
./build_openroad.sh --local         # builds openroad + yosys under tools/install/
```

After the build, the skill autodetects both binaries from `$ORFS_ROOT/tools/install/` if you set `ORFS_ROOT`. Do that in `env.local.sh` (one line):

```bash
cp ~/.claude/skills/r2g-rtl2gds/references/env.local.sh.template \
   ~/.claude/skills/r2g-rtl2gds/references/env.local.sh

# Add this one line to env.local.sh:
echo 'export ORFS_ROOT="$HOME/OpenROAD-flow-scripts"' \
  >> ~/.claude/skills/r2g-rtl2gds/references/env.local.sh
```

---

### Path C — OSS-CAD-Suite + separate OpenROAD binary

OSS-CAD-Suite provides pre-built Yosys, iverilog, and Verilator. Pair it with a pre-built OpenROAD binary (from the OpenROAD releases page or your distro).

```bash
# Download OSS-CAD-Suite (check https://github.com/YosysHQ/oss-cad-suite-build for latest tag)
curl -LO https://github.com/YosysHQ/oss-cad-suite-build/releases/latest/download/oss-cad-suite-linux-x64.tgz
tar -xf oss-cad-suite-linux-x64.tgz -C "$HOME"        # extracts to ~/oss-cad-suite

# Install OpenROAD binary (Debian / Ubuntu)
sudo apt install openroad

# Clone ORFS for the flow Makefile (no build needed here)
git clone --recursive https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts \
    ~/OpenROAD-flow-scripts
```

Then set all paths in `env.local.sh`:

```bash
export ORFS_ROOT="$HOME/OpenROAD-flow-scripts"
export YOSYS_EXE="$HOME/oss-cad-suite/bin/yosys"
export IVERILOG_EXE="$HOME/oss-cad-suite/bin/iverilog"
export VVP_EXE="$HOME/oss-cad-suite/bin/vvp"
export VERILATOR_EXE="$HOME/oss-cad-suite/bin/verilator"
export OPENROAD_EXE="/usr/bin/openroad"
export KLAYOUT_CMD="/usr/bin/klayout"
```

---

### `env.local.sh` — full reference

The file lives at `~/.claude/skills/r2g-rtl2gds/references/env.local.sh`. Copy it from the template and edit:

```bash
cp ~/.claude/skills/r2g-rtl2gds/references/env.local.sh.template \
   ~/.claude/skills/r2g-rtl2gds/references/env.local.sh
```

All keys are optional — uncomment only the lines that the autodetect gets wrong.

```bash
# ── ORFS checkout ─────────────────────────────────────────────────────────────
# Required. Must contain flow/Makefile. Set this and everything else is found.
export ORFS_ROOT="$HOME/OpenROAD-flow-scripts"

# ── Required tool binaries ────────────────────────────────────────────────────
# Autodetected from $ORFS_ROOT/tools/install/, $PATH, and well-known paths.
# Uncomment only if autodetect picks the wrong binary.
# export OPENROAD_EXE="$ORFS_ROOT/tools/install/OpenROAD/bin/openroad"
# export YOSYS_EXE="$ORFS_ROOT/tools/install/yosys/bin/yosys"
# export IVERILOG_EXE="/usr/bin/iverilog"
# export VVP_EXE="/usr/bin/vvp"

# ── Optional tool binaries ────────────────────────────────────────────────────
# export VERILATOR_EXE="/usr/local/bin/verilator"
# export KLAYOUT_CMD="/usr/bin/klayout"
# export MAGIC_EXE="/usr/bin/magic"
# export NETGEN_EXE="/usr/bin/netgen-lvs"
# export STA_EXE="/usr/local/bin/opensta"

# ── PDK root (sky130 DRC/LVS with Magic / Netgen only) ────────────────────────
# export PDK_ROOT="$HOME/pdks"     # must contain sky130A/
```

The file is sourced automatically by every flow script. Alternatively, point to a file anywhere on disk:

```bash
export R2G_ENV_FILE=/path/to/your-env.sh   # add to ~/.bashrc or ~/.zshrc
```

**Autodetected locations** (checked in order if the variable is not set):

| Variable | Checked paths (first hit wins) |
|----------|-------------------------------|
| `ORFS_ROOT` | `$HOME/OpenROAD-flow-scripts`, `/opt/OpenROAD-flow-scripts`, `/opt/EDA4AI/OpenROAD-flow-scripts`, sibling of skill dir |
| `OPENROAD_EXE` | `$ORFS_ROOT/tools/install/OpenROAD/bin/openroad`, `/usr/local/bin/openroad`, `/usr/bin/openroad` |
| `YOSYS_EXE` | `$ORFS_ROOT/tools/install/yosys/bin/yosys`, `/opt/pdk_klayout_openroad/oss-cad-suite/bin/yosys`, `/usr/local/bin/yosys`, `/usr/bin/yosys` |
| `IVERILOG_EXE` | `/opt/pdk_klayout_openroad/oss-cad-suite/bin/iverilog`, `/usr/bin/iverilog` |
| `VVP_EXE` | `/opt/pdk_klayout_openroad/oss-cad-suite/bin/vvp`, `/usr/bin/vvp` |
| `KLAYOUT_CMD` | `/usr/local/bin/klayout`, `/usr/bin/klayout` |
| `MAGIC_EXE` | `/usr/local/bin/magic`, `/usr/bin/magic` |
| `NETGEN_EXE` | `netgen-lvs` or `netgen` on `$PATH`, `/usr/bin/netgen-lvs`, `/usr/local/bin/netgen` |
| `STA_EXE` | `sta` or `opensta` on `$PATH`, `/usr/local/bin/opensta`, `/usr/bin/opensta` |
| `PDK_ROOT` | `/opt/pdks`, `$HOME/pdks`, `/usr/local/share/pdks` |

---

### Verify the setup

```bash
bash ~/.claude/skills/r2g-rtl2gds/scripts/flow/check_env.sh
```

Expected output when all required tools are found:

```
[ORFS]
ok   ORFS_ROOT      /home/you/OpenROAD-flow-scripts
ok   FLOW_DIR       /home/you/OpenROAD-flow-scripts/flow
skip PDK_ROOT       (optional, not found)
skip SKY130A_DIR    (optional, not found)

[required tools]
ok   OPENROAD_EXE   /home/you/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad
ok   YOSYS_EXE      /home/you/OpenROAD-flow-scripts/tools/install/yosys/bin/yosys
ok   IVERILOG_EXE   /usr/bin/iverilog
ok   VVP_EXE        /usr/bin/vvp
ok   python3        /usr/bin/python3

[optional tools]
ok   KLAYOUT_CMD    /usr/bin/klayout
skip VERILATOR_EXE  (optional, not found)
skip MAGIC_EXE      (optional, not found)
skip NETGEN_EXE     (optional, not found)
skip STA_EXE        (optional, not found)
skip gtkwave        (optional, not found)

[platforms]
ok    nangate45
ok    sky130hd
ok    sky130hs
ok    asap7
ok    gf180
ok    ihp-sg13g2
```

**All required lines must show `ok` before running a flow.** Optional `skip` lines are fine — nangate45 (the default platform) only needs the required tools and KLayout.

Common fixes:

| Symptom | Fix |
|---------|-----|
| `MISS ORFS_ROOT` | Set `ORFS_ROOT` in `env.local.sh` pointing to your ORFS clone |
| `MISS OPENROAD_EXE` | Add `OPENROAD_EXE` to `env.local.sh`, or run `build_openroad.sh --local` first |
| `MISS YOSYS_EXE` | Same as OpenROAD — built together by `build_openroad.sh` |
| `MISS IVERILOG_EXE` | `sudo apt install iverilog` (or set `IVERILOG_EXE` in `env.local.sh`) |
| `MISS python3` | `sudo apt install python3` |
| Platforms list empty | `ORFS_ROOT` set but `flow/platforms/` not present — check ORFS clone is complete |

---

## First use

With tools verified, open any Claude Code session and ask something like:

> *"Take this RTL through to GDS on nangate45"*
> *"Synthesize this UART at 100 MHz"*
> *"Run DRC and LVS on my design"*
> *"Generate a simple arbiter and produce a GDS"*

Claude matches these requests to the `r2g-rtl2gds` skill and drives every stage — spec normalization, RTL generation, lint, simulation, synthesis, place-and-route, timing gate, and signoff.

The skill works from **existing RTL** (drop your file into `rtl/design.v`) or from a **natural-language spec** (Claude writes the RTL for you).

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

The skill has been validated on **682 RTL designs** spanning ICCAD benchmarks, RISC-V cores, BOOM/Chipyard, VTR, zipcpu, verilog-ethernet, wb2axip, and more.

**ORFS backend (place & route → GDS):** 476 / 495 designs from the original `rtl_designs/` batch pass (96.2%); 19 remaining have understood root causes (megadesign synthesis budgets, missing netlists, zero-logic stubs).

| Pass | Outcome | Cumulative |
|------|---------|------------|
| Initial ORFS sweep | 402 / 495 pass | 81.2% |
| `fix_orfs_failures.py` + retries | +59 rescued | 93.1% |
| Long-tail (timeouts, macros, includes) | +15 rescued | **96.2%** (476 / 495) |

**Signoff (682-design corpus, 2026-06-03):**

| Check | Clean | Rate | Notes |
|-------|------:|-----:|-------|
| LVS | 607 / 674 | **90%** | 15 `fail` are KLayout-0.30.7 symmetric-matcher limits (layout correct, tool can't prove it); only 2 genuine defects (both wb2axip); 44 `incomplete` are comparer bugs or extraction timeouts, not layout errors |
| DRC | 675 / 682 honest-verdict | **99%** coverage | `clean_beol` (BEOL-only pass) counts as clean; 7 stuck designs are verified-intractable (≥465K cells, CONTACT-op hang) |
| RCX | 681 / 682 | **99.85%** | 1 intractable (boom_smallseboom) |

A curated 70-design subset (7 families including macro designs) achieves **100% ORFS + LVS + RCX pass rate**.

---

## License

See `LICENSE`.
