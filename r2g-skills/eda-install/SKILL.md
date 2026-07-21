---
name: eda-install
description: Detect a machine's environment and install + verify the open-source EDA toolchain that the signoff-loop and def-graph skills need — OpenROAD-flow-scripts (openroad/yosys), iverilog, KLayout, Magic, Netgen, OpenSTA, the sky130A PDK, and the torch+torch_geometric graph venv. Use when setting up a new machine, when `check_env.sh` reports missing tools, when the user asks to install/bootstrap/provision the EDA tools or "set up the environment", or when a flow fails because a tool or PDK is absent. Without root it automatically installs a no-sudo path (pre-built conda litex-hub binaries + a venv on a big volume). Produces references/env.local.sh so a bootstrapped toolchain is auto-discovered by the flow skills.
metadata:
  requires:
    bins: [bash]
    optional_bins: [conda, git, curl]
    env:
      # Nothing must be pre-set — detection reads the ambient environment. These
      # only STEER the bootstrap; each is optional.
      R2G_PREFIX: "big-volume root for the conda install, PDK, and torch venv (default: first writable dir >= 15GB free, preferring /proj)"
      R2G_GRAPH_PYTHON: "an existing python with torch+torch_geometric+pandas (skips building the graph venv)"
      R2G_MIN_FREE_GB: "free-space threshold for the big-volume picker (default 15)"
      R2G_ENV_FILE: "a shell snippet of tool-path exports to seed detection"
  warnings:
    - Always run `bootstrap.sh --dry-run` first — it prints a per-tier plan and installs nothing.
    - Never installs large artifacts into a full $HOME — the PDK (~8GB) and torch venv go on a big volume.
    - The heavy ORFS source build is opt-in (--yes-gated); without root the no-sudo conda path is used instead.
    - klayout is optional and best from a system/distro package — the conda recipe is frequently unsatisfiable (litex-hub pins openssl 1.1 vs ruby's openssl 3.x); the tier prefers an existing klayout, uses a dedicated env, and fails soft.
    - Does NOT run PnR or build datasets — it provisions the tools that signoff-loop and def-graph run.
---
# eda-install Skill

**Provision the open-source EDA toolchain, then get out of the way.** This is the setup companion to
the two working skills: `signoff-loop` (RTL→GDS + signoff) and `def-graph` (graph datasets). It
answers one question — *"are the tools this machine needs present, and if not, how do I get them
without breaking anything?"* — and acts on the answer.

## The one command

```bash
bash r2g-skills/eda-install/bootstrap.sh --dry-run     # detect + plan, install NOTHING
bash r2g-skills/eda-install/bootstrap.sh               # install missing tiers + pin env.local.sh + verify
```

`bash r2g-skills/bootstrap.sh` is a shim to the same script.

## What it does — detect → plan → install → pin → verify

1. **Detect** (`scripts/setup/detect_env.sh`) — a clean `KEY=VALUE` snapshot of the machine: OS +
   package manager, `HAVE_SUDO`, `HAVE_CONDA`, a big writable volume (≥15 GB, preferring `/proj`),
   plus every tool + PDK the shared `scripts/flow/_env.sh` resolver already discovers, and a
   torch-capable python. Absence is data, never an error.
2. **Plan** — `bootstrap.sh` prints a per-tier table (core / frontend / sky130 / klayout / pdk /
   graph), each `OK` / `MISS` (required, absent) / `OPT` (optional, installable), with the exact
   action it would take. The install **channel is chosen by `HAVE_SUDO`** (see below).
3. **Install** — each tier is `scripts/setup/install_<tier>.sh`; `bootstrap.sh` dispatches to one the
   moment it exists (and until then prints the command it *would* run). The `platform_rules` tier is
   **in the default plan** (2026-07-20, round-2 pilot P0-3 — a stock nangate45 checkout has no LVS
   deck and an unusable 0-area antenna diode, so strict signoff is impossible while every tool reads
   green): the plan probes `platform_capability.py --platform nangate45 --strict` and shows OK/OPT;
   being ORFS-mutating it still installs only when named (`--tiers platform_rules`), materializing
   the repo's bundled nangate45 DRC/LVS/antenna rule decks into the ORFS checkout
   (`install_platform_rules.sh`, best-effort, HINTs when the repo `tools/` installers are
   unreachable). `check_env.sh` prints the same per-platform capability table;
   `R2G_STRICT_PLATFORMS="nangate45"` makes readiness REQUIRED there.
4. **Pin** (`scripts/setup/write_env_local.sh`) — writes `references/env.local.sh` into **both**
   `signoff-loop` and `def-graph` from the resolved paths, so the flow skills find a conda / `/proj`
   toolchain with no manual edit. It pins only what autodetect would miss (e.g. omits openroad/yosys
   already under `$ORFS_ROOT/tools/install`) and adds `R2G_GRAPH_PYTHON`.
5. **Verify** — runs `scripts/flow/check_env.sh` (ORFS + required + optional + graph stage +
   platforms) and reports the same table the README documents.

## The channel is chosen for you: no-sudo is the default

Detection runs `sudo -n true`. **Without root** (`HAVE_SUDO=0` — the common case on shared servers)
every tier routes through **pre-built conda `litex-hub` packages + a venv**, all under the big volume:

| Tool(s) | No-sudo channel |
| --- | --- |
| openroad, yosys, iverilog, verilator, klayout, magic, netgen, opensta | conda `litex-hub` (all `--override-channels -c litex-hub -c conda-forge` — the ToS-gate workaround) |
| ORFS flow + platforms | `git clone --recursive` — **no build**; `env.local.sh` points `OPENROAD_EXE`/`YOSYS_EXE` at the conda binaries |
| sky130A PDK | conda `open_pdks.sky130a` → the big volume (never `volare` — proxy/rate-limit caveat) |
| torch / torch_geometric / pandas | `python3 -m venv` + pip (CPU wheels) |

"No sudo" means *download pre-built binaries into a user-writable prefix* — not *compile in userspace*.
With root, `core`/`frontend` may instead build ORFS from source (`--yes`-gated, ~30 min); every other
tier is already root-free. What cannot be self-healed (conda/network blocked, no ≥15 GB volume, a
GLIBC too old for the conda binaries) **escalates with a clear HINT — never a silent failure.**

## Flags

| Flag | Effect |
| --- | --- |
| `--dry-run` | Detect + plan only; install nothing. **Always run this first.** |
| `--yes` / `-y` | Non-interactive; accept the plan (incl. `--yes`-gated heavy tiers). |
| `--prefix DIR` | Big-volume root for the conda install, PDK, and torch venv. |
| `--tiers a,b,c` | Act only on a subset (`core,frontend,sky130,klayout,pdk,graph`). |
| `--graph-python P` | Pin an existing torch venv (`R2G_GRAPH_PYTHON`) instead of building one. |
| `--plan-from FILE` | Plan against a saved detect dump (review / tests) — implies `--dry-run`. |
| `--deploy [--link]` | After provisioning, run `install.sh` to deploy the skills (`--link` recommended). |

## Invariants (honesty layer)

- **`scripts/flow/_env.sh` is byte-identical across all three skills** (md5 `a5ac873e…`) — the same
  resolver the flow scripts use; edit every copy together.
- **Detection is read-only and total** — it emits every key (empty == absent) and never exits non-zero
  for a missing tool.
- **The pin file is idempotent** — regenerating `env.local.sh` writes only what autodetect misses, so
  it never fights `_env.sh`.
- **`--dry-run` and `--plan-from` install nothing** — the plan is always previewable before any action.

## References

- `references/setup.md` — tiers, the no-sudo path in depth, and troubleshooting.
- `docs/superpowers/plans/r2g-skills-bootstrap-2026-07-08.md` — full design + rationale.
