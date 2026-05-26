# agent-r2g

An AI-driven open-source EDA skill that takes a natural-language hardware spec (or RTL) and drives it all the way to **GDSII + signoff (DRC, LVS, RCX)** through [OpenROAD-flow-scripts](https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts), Yosys, KLayout, Magic, Netgen, and OpenRCX.

The core deliverable is the `r2g-rtl2gds` Claude Code skill. It is self-contained at `r2g-rtl2gds/` so it can be installed independently of this repository.

---

## Install

```bash
# From a clone of this repo
git clone https://github.com/<your-org>/agent-r2g.git
cd agent-r2g
./install.sh                # prompts: user (~/.claude/skills) vs project (./.claude/skills)
```

Flags for non-interactive install:

```bash
./install.sh --user          # ~/.claude/skills/r2g-rtl2gds  (available in every Claude Code session)
./install.sh --project DIR   # DIR/.claude/skills/r2g-rtl2gds (scoped to one project)
./install.sh --link          # symlink instead of copy (recommended while developing the skill)
./install.sh --uninstall     # remove a previous install
```

Restart Claude Code after install. The skill advertises itself through its `SKILL.md` frontmatter — ask Claude something like *"take this RTL through to GDS on nangate45"* and it will invoke the skill.

### Install just the skill (no agent-r2g clone)

The skill directory is fully standalone. If you already have the `r2g-rtl2gds/` directory by itself (e.g., downloaded as a tarball or via `degit`), use the in-skill installer:

```bash
cd r2g-rtl2gds
./install.sh --user
```

### As a Claude Code plugin

The repository also ships a Claude Code plugin manifest at `.claude-plugin/plugin.json`. Point `/plugin install` at this repo URL to register `r2g-rtl2gds` as a managed plugin.

### Other agent harnesses

The skill is a self-contained directory. Point your harness's skill loader at `r2g-rtl2gds/SKILL.md` as the entry document. All referenced scripts are resolved relative to that directory.

---

## Repository layout

```
agent-r2g/
├── r2g-rtl2gds/                    # ★ The skill — copy/symlink this into ~/.claude/skills/
│   ├── SKILL.md                    #   Entry point — metadata, workflow, hard rules
│   ├── install.sh                  #   Standalone installer (works without the rest of the repo)
│   ├── scripts/                    #   30 stateless Python/Shell CLIs
│   │   ├── flow/                   #     stage runners (run_lint, run_orfs, run_drc, run_lvs, run_rcx, …)
│   │   ├── extract/                #     parse tool output into JSON
│   │   ├── project/                #     init / normalize / validate project & spec
│   │   ├── reports/                #     timing gate, diagnosis, history
│   │   └── dashboard/              #     GDS render + multi-project HTML dashboard
│   ├── knowledge/                  #   Self-contained knowledge-store subsystem
│   ├── references/                 #   Workflow guide, failure patterns, PPA guide
│   ├── assets/                     #   config.mk / constraint.sdc templates + simple-arbiter example
│   └── tests/                      #   pytest suite
├── install.sh                      # ★ One-command installer for the skill (delegates to r2g-rtl2gds/install.sh)
├── .claude-plugin/plugin.json      #   Claude Code plugin manifest
├── tools/                          #   Repo-level batch orchestration (optional, not part of the skill)
│   ├── setup_rtl_designs.py        #     Scaffold design_cases/ from rtl_designs/
│   ├── batch_orfs_only.sh          #     Parallel ORFS-only runner with per-case flock
│   ├── batch_flow.sh               #     Full flow (ORFS + signoff)
│   └── fix_orfs_failures.py        #     Log-signature-driven config.mk rewriter
├── docs/                           #   Campaign reports and design plans (historical)
├── CLAUDE.md                       #   Project instructions for Claude Code (this repo only)
└── LICENSE
```

Everything under `r2g-rtl2gds/` is what gets installed. Everything outside it (`tools/`, `docs/`, `rtl_designs*/`, `design_cases/`) is **agent-r2g** workspace used to validate the skill at scale.

---

## Requirements

The skill targets Linux (tested on RHEL 8.10). All EDA tools are assumed pre-installed and reachable via `$PATH` or `command -v`. The skill autodetects them.

| Tool | Purpose | Required? |
|------|---------|-----------|
| Python 3.10+ | skill scripts | yes |
| Yosys | synthesis | yes |
| iverilog / vvp | simulation | yes |
| OpenROAD | place & route, OpenRCX | yes |
| OpenROAD-flow-scripts | full backend flow | yes |
| Verilator | faster lint/sim | optional |
| KLayout | GDS viewer, DRC, LVS | optional |
| Magic + Netgen | sky130 DRC/LVS | optional |
| OpenSTA | signoff STA | optional |
| sky130A PDK | sky130 DRC/LVS/SPICE | optional |

After install, verify discovery:

```bash
bash ~/.claude/skills/r2g-rtl2gds/scripts/flow/check_env.sh
```

### Tool discovery (no `source` needed)

The skill autodetects every tool on first use. For each value it checks, in order:

1. Existing env var (e.g., `ORFS_ROOT=/opt/ORFS`)
2. `$R2G_ENV_FILE` snippet
3. `r2g-rtl2gds/references/env.local.sh` (copy from `env.local.sh.template`)
4. `$ORFS_ROOT/env.sh`
5. `/opt/openroad_tools_env.sh`
6. `command -v <tool>` and a list of well-known install paths

Supported overrides: `ORFS_ROOT`, `PDK_ROOT`, `OPENROAD_EXE`, `YOSYS_EXE`, `KLAYOUT_CMD`, `MAGIC_EXE`, `NETGEN_EXE`, `STA_EXE`, `IVERILOG_EXE`, `VVP_EXE`, `VERILATOR_EXE`, `R2G_ENV_FILE`.

---

## Quick start (standalone, no agent)

The skill's shell/Python scripts are usable directly. Example end-to-end run for a small counter RTL (assumes the skill is installed at `~/.claude/skills/r2g-rtl2gds/`):

```bash
SKILL=~/.claude/skills/r2g-rtl2gds
DESIGN=my_counter

# 1. Scaffold a project directory (creates design_cases/<DESIGN>/ in CWD)
python3 $SKILL/scripts/project/init_project.py $DESIGN

# 2. Drop RTL and testbench in place
cp my_counter.v   design_cases/$DESIGN/rtl/design.v
cp tb_counter.v   design_cases/$DESIGN/tb/testbench.v

# 3. Write constraints
cp $SKILL/assets/config-template.mk      design_cases/$DESIGN/constraints/config.mk
cp $SKILL/assets/constraint-template.sdc design_cases/$DESIGN/constraints/constraint.sdc
# ...then edit DESIGN_NAME, VERILOG_FILES, clk_port_name to match your RTL

# 4. Pre-flight + frontend
bash    $SKILL/scripts/flow/check_env.sh
bash    $SKILL/scripts/flow/run_lint.sh  design_cases/$DESIGN/rtl/design.v   design_cases/$DESIGN/lint/lint.log
bash    $SKILL/scripts/flow/run_sim.sh   design_cases/$DESIGN/rtl/design.v   design_cases/$DESIGN/tb/testbench.v  design_cases/$DESIGN/sim
bash    $SKILL/scripts/flow/run_synth.sh design_cases/$DESIGN/rtl/design.v   my_counter                            design_cases/$DESIGN/synth

# 5. Backend (place & route → GDS)
bash    $SKILL/scripts/flow/run_orfs.sh  design_cases/$DESIGN nangate45

# 6. PPA + timing gate
python3 $SKILL/scripts/extract/extract_ppa.py  design_cases/$DESIGN  design_cases/$DESIGN/reports/ppa.json
python3 $SKILL/scripts/reports/check_timing.py design_cases/$DESIGN

# 7. Signoff
bash    $SKILL/scripts/flow/run_drc.sh   design_cases/$DESIGN nangate45
bash    $SKILL/scripts/flow/run_lvs.sh   design_cases/$DESIGN nangate45
bash    $SKILL/scripts/flow/run_rcx.sh   design_cases/$DESIGN nangate45

# 8. Dashboard (optional, HTML + GDS previews)
python3 $SKILL/scripts/dashboard/generate_multi_project_dashboard.py
python3 $SKILL/scripts/dashboard/serve_multi_project_dashboard.py 8765
```

A smoke-test example lives at `r2g-rtl2gds/assets/examples/simple-arbiter/`.

---

## Batch mode (optional, repo-level)

The `tools/` directory at the repo root drives the skill across hundreds of designs in parallel. It is **not** part of the skill install — clone this repo if you want to use it.

### Scaffold projects from an RTL catalog

Place raw designs under `rtl_designs/<name>/` with a `design_meta.json` (minimum keys: `design`, `top`, `platform`, `rtl_files`):

```bash
python3 tools/setup_rtl_designs.py
```

This emits `design_cases/<name>/` for every entry, generating size-aware `config.mk` and clock-aware `constraint.sdc`. It auto-detects:

- Clock port names via posedge/negedge signal analysis
- IO pin count (so the initial floorplan has enough perimeter)
- Largest inferred memory (sets `SYNTH_MEMORY_MAX_BITS` so Yosys doesn't reject)
- Unresolved `` `include `` targets (adds `VERILOG_INCLUDE_DIRS`)

### Run ORFS across all projects

```bash
bash tools/batch_orfs_only.sh 8 7200           # 8-way parallel, 2-hour per-stage timeout

echo -e "my_counter\naes_core" > cases.txt
DESIGNS_LIST=cases.txt bash tools/batch_orfs_only.sh 4 3600
```

Results land in `design_cases/_batch/orfs_results.jsonl` (one JSON line per case).

### Auto-fix failures and retry

```bash
python3 tools/fix_orfs_failures.py             # classifies each failure and rewrites the offending config.mk

grep -oE '"case": "[^"]+"' design_cases/_batch/orfs_results.jsonl | awk -F'"' '{print $4}' > failed.txt
DESIGNS_LIST=failed.txt bash tools/batch_orfs_only.sh 8 7200
```

`fix_orfs_failures.py` handles six known failure signatures:

1. `SYNTH_MEMORY_MAX_BITS` exceeded → raise to 128 Kbit
2. `PPL-0024` IO pin overflow → explicit `DIE_AREA` from log-reported required perimeter
3. `FLW-0024` place density > 1.0 → drop to `CORE_UTILIZATION = 10`
4. `PDN-0179/0185` insufficient strap width → enlarge die
5. Missing `` `include `` → add `VERILOG_INCLUDE_DIRS` + stub
6. Stage timeout → lower density, request longer timeout

Full pattern catalog with symptoms and fixes lives inside the skill: `r2g-rtl2gds/references/failure-patterns.md`.

---

## Validated scale

Tested on **495 heterogeneous RTL designs** (ICCAD benchmarks, verilog-ethernet, wb2axip, OpenCores, RISC-V cores, koios, VTR, zipcpu, BOOM/Chipyard, etc.):

| Pass | Designs rescued | Cumulative pass rate |
|------|-----------------|----------------------|
| 1 — initial ORFS sweep | 402 / 495 | 81.2% |
| 2 — `fix_orfs_failures.py` + config rewrites | +59 | 93.1% |
| 3 — long-tail retries (timeouts, macros, includes) | +15 | **96.2%** (476 / 495) |

The remaining 19 designs have understood, documented root causes (megadesign synthesis budgets, missing source netlists, zero-logic combinational stubs). Detail: `docs/batch_pass3_report.md`.

A separate sweep on a curated subset of 70 designs across 7 macro and non-macro families achieves **100% ORFS + LVS + RCX pass rate** — see `CLAUDE.md` "Validated Results" for the breakdown.

---

## Platform support matrix

| Platform | KLayout DRC | KLayout LVS | Magic DRC | Netgen LVS | OpenRCX |
|----------|-------------|-------------|-----------|------------|---------|
| `nangate45` | yes | yes | no | no | yes |
| `sky130hd` | yes | yes | yes | yes | yes |
| `sky130hs` | yes | yes | yes | yes | yes |
| `asap7` | yes | no | no | no | yes |
| `gf180` | yes | yes | no | no | yes |
| `ihp-sg13g2` | yes | yes | no | no | yes |

LVS gracefully skips for platforms without `.lylvs` rules (reports `status: "skipped"`).

---

## Uninstall

```bash
./install.sh --uninstall                 # removes the symlink/copy from the chosen scope
# or remove manually
rm -rf ~/.claude/skills/r2g-rtl2gds
```

---

## License

See `LICENSE`.
