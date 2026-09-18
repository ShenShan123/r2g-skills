---
name: eda-install
description: Detect a machine's environment and install + verify the open-source EDA toolchain that the signoff-loop and def-graph skills need — OpenROAD-flow-scripts (openroad/yosys), iverilog, KLayout, Magic, Netgen, OpenSTA, the sky130A PDK, and the torch+torch_geometric graph venv. Use when setting up a new machine, when `check_env.sh` reports missing tools, when the user asks to install/bootstrap/provision the EDA tools or "set up the environment", or when a flow fails because a tool or PDK is absent. The preferred path is a direct user-owned bundle under `R2G_PREFIX`; a legacy no-sudo conda path remains available only when `--direct` is not requested. Produces references/env.local.sh so a bootstrapped toolchain is auto-discovered by the flow skills.
metadata:
  requires:
    bins: [bash]
    optional_bins: [conda, git, curl]
    env:
      # Nothing must be pre-set — detection reads the ambient environment. These
      # only STEER the bootstrap; each is optional.
      R2G_PREFIX: "big-volume root for the direct tool bundle, PDK, and torch venv (default: first writable dir >= 15GB free, preferring /proj)"
      R2G_TOOLCHAIN_ROOT: "root of the direct EDA bundle (defaults to R2G_PREFIX)"
      R2G_GRAPH_PYTHON: "an existing python with torch+torch_geometric+pandas (skips building the graph venv)"
      R2G_MIN_FREE_GB: "free-space threshold for the big-volume picker (default 15)"
      R2G_ENV_FILE: "a shell snippet of tool-path exports to seed detection"
      R2G_DIRECT_ARTIFACT_LOCK: "optional operator-supplied fixed HTTPS artifact lock; default references/direct-artifacts.json"
      R2G_DIRECT_OFFLINE: "1 requires a matching SHA256 archive already in the prefix cache; no network"
      R2G_DIRECT_DOWNLOAD_TIMEOUT: "total resumable-download budget in seconds, default 3600, range 1..14400"
      R2G_DIRECT_DOWNLOAD_JOBS: "parallel verified HTTPS range jobs, default 1, range 1..8"
      R2G_DIRECT_RESUME_FROM: "optional explicit unverified partial file to import into a NEW cache; final SHA256 still required"
  warnings:
    - Required runtime probes need timeout or gtimeout; a missing probe limiter fails readiness.
    - Always run `bootstrap.sh --dry-run` first — it prints a per-tier plan and installs nothing.
    - Never installs large artifacts into a full $HOME — the PDK (~8GB) and torch venv go on a big volume.
    - The heavy ORFS source build is opt-in (--yes-gated); use --direct to refuse all conda fallback.
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
   unreachable — EXCEPT the sky130hs `.lyt` lefdef repair, a REQUIRED postcondition verified by
   `patch_sky130hs_lyt.py --check` plus the `sky130hs_gds_canary.py` DEF→GDS geometry canary; a
   failed repair FAILS setup, RMD-P0-04). Platforms SELECTED as strict (`--strict-platforms
   nangate45,sky130hd,sky130hs` on bootstrap.sh, or `R2G_STRICT_PLATFORMS` / `--platforms` on the
   installer) are FAIL-CLOSED end-to-end (RMD2-P1-01, failure-patterns #55): a missing/failing
   rule installer, an unverifiable canary, or a failed post-install
   `platform_capability.py --strict` probe fails setup, and the capability verdict + collateral
   sha256 digests are persisted to `eda-install/references/install_manifest.json`. Selecting
   strict platforms also makes the platform_rules tier REQUIRED in the plan. `check_env.sh` prints the same per-platform capability
   table; `check_env.sh --platform <p>` (or `R2G_TARGET_PLATFORM`) makes the NAMED campaign
   platform's strict readiness REQUIRED and fail-closed (RMD-P0-03);
   `R2G_STRICT_PLATFORMS="nangate45"` does the same for a standing list.
4. **Pin** (`scripts/setup/write_env_local.sh`) — writes `references/env.local.sh` into **both**
   `signoff-loop` and `def-graph` from the resolved paths, so the flow skills find the direct
   bundle (or a legacy conda/`/proj` installation) with no manual edit. Direct paths under
   `R2G_TOOLCHAIN_ROOT`/`R2G_PREFIX` outrank legacy package and host fallbacks; host binaries
   therefore do not make the required `core` tier complete. The writer pins paths outside
   `$ORFS_ROOT/tools/install` and adds `R2G_GRAPH_PYTHON`.
5. **Verify** — runs `scripts/flow/check_env.sh` (ORFS + required + optional + graph stage +
   platforms) and reports the same table the README documents. Failed installation, required
   pin generation, verification, explicitly requested deployment, or terminal manifest writing
   makes bootstrap exit non-zero. The manifest is written after verification/deployment and
   records separate `install_rc`, `pin_rc`, `verify_rc`, `deploy_rc`, and aggregate `bootstrap_rc`;
   a previous manifest is not current evidence when this invocation failed.
   Missing/failing initial or final pin resolution also fails closed (resolver rc=3 means
   fresh-machine autodetection, not a resolver fault). Re-observe pins after writing:
   the final `env_file_sha256` must match current bytes at manifest creation, while
   `plan_env_file_sha256` retains the pre-pin observation. `selection_source` retains the
   planning source; `final_selection_source` records the new observation, including an
   internally bound env-file. Requested deployment is skipped if earlier required phases fail.
   Readiness also checks actual ORFS `flow/Makefile`, required executable permissions, and
   bounded version/runtime probes (30s plus a 2s forced-kill grace). OpenROAD, Yosys,
   Icarus, vvp and Python 3.10+ must run successfully and produce output; an existing
   but unloadable binary is not ready. The standalone def-graph checker retains its
   narrower ORFS-data/Python requirement. These probes are not semantic or native-read proof.

   Direct SDK launchers (`openroad-matched/launch_openroad.sh`,
   `yosys/launch_yosys.sh`) outrank their bare payloads when present, preserving
   private loader/data bindings. Valid explicit env-file pins outrank skill-local
   and ORFS defaults, including Magic. Shared resolution and generated consumer
   pins export `COLUMNS=8192`: Yosys' ABC process-reuse protocol needs the complete
   echoed `source <absolute-script>` command, which readline otherwise clips on
   narrow terminals. This is a pinned I/O setting, not a synthesis constraint.
   Direct detection does not probe sudo or conda; neither can alter its plan.

## Direct bundle first; conda is a legacy fallback

When a direct bundle is present below `R2G_TOOLCHAIN_ROOT` (normally the same directory as
`R2G_PREFIX`), detection and all flow scripts use it without `conda activate`. The expected
layout is:

```text
$R2G_TOOLCHAIN_ROOT/
  openroad/bin/openroad.bin   # ELF payload; openroad is a convenience wrapper
  yosys/bin/yosys              # flow-matched ORFS Yosys
  oss-cad-suite/bin/{iverilog,vvp,verilator}
  klayout/bin/klayout.bin     # ELF payload; klayout is a convenience wrapper
  magic/bin/magic              netgen/bin/netgen  sta/bin/sta
  pdks/sky130A/
```

`bash r2g-skills/eda-install/bootstrap.sh --direct --dry-run --prefix "$R2G_PREFIX"` is the
fail-closed check: it never invokes conda. A missing direct artifact is reported as a required
action rather than silently falling back to `/usr`, `/opt`, or a package manager.

The direct **frontend** installer downloads the fixed Linux x64 OSS CAD Suite
release specified in `references/direct-artifacts.json`, verifies exact size and
SHA256, validates every tar member before extraction, and checks Icarus/vvp/Verilator
both before and after relocation. Its receipt binds the lock bytes and full payload
inventory; subsequent runs reject drift instead of overwriting an existing tree.
Failed partial downloads/stages remain under `.r2g-downloads`/`.r2g-staging` for inspection.
Preview creates nothing; `R2G_DIRECT_OFFLINE=1` requires an already matching cached archive.
Unsupported hosts have no unpinned or package-manager fallback.

Downloads use descriptor-bound resumable partials, with curl's truncating automatic
retry disabled. Each attempt resumes at the current byte offset; the total budget
is bounded by `R2G_DIRECT_DOWNLOAD_TIMEOUT`. A contended artifact lock prevents
concurrent writes. Retry/failure never promotes partial bytes into the verified cache;
exact final size/SHA256 remains mandatory. `R2G_DIRECT_RESUME_FROM` can explicitly
copy an old partial into a new cache without changing the source file.
On slow proxies, `R2G_DIRECT_DOWNLOAD_JOBS=4` fetches adjacent 4 MiB ranges in
parallel. Every range must return an exact `Content-Range` and byte count before
any bytes are appended. A failed connection retains the whole round, leaves the
contiguous partial unchanged, and retries up to the three-round no-progress bound.
The final whole-archive SHA256 remains authoritative.

This is **not yet a complete fresh-machine direct toolchain installer**. Missing
flow-matched core artifacts fail before any unpinned ORFS clone/build; stage a verified
SDK first. Other direct tiers still require their own verified acquisition recipes.
Frontend runtime receipts confer neither native-read closure nor TEHM production authority.

If `--direct` is not requested, detection runs `sudo -n true`. **Without root** (`HAVE_SUDO=0` —
the common case on shared servers) the legacy fallback routes through **pre-built conda
`litex-hub` packages + a venv**, all under the big volume:

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

For TEHM campaigns, record the selected installation once with
`scripts/record_orfs_toolchain_manifest.py record`, then pass the JSON through
`--toolchain-manifest` (or `R2G_TOOLCHAIN_MANIFEST`) to the campaign. The TEHM preflight replays
the lock before EDA execution and rejects a changed ORFS tree, binary, PDK marker, or capability.

## Flags

| Flag | Effect |
| --- | --- |
| `--dry-run` | Detect + plan only; install nothing. **Always run this first.** |
| `--yes` / `-y` | Non-interactive; accept the plan (incl. `--yes`-gated heavy tiers). |
| `--hermetic` | Require user-owned tools/PDK/graph outside `/usr`/`/opt` and install all missing tiers. |
| `--direct` / `--no-conda` | Use only the direct user-owned bundle; refuse any conda install. Implies `--hermetic`. |
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
