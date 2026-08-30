# eda-install — setup reference

Detailed companion to `SKILL.md`. Full design + rationale live in
`docs/superpowers/plans/r2g-skills-bootstrap-2026-07-08.md`.

## Layout

```
eda-install/
  bootstrap.sh              # orchestrator: detect → plan → install → pin → verify
  scripts/
    flow/
      _env.sh               # byte-identical shared resolver (md5 a5ac873e…)
      check_env.sh          # comprehensive verifier (ORFS + tools + graph + platforms)
    setup/
      detect_env.sh         # KEY=VALUE machine + toolchain snapshot
      write_env_local.sh    # pins references/env.local.sh into signoff-loop + def-graph
      install_<tier>.sh     # per-tier installers (dispatched by bootstrap.sh when present)
  references/setup.md        # this file
  tests/test_bootstrap.py    # detect contract + planner + pin + md5 identity
```

## Detection contract (`detect_env.sh` → stdout `KEY=VALUE`)

`OS_FAMILY`, `PKG_MGR`, `HAVE_SUDO`, `HAVE_CONDA`, `PYTHON3`, `BIG_VOLUME`,
`BIG_VOLUME_FREE_GB`, `MIN_FREE_GB`, then every `_env.sh` value (`ORFS_ROOT`, `FLOW_DIR`,
`OPENROAD_EXE`, `YOSYS_EXE`, `IVERILOG_EXE`, `VVP_EXE`, `VERILATOR_EXE`, `KLAYOUT_CMD`,
`MAGIC_EXE`, `NETGEN_EXE`, `STA_EXE`, `PDK_ROOT`, `SKY130A_DIR`) and `GRAPH_PYTHON`.
Every key is always emitted (empty value == absent); diagnostics go to stderr.

## Tiers

| Tier | Need | Satisfied when | No-sudo action |
| --- | --- | --- | --- |
| `core` | required | `ORFS_ROOT` + user-owned `OPENROAD_EXE` + user-owned `YOSYS_EXE` | clone ORFS (no build) + conda `openroad yosys` |
| `frontend` | required | `IVERILOG_EXE` + `VVP_EXE` | conda `iverilog verilator` |
| `sky130` | optional | `MAGIC_EXE` + `NETGEN_EXE` | conda `magic netgen` |
| `klayout` | optional | `KLAYOUT_CMD` (system OK) | dedicated `klayout` env; prefers system klayout, fails soft |
| `pdk` | optional | `SKY130A_DIR` | conda `open_pdks.sky130a` → big volume |
| `graph` | optional | `GRAPH_PYTHON` (torch venv) | `python3 -m venv` + pip torch(cpu)+pyg+pandas |

`core` and `frontend` branch on `HAVE_SUDO` (source build vs conda); the rest are root-free either way.

## No-sudo path (the default when `HAVE_SUDO=0`)

The entire toolchain is pre-built on the [`litex-hub`](https://anaconda.org/litex-hub) conda channel,
so provisioning is: install/reuse Miniconda on the big volume → `conda create -n eda …` → `conda
install open_pdks.sky130a` → `git clone` ORFS (no build) → `venv` + pip for torch →
`write_env_local.sh` pins it → `check_env.sh` goes green. No `sudo`; nothing written outside the big
volume and the two flow skills' `references/env.local.sh`.

Key rules: whole conda root on the big volume (not a full `$HOME`); `--override-channels -c litex-hub
-c conda-forge` on every conda call (defaults-channel ToS gate); pin the ORFS clone to a tag
compatible with the conda openroad, and fall back to a pre-built OpenROAD binary release on version
skew (`check_env.sh` prints tool versions).

The resolver checks the conda `eda` bin directory under `R2G_PREFIX` before `/opt` and `/usr` for
OpenROAD and Yosys. A host-wide pair can still be shown as a diagnostic, but it does not make the
`core` tier complete; a new clone therefore downloads a user-owned pair and writes both paths into
`references/env.local.sh`. After provisioning, create and replay the TEHM lock before a campaign:

```bash
python3 memory/scripts/record_orfs_toolchain_manifest.py record \
  --orfs-root "$ORFS_ROOT" --prefix "$R2G_PREFIX" \
  --openroad "$R2G_PREFIX/miniconda3/envs/eda/bin/openroad" \
  --yosys "$R2G_PREFIX/miniconda3/envs/eda/bin/yosys" \
  --pdk-root "$PDK_ROOT" \
  --output "$R2G_PREFIX/tehm-orfs-toolchain-manifest.json"
python3 memory/scripts/record_orfs_toolchain_manifest.py check \
  --manifest "$R2G_PREFIX/tehm-orfs-toolchain-manifest.json"
```

The lock is metadata (paths, versions, SHA256 and capability probes), not a checked-in binary. A
clean, matching tree-packaged or user-prefix installation is required for production evidence;
`--allow-external`/`--allow-dirty` are diagnostic-only escape hatches.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `big-volume=<none>` in the plan | pass `--prefix /path/with/space` (needs ≥ `R2G_MIN_FREE_GB`, default 15) |
| `graph OPT` though a venv exists | pass `--graph-python /path/to/venv/bin/python` (or export `R2G_GRAPH_PYTHON`) |
| conda download blocked | escalated by design — run the printed Miniconda command once the host is reachable |
| conda openroad ≠ ORFS `HEAD` | pin the ORFS clone tag, or use a pre-built OpenROAD binary release |
| klayout conda install fails (openssl/ruby/Qt solve) | expected — litex-hub klayout is often unsatisfiable + conda-forge has none; use the distro package (`dnf`/`apt install klayout`, usually newer). The tier fails soft; `KLAYOUT_CMD` uses whatever klayout resolves. |
