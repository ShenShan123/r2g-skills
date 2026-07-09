# One-command toolchain bootstrap: detect → install → pin → verify

**Date:** 2026-07-08  ·  **Branch:** `feat/r2g-bootstrap`  ·  **Status:** 🟢 **FEATURE COMPLETE (slices
1–2).** Slice 1 (detection + plan/`--dry-run` + `env.local.sh` pin + verify-layer graph coverage) is
committed as `c4006ad`; slice 2 (the per-tier `install_<tier>.sh` scripts + shared `_setup_lib.sh` +
their tests) landed on the same branch. The whole detect → plan → install → pin → verify wheel now
runs end-to-end; remaining work is real-machine validation of the network install paths (this machine
is already fully provisioned, so every tier reads `OK` and installs nothing).

> **Rev 1 (2026-07-08):** expanded the **no-sudo path** into a first-class, auto-selected default
> (new §"No-sudo path"), because it is the reference machine's actual situation (no root, `$HOME`
> full, whole toolchain in a conda env on `/proj`). The install-tier table now carries an explicit
> sudo-vs-no-sudo channel per tier.
>
> **Rev 2 (2026-07-08):** the provisioning machinery is refactored into a **dedicated third sub-skill,
> `eda-install`** (`r2g-skills/eda-install/`), triggerable in-session by *"set up the EDA tools"*.
> `bootstrap.sh` + `detect_env.sh` + `write_env_local.sh` + a comprehensive `check_env.sh` now live
> under it, with a byte-identical `_env.sh` copy (md5 invariant now spans **three** skills). A shim at
> `r2g-skills/bootstrap.sh` preserves the documented command; `install.sh` + `plugin.json` deploy all
> three skills.

## What & why

Today, making `r2g-skills` *usable on a fresh machine* is a manual ritual. `install.sh` deploys the
two **skills**, but the **EDA toolchain** (ORFS + openroad/yosys, iverilog, klayout, magic/netgen, the
sky130A PDK, the torch venv) is installed entirely by **copy-paste prose** — README Paths A/B/C, the
Miniconda no-sudo recipe, `run_graphs.sh` comments, and `env.local.sh.template` examples. Nothing
*executes* any of it, nothing *detects* what the machine already has, and the user hand-writes
`env.local.sh` afterward.

Goal: **`bash r2g-skills/bootstrap.sh`** — one command that detects the environment, installs only the
missing pieces via the channel that fits the machine (sudo vs no-sudo, big-volume placement),
auto-generates `env.local.sh`, and verifies the result — so "take this RTL to GDS" works without a
human reading the install section.

## Current state — the four layers, and the missing one

The codebase already separates concerns cleanly. The gap is a **provisioner** — the layer that *acts*
on what detection finds.

| Layer | File | Role | Auto today? |
|---|---|---|---|
| Skill **deploy** | `r2g-skills/install.sh` | Symlink/copy both sub-skills into `.claude/skills/` | ✅ one command |
| Tool **detect** | `signoff-loop|def-graph/scripts/flow/_env.sh` (byte-identical, md5 `ad4406d0…`) | Sourced; resolves ORFS + tools + PDK: env → `$R2G_ENV_FILE` → `references/env.local.sh` → `$ORFS_ROOT/env.sh` → `/opt/openroad_tools_env.sh` → `command -v` → well-known paths | ✅ detect-only |
| Tool **verify** | `signoff-loop/scripts/flow/check_env.sh` | Prints `ok`/`MISS`/`skip`; exits 1 on a missing *required* tool | ✅ read-only |
| Platform-rule **install** | `tools/install_nangate45_{lvs,drc,antenna}.sh` | Materialize DRC/LVS/antenna decks into the ORFS checkout (idempotent, backup-aware) | ⚠️ nangate45-only, **repo-only** (not bundled in the installed skill) |
| **Provisioner (install tools)** | — | detect → plan → install missing tiers → pin `env.local.sh` → verify | ❌ **absent — this plan** |

### Specific gaps
1. **No provisioner.** ORFS/openroad/yosys/iverilog/klayout/magic/netgen/PDK/torch install only as prose.
2. **No machine detection.** Nothing detects distro/package-manager, `sudo` availability, existing
   conda, a big free volume, or proxy conditions — the user picks a "Path" by hand.
3. **`check_env.sh` doesn't cover def-graph.** No `R2G_GRAPH_PYTHON`/torch row, no def-graph checker,
   no per-platform PDK/rule-deck readiness — "green" ≠ the *compound* skill is ready.
4. **`env.local.sh` is hand-authored.** The reference machine's file was written by a human (its
   "auto-generated" comment notwithstanding).
5. **Deploy and setup aren't chained.** `install.sh` and toolchain setup are separate, with a manual
   `check_env.sh` in between.

## Design goals / non-goals

**Goals**
- **Idempotent + re-runnable.** Second run is a no-op when everything's present (mirror the
  backup-aware `install_nangate45_*.sh` pattern).
- **Tiered + fail-soft** (mirror the skill's own philosophy): install the **required** core; offer
  **optional** tiers (sky130 signoff, PDK, graph venv) opt-in; a dead channel skips *that tier*, never
  aborts the core. A def-graph SKIP-on-missing-torch must stay a valid end state.
- **No-sudo first.** The reference machine has no root; support the Miniconda/litex-hub path as a
  first-class citizen, not a footnote.
- **Never fill `$HOME`.** Large artifacts (PDK ~8 GB, torch venv) land on a big volume (`/proj`),
  never `$HOME` — a CLAUDE.md hard rule.
- **Plan before acting.** A `--dry-run` plan table by default; heavy/irreversible work (the ~30-min
  ORFS build) is gated behind `--yes`.

**Non-goals**
- Not a container image or a system-package manager replacement — it *drives* apt/dnf/conda, it
  doesn't reinvent them.
- No new runtime dependency in the flow scripts themselves; bootstrap is a setup-time tool.
- Does not touch the knowledge/learning loop, the graph extractors, or any signoff logic.

## Architecture — `bootstrap.sh` = detect → plan → install → pin → verify

One new entry point, `r2g-skills/bootstrap.sh` (sibling of `install.sh`, so it ships with the
**skill collection**, not only a full repo clone), delegating to small modular installers under
`signoff-loop/scripts/setup/`. Target UX:

```bash
bash r2g-skills/bootstrap.sh                 # detect, print plan, prompt, install missing, pin, verify
bash r2g-skills/bootstrap.sh --yes           # non-interactive (accept the plan, incl. heavy tiers opted-in)
bash r2g-skills/bootstrap.sh --dry-run       # detection + plan table only, install nothing
bash r2g-skills/bootstrap.sh --prefix /proj/me   # big-volume root for PDK + torch venv
bash r2g-skills/bootstrap.sh --tiers core,graph  # install only named tiers
bash r2g-skills/bootstrap.sh --deploy --link     # also run install.sh --link at the end
```

### 1. Detect — `scripts/setup/detect_env.sh`
Sources `_env.sh` for tool discovery, then gathers *machine* facts it doesn't currently collect, and
emits a machine-readable `KEY=VALUE` block (same contract style as `resolve_platform_paths.sh`):

```
OS_FAMILY=rhel|debian|fedora|macos|unknown
PKG_MGR=apt|dnf|yum|brew|none
HAVE_SUDO=0|1                     # `sudo -n true`
HAVE_CONDA=/path/to/conda|        # conda or mamba
ORFS_ROOT=…|                      # from _env.sh
OPENROAD_EXE=…| YOSYS_EXE=…| …    # from _env.sh (each tool)
GRAPH_PYTHON=…|                   # python with torch+torch_geometric+pandas, else empty
BIG_VOLUME=/proj/user5           # first writable dir with ≥15GB free (prefer /proj/$USER, never a full $HOME)
PDK_ROOT=…|
```

The **big-volume picker** directly encodes the "never fill `$HOME`" rule: prefer `/proj/$USER`, fall
back to `$HOME` only if it has ≥15 GB free; overridable with `--prefix`.

### 2. Plan — printed by `bootstrap.sh`
A table of what will happen per tier before anything runs (dry-run is the default first pass):

```
tier        status    action
core-orfs   MISS      build ORFS at /proj/user5/OpenROAD-flow-scripts (~30min; needs --yes)
frontend    partial   conda: iverilog,vvp into env 'eda' (no-sudo, litex-hub)
sky130      MISS      conda: magic,netgen into env 'eda'
pdk         present   skip (PDK_ROOT=/proj/.../sky130A)
graph-venv  MISS      venv at /proj/user5/pyenvs/r2g-graph + torch(cpu)+pyg+pandas
plat-rules  n/a       install nangate45 lvs/drc/antenna decks into ORFS
```

### 3. Install — modular, idempotent, tiered
Each tier is a standalone script under `signoff-loop/scripts/setup/`, independently runnable and
testable. The **channel is auto-selected by `HAVE_SUDO`**: with root available, tiers may build from
source / use the system package manager; **without root, every tier routes through conda `litex-hub`
+ venv into `$BIG_VOLUME`** (see §"No-sudo path"). Both channels land the same binaries in
`env.local.sh`; the flow scripts never know which was used.

| Tier | Script | With sudo | No sudo (auto when `HAVE_SUDO=0`) |
|---|---|---|---|
| **core-orfs** (required) | `install_orfs.sh` | clone ORFS under `$BIG_VOLUME` + `etc/DependencyInstaller.sh` + `build_openroad.sh --local` (builds openroad+yosys; ~30min, `--yes`-gated) | clone ORFS (git only, **no build**) + `openroad`/`yosys` from conda; point `OPENROAD_EXE`/`YOSYS_EXE` at the conda binaries |
| **frontend** (required) | `install_frontend.sh` | `iverilog`/`verilator` via pkg-mgr | conda `eda`: `iverilog verilator` (yosys shared with core) |
| **sky130** (optional) | `install_signoff_conda.sh` | conda `eda` even with sudo (no distro packages ship these cleanly) | conda `eda`: `magic netgen` |
| **pdk** (optional) | `install_pdk.sh` | conda `open_pdks.sky130a` → `$BIG_VOLUME` | same (conda, never `volare` — proxy/rate-limit caveat) |
| **graph-venv** (optional) | `install_graph_venv.sh` | `python3 -m venv $BIG_VOLUME/pyenvs/r2g-graph` + torch(cpu)+pyg+pandas | same (venv+pip need no root) |
| **plat-rules** | `install_platform_rules.sh` | wrap the bundled `install_nangate45_{lvs,drc,antenna}.sh` (writes into the ORFS clone — no root) | same |

All conda calls use `--override-channels -c litex-hub -c conda-forge` (the ToS-gate workaround). All
idempotent and backup-aware. Note **sky130/pdk/graph-venv are already root-free even with sudo** — only
core-orfs and frontend actually branch on `HAVE_SUDO`.

### 4. Pin — `scripts/setup/write_env_local.sh`
Generates `references/env.local.sh` **in both skills** from the detected/installed paths — writing
only the lines autodetect would miss (conda `eda` bin, `/proj` PDK, `R2G_GRAPH_PYTHON`). Replaces the
hand-written pin file; preserves the "all keys optional" spirit and backs up any existing file once.

### 5. Verify — extend `check_env.sh`
- Add a **graph-python/torch** row and per-platform **PDK + rule-deck readiness** rows.
- Add a thin `def-graph/scripts/flow/check_env.sh` (or a `--graph` flag on the shared one) so
  verification covers the compound skill, closing gap #3.
- `bootstrap.sh` ends by running `check_env.sh` and printing the same table the README documents.

## No-sudo path (auto-selected when `HAVE_SUDO=0`)

This is the **reference machine's actual situation** and therefore the plan's default assumption:
no root, `$HOME` full, the whole toolchain living in a conda env with the PDK + torch venv staged on
`/proj`. The user never *chooses* this path — `detect_env.sh` runs `sudo -n true`, and on failure the
planner silently routes every tier through the root-free channel below.

### Cornerstone: the whole toolchain is pre-built on conda `litex-hub`
"No sudo" here does **not** mean "compile everything in userspace." Every EDA binary this skill needs
is a pre-built package on the [`litex-hub`](https://anaconda.org/litex-hub) channel, installable into
a user-writable prefix with zero root. The only thing cloned-but-never-built is ORFS itself (for its
`flow/Makefile` + `platforms/`), and `_env.sh` already tolerates a build-less ORFS by falling back
from `$ORFS_ROOT/tools/install/...` to `command -v`.

| Tool | Tier | No-sudo channel | conda package |
|---|---|---|---|
| openroad | core | conda | `openroad` |
| yosys | core/frontend | conda | `yosys` |
| ORFS flow + platforms | core | `git clone --recursive` (**no build**) | — |
| iverilog / vvp | frontend | conda | `iverilog` |
| verilator | optional | conda | `verilator` |
| klayout | optional | conda | `klayout` |
| magic | sky130 | conda | `magic` |
| netgen | sky130 | conda | `netgen` |
| opensta | optional | conda | `opensta` |
| sky130A PDK | pdk | conda → `$BIG_VOLUME` | `open_pdks.sky130a` |
| torch / torch_geometric / pandas | graph | `venv` + pip (CPU wheels) | — |

### Four decisions that make it robust
1. **ORFS is cloned, not built.** `build_openroad.sh` needs system build-deps behind sudo; skip it.
   `git clone --recursive` gives the flow + platforms (git needs no root), then `env.local.sh` pins
   `OPENROAD_EXE`/`YOSYS_EXE` to the conda binaries. ORFS's Makefile only needs the binaries +
   `flow/platforms/`, which the clone provides.
2. **The whole conda root goes on `$BIG_VOLUME`, not `$HOME`.** `bash Miniconda3.sh -b -p
   $BIG_VOLUME/miniconda3` — because `$HOME` is full and the `eda` env + `open_pdks` PDK (~8 GB) live
   under `<conda-prefix>/envs/eda`. This consolidates the PDK *with* the tools, instead of the
   reference machine's current split (tools in `$HOME/miniconda3`, PDK hand-staged on `/proj`).
   If conda is already present (`HAVE_CONDA` set) and has room, reuse it; else install Miniconda here.
3. **`--override-channels -c litex-hub -c conda-forge` on every conda call.** The conda `defaults`
   channel now requires interactive Terms-of-Service acceptance and aborts a non-interactive
   `create`/`install` — the override sidesteps it (already documented in README + `env.local.sh`).
4. **Version-compat is checked, not assumed.** litex-hub's `openroad`/`yosys` can lag ORFS `HEAD`.
   `install_orfs.sh` (no-sudo mode) pins the ORFS clone to a tag known-compatible with the conda
   `openroad`; if a mismatch still surfaces at flow time, the documented fallback is a pre-built
   OpenROAD **binary release** (self-contained tarball, no root) matched to ORFS's expected version.
   `check_env.sh` prints the discovered `openroad -version` / `yosys -V` so a skew is visible, not silent.

### What the no-sudo path CANNOT self-heal → escalate with a clear HINT (never silent-fail)
- **conda/Miniconda download blocked** (offline, or a proxy without the Miniconda host allow-listed) and
  no cached installer → print the exact `curl … && bash Miniconda3.sh -b -p $BIG_VOLUME/miniconda3`
  command and stop. (Same class as the `volare`/SOCKS caveat: don't fight the network, document it.)
- **No writable volume with ≥15 GB free** anywhere (PDK alone is ~8 GB) → refuse and ask for `--prefix DIR`.
- **A genuine system-level dependency conda can't provide** (rare for this toolchain; e.g. a missing
  libc/GLIBC too old for the conda binaries) → report it; a source build behind sudo is the only fix,
  which is out of scope for the no-sudo path.

### Net effect
On a root-less machine the entire bootstrap is: install/reuse Miniconda on `$BIG_VOLUME` → one
`conda create -n eda` (openroad yosys iverilog verilator klayout magic netgen opensta) → one
`conda install open_pdks.sky130a` → `git clone` ORFS (no build) → `venv` + pip for torch →
`write_env_local.sh` pins it all → `check_env.sh` goes green. No `sudo`, nothing written outside
`$BIG_VOLUME` and the two skills' `references/env.local.sh`.

## Progress — slice 1 (2026-07-08, `feat/r2g-bootstrap`)

Landed the zero-risk, no-network core: detection, the dry-run plan, the pin generator, and the
verify-layer graph coverage. On *this* machine (`HAVE_SUDO=0`, `$HOME` full, tools in conda +
PDK/venv on `/proj`) `bootstrap.sh --dry-run` reads all required tiers **OK** and installs nothing.

**Rev 2 note:** everything below now lives under the **`eda-install`** skill (see Rev 2 in the header);
paths updated accordingly. On *this* machine (`HAVE_SUDO=0`, `$HOME` full, tools in conda + PDK/venv on
`/proj`) `bootstrap.sh --dry-run` reads all required tiers **OK** and installs nothing.

| File | State | Notes |
|---|---|---|
| `eda-install/scripts/setup/detect_env.sh` | ✅ built | KEY=VALUE contract (22 keys); sources `_env.sh`, adds OS/pkg-mgr/`HAVE_SUDO`/`HAVE_CONDA`/`BIG_VOLUME`/`GRAPH_PYTHON`. Verified: this machine → `HAVE_SUDO=0`, big-volume `/proj/workarea/user5`. |
| `eda-install/bootstrap.sh` (+ `r2g-skills/bootstrap.sh` shim) | ✅ built (plan path) | `--dry-run`/`--plan-from`/`--tiers`/`--prefix`/`--graph-python`/`--yes`/`--deploy`. Channel auto-selected by `HAVE_SUDO`. Install path dispatches to `install_<tier>.sh` when present, else prints the exact command it *would* run. |
| `eda-install/scripts/setup/write_env_local.sh` | ✅ built | Generates `env.local.sh` in **both** flow skills; pins ORFS + conda tools + PDK + `R2G_GRAPH_PYTHON`; omits openroad/yosys under `$ORFS_ROOT/tools/install`; backs up once. |
| `eda-install/scripts/flow/check_env.sh` | ✅ built | Comprehensive verifier (ORFS + required + optional + graph stage + platforms); the skill's own `_env.sh` is a byte-identical copy. |
| `eda-install/SKILL.md` + `references/setup.md` | ✅ built | Skill entry point (triggers on "set up the EDA tools") + tier/no-sudo/troubleshooting reference. |
| `signoff-loop/scripts/flow/check_env.sh` | ✅ edited | New `[graph dataset stage]` row (torch/`R2G_GRAPH_PYTHON` + version); override footer points at `bootstrap.sh`. |
| `def-graph/scripts/flow/check_env.sh` | ✅ built | Self-contained def-graph-side checker (ORFS + torch venv + platforms) for a def-graph-only install. |
| `install.sh` · `.claude-plugin/plugin.json` | ✅ edited | Deploy/register all **three** sub-skills (`eda-install` first). |
| `eda-install/tests/test_bootstrap.py` | ✅ built | 9 tests: detect contract, planner over 3 synthetic machines (provisioned/bare-nosudo/bare-sudo), graph-flip, `--tiers`, pin generator, `_env.sh` md5-identity across **3** copies. |
| `eda-install/scripts/setup/_setup_lib.sh` | ✅ built (slice 2) | Shared conda helpers: `ensure_conda` (bootstraps Miniconda on the big volume), `conda_env_install` (litex-hub + ToS override), `pick_big_volume`, `run`/`DRY`, `setup_parse`. |
| `eda-install/scripts/setup/install_{core,frontend,sky130,klayout,pdk,graph}.sh` | ✅ built (slice 2) | Per-tier installers — **named by tier** so `bootstrap.sh`'s `install_<tier>.sh` dispatch resolves. Idempotent (skip when present), `--dry-run`-previewable. No-sudo conda by default; `install_core.sh --build` for a source build. |
| `eda-install/scripts/setup/install_platform_rules.sh` | ✅ built (slice 2) | Best-effort dispatcher to the repo's nangate45 LVS/DRC/antenna rule installers. |
| `eda-install/tests/test_install_tiers.py` | ✅ built (slice 2) | 13 tests: bootstrap↔file wiring, per-tier command construction (channel/pkgs/paths, no-build), idempotent-when-present — **all under `--dry-run`** (zero network, zero real installs). |

> **Naming reconciliation:** the tier files are `install_<tier>.sh` (`core`/`frontend`/`sky130`/`klayout`/
> `pdk`/`graph`), not the earlier `install_{orfs,signoff_conda,graph_venv}.sh` sketch — the names must
> equal the tier keys `bootstrap.sh` dispatches on.

**Verification:** eda-install suite **22 passed** (9 bootstrap + 13 tier); signoff-loop **790 / 1**,
def-graph **337 / 14** — no regressions. `bash -n` clean on all scripts. `--dry-run` via the shim reads
all tiers **OK** on this machine; a real-mode installer run on a present tool is a verified no-op.

## File change list (proposal)

**New**
- `r2g-skills/bootstrap.sh` — orchestrator (detect → plan → install tiers → pin → verify → optional `--deploy`).
- `r2g-skills/signoff-loop/scripts/setup/{detect_env,install_orfs,install_frontend,install_signoff_conda,install_pdk,install_graph_venv,install_platform_rules,write_env_local}.sh`.
- `r2g-skills/signoff-loop/scripts/setup/platform_rules/install_nangate45_{lvs,drc,antenna}.sh` — bundled copies (source stays `tools/`; keep in sync or symlink).
- `r2g-skills/def-graph/scripts/flow/check_env.sh` (or `--graph` on the shared checker) — torch/`R2G_GRAPH_PYTHON` coverage.
- `r2g-skills/signoff-loop/tests/test_bootstrap.py` — dry-run + detection-parsing tests (no network).

**Edited**
- `check_env.sh` — graph-venv + per-platform readiness rows.
- `_env.sh` — add `R2G_GRAPH_PYTHON` / conda-`eda` discovery so a bootstrapped env is auto-found next
  session. **Edit BOTH copies; a test must assert the md5 stays identical** (CLAUDE.md `ad4406d0…` rule).
- `README.md`, both `SKILL.md`s — lead with `bootstrap.sh`; demote the manual Path A/B/C recipes to a
  fallback appendix.
- `env.local.sh.template` (both skills) — note it is now auto-generated by bootstrap.

## Sequencing (build order)

1. **Detection + dry-run plan** (`detect_env.sh`, `bootstrap.sh --dry-run`) — no installs; immediately
   useful, fully testable, zero risk.
2. **Pin generator + `check_env.sh` extension** — makes an *already-tooled* machine (like this one)
   turnkey with no heavy install; closes the def-graph verification gap.
3. **No-sudo tiers (the default path)** — Miniconda-on-`$BIG_VOLUME` + conda `litex-hub`
   (openroad/yosys/iverilog/magic/netgen), `open_pdks` PDK, graph venv, ORFS clone-only. Highest
   value: matches the reference machine and every root-less user; needs no privilege escalation.
4. **Sudo/build tiers (opt-in fallback)** — ORFS build-from-source + apt/dnf, only when `HAVE_SUDO=1`
   and the user prefers a source build. Heavy, `--yes`-gated.
5. **Bundle platform-rule installers + `--deploy` chaining** — the final "one command" polish.

The **first slice** to land is steps 1–2.

## Risks & how the design respects the hard rules

| Risk / constraint (source) | Mitigation in this design |
|---|---|
| `$HOME` full; PDK+venv must go to `/proj` (CLAUDE.md) | big-volume picker defaults to `/proj`, `--prefix` override; never `$HOME` unless it has room |
| conda ToS gate aborts non-interactive create (README) | always `--override-channels -c litex-hub -c conda-forge` |
| `_env.sh` byte-identical invariant (CLAUDE.md `ad4406d0…`) | discovery edits land in both copies; a test asserts md5 equality |
| ~30-min ORFS build is heavy/irreversible | dry-run plan default + `--yes` gate; never silently |
| Network/proxy flakiness | PDK via conda `open_pdks.sky130a`, never `volare`; each optional tier fails soft |
| Skill deploy must be symlink, not copy (stale-skill defect, CLAUDE.md) | `--deploy` calls `install.sh --link` |
| **No sudo on the target** (the reference machine) | **Default path** — whole toolchain from conda `litex-hub` (pre-built, no build) + ORFS clone-only + venv, all on `$BIG_VOLUME`; auto-selected when `HAVE_SUDO=0`. See §"No-sudo path". |
| No sudo AND conda/network unreachable | not self-healable → escalate with the exact Miniconda install command + stop (never silent-fail) |
| conda openroad/yosys version skew vs ORFS `HEAD` | pin ORFS clone to a compatible tag; `check_env.sh` prints tool versions; documented pre-built-binary fallback |

## Testing plan

- `test_bootstrap.py`: parse a fixture `detect_env.sh` `KEY=VALUE` block → assert the plan table
  (which tiers `MISS`/`present`/`skip`) for several synthetic machines (no-sudo+conda, apt+sudo,
  fully-provisioned). No network, no real installs.
- md5-equality test for the two `_env.sh` copies (guards the byte-identical invariant after the
  discovery edit).
- `check_env.sh --graph` on this machine (torch venv present) shows `ok R2G_GRAPH_PYTHON …`.
- Manual: `bootstrap.sh --dry-run` on this machine must report **all tiers present** (it's fully
  tooled) and install nothing.

## Superseded invariants (once implemented)

- The headline setup path becomes **`bash r2g-skills/bootstrap.sh`**; README Paths A/B/C become the
  manual fallback, not the primary instruction.
- `references/env.local.sh` is **generated** by `write_env_local.sh`, not hand-written (the template's
  "copy and edit" note is demoted to the manual path).
- `check_env.sh` green now means the **compound** skill is ready (includes the graph venv), not just
  signoff.
- The nangate45 platform-rule installers are **bundled with the skill** (previously repo-only under
  `tools/`).

## Docs to update on implementation (per the "update plan/spec docs" practice)

- This file → flip status to ✅ IMPLEMENTED with the commit + the superseded invariants that landed.
- `README.md` + both `SKILL.md`s — new headline setup path.
- `CLAUDE.md` — one line under "Toolchain" pointing at `bootstrap.sh` as the setup entry point.
- A `references/failure-patterns.md` entry if any tier's failure mode needs a documented HINT.

## Open questions (recommend the first option; not blocking the plan)

1. **New top-level `bootstrap.sh` vs `check_env.sh --install`?** → Recommend a **separate
   `bootstrap.sh`**: keeps `check_env.sh` read-only (its current contract) and gives setup its own
   `--dry-run`/`--yes`/`--prefix` surface.
2. **Bundle vs symlink the nangate45 rule installers into the skill?** → Recommend **bundled copies**
   with a sync test, so a skill-only install (no repo clone) still has them.
3. **ORFS: build-from-source vs pre-built binary default?** → Recommend **detect-driven**: build when
   `apt`+sudo present, else pre-built binary + a clear HINT; both gated by `--yes`.
