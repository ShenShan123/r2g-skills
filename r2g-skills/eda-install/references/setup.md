# eda-install — setup reference

Detailed companion to `SKILL.md`. Full design + rationale live in
`docs/superpowers/plans/r2g-skills-bootstrap-2026-07-08.md`.

## Layout

```
eda-install/
  bootstrap.sh              # orchestrator: detect → plan → install → pin → verify
  scripts/
    flow/
      _env.sh               # byte-identical shared resolver (direct bundle first)
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

For a direct installation, set `R2G_TOOLCHAIN_ROOT` (defaults to `R2G_PREFIX`) and keep all
non-system payloads below it:

```text
$R2G_TOOLCHAIN_ROOT/{openroad,yosys,oss-cad-suite,klayout,magic,netgen,sta,pdks}
```

| Tier | Need | Satisfied when | Direct action / legacy fallback |
| --- | --- | --- | --- |
| `core` | required | `ORFS_ROOT` + user-owned `OPENROAD_EXE` + user-owned `YOSYS_EXE` | use `$R2G_TOOLCHAIN_ROOT/{openroad,yosys}`; legacy clone + conda |
| `frontend` | required | `IVERILOG_EXE` + `VVP_EXE` | download fixed HTTPS/SHA256 OSS CAD Suite into `$R2G_TOOLCHAIN_ROOT/oss-cad-suite`; legacy conda |
| `sky130` | optional | `MAGIC_EXE` + `NETGEN_EXE` | use `$R2G_TOOLCHAIN_ROOT/{magic,netgen}`; legacy conda |
| `klayout` | optional | `KLAYOUT_CMD` (system OK) | use `$R2G_TOOLCHAIN_ROOT/klayout`; legacy dedicated env/system |
| `pdk` | optional | `SKY130A_DIR` | use `$R2G_TOOLCHAIN_ROOT/pdks`; legacy conda `open_pdks.sky130a` |
| `graph` | optional | `GRAPH_PYTHON` (torch venv) | `python3 -m venv` + pip torch(cpu)+pyg+pandas |

`--direct` makes every missing direct artifact a fail-closed action; it never invokes conda. Without
`--direct`, `core` and `frontend` may still branch on `HAVE_SUDO` (source build vs legacy conda).

Direct frontend acquisition uses `scripts/setup/direct_archive.py` and the checked-in
`references/direct-artifacts.json` lock (currently Linux x64 only). An explicitly supplied
`R2G_DIRECT_ARTIFACT_LOCK` must follow the same schema, fixed HTTPS URL, exact size/SHA256,
archive bounds and required runtime probes. Cache files are SHA-addressed below
`.r2g-downloads`; `R2G_DIRECT_OFFLINE=1` forbids downloading a missing cache entry.
Before publishing the payload, tar validation rejects escaping paths/links, member writes
below links, special files, duplicates and size/member-limit violations. Required probes
have bounded runtime/output and their own process groups, including child cleanup.
`.r2g-install/frontend.json` binds lock bytes, descriptor and payload inventory; replay
rejects changed bytes, permissions or metadata. Existing untracked payloads are retained,
never overwritten. Failed downloads/stages remain recoverable.

Interrupted transfers retain a descriptor-bound `<sha256>.part` and resume from
its size on the next attempt/invocation; curl internal retry is disabled because
it can truncate a timed-out transfer back to zero. Each transfer is at most 600s,
the total budget defaults to 3600s (`R2G_DIRECT_DOWNLOAD_TIMEOUT`, 1..14400), and
three consecutive no-progress attempts fail. A per-artifact flock prevents concurrent
partial writes. Partials are never verified artifacts: only final exact size/SHA256
can publish the cache file. To recover an older partial into a fresh prefix, set
`R2G_DIRECT_RESUME_FROM=/explicit/path/file.part`; the old file is not modified,
and imported bytes confer no readiness/production authority.

`R2G_DIRECT_DOWNLOAD_JOBS` (default 1, range 1..8) optionally uses adjacent
4 MiB HTTPS range requests for constrained proxies. A round appends nothing
unless every response is an exact HTTP `Content-Range` with the required byte
count. Invalid/partial range bodies and headers remain inspectable; they are
never spliced into the contiguous partial; the complete round is retried up to
the existing three-consecutive-no-progress bound. Successful temporary chunks are
removed only after their bytes are appended in exact order. Whole-archive
size/SHA256 is still the sole cache-publication gate.

Missing direct core currently fails before unpinned ORFS clone/build/package fallback.
Neither this refusal nor a frontend receipt proves full fresh-machine provisioning,
upstream build origins for the rest of the SDK, native-read closure or production eligibility.

## Legacy no-sudo path (only when `--direct` is not requested)

The entire toolchain is pre-built on the [`litex-hub`](https://anaconda.org/litex-hub) conda channel,
so provisioning is: install/reuse Miniconda on the big volume → `conda create -n eda …` → `conda
install open_pdks.sky130a` → `git clone` ORFS (no build) → `venv` + pip for torch →
`write_env_local.sh` pins it → `check_env.sh` goes green. No `sudo`; nothing written outside the big
volume and the two flow skills' `references/env.local.sh`.

Key rules: whole conda root on the big volume (not a full `$HOME`); `--override-channels -c litex-hub
-c conda-forge` on every conda call (defaults-channel ToS gate); pin the ORFS clone to a tag
compatible with the conda openroad, and fall back to a pre-built OpenROAD binary release on version
skew (`check_env.sh` prints tool versions).

The resolver checks the direct bundle under `R2G_TOOLCHAIN_ROOT`/`R2G_PREFIX` before legacy conda,
`/opt` and `/usr`. A host-wide pair can still be shown as a diagnostic, but it does not make the
`core` tier complete. After provisioning, create and replay the TEHM lock before a campaign:

```bash
python3 memory/scripts/record_orfs_toolchain_manifest.py record \
  --orfs-root "$ORFS_ROOT" --prefix "$R2G_PREFIX" \
  --openroad "$R2G_TOOLCHAIN_ROOT/openroad/bin/openroad.bin" \
  --yosys "$R2G_TOOLCHAIN_ROOT/yosys/bin/yosys" \
  --pdk-root "$PDK_ROOT" \
  --output "$R2G_PREFIX/tehm-orfs-toolchain-manifest.json"
python3 memory/scripts/record_orfs_toolchain_manifest.py check \
  --manifest "$R2G_PREFIX/tehm-orfs-toolchain-manifest.json"
```

The lock is metadata (paths, versions, SHA256 and capability probes), not a checked-in binary. A
clean, matching tree-packaged or user-prefix installation is required for production evidence;
`--allow-external`/`--allow-dirty` are diagnostic-only escape hatches.

## Bootstrap completion is fail-closed

A non-preview bootstrap succeeds only when installation, required pin generation, environment
verification, any explicitly requested `--deploy`, and terminal manifest writing all succeed.
Missing pin/verifier scripts or a broken metadata Python runtime fail the command; they are not
warnings that leave a successful exit status. `--dry-run` still performs none of these phases.

`install_manifest.json` is written after verification/deployment and records `install_rc`,
`pin_rc`, `verify_rc`, `deploy_rc`, and aggregate `bootstrap_rc`. `install_rc` remains specific
to installers. If manifest writing fails, the command fails; a manifest left by an older run
must not be treated as current evidence. Callers must check the invocation's exit status.
These phase results do not prove download origins, native dependency closure, or TEHM
promotion eligibility; the separate frozen toolchain/evidence checks are still required.

Initial pin resolution fails before installation on any rc other than 0/3/4; conflicts (4)
retain the existing explicit fail-closed path. Pins are resolved again after writing, and
any final resolver fault/conflict fails completion. rc=3 is fresh-machine autodetection,
not proof of installed tools; verification still owns required tool readiness.
The final ORFS/PDK/env-file fields describe this post-pin observation, and the env-file
SHA is rechecked immediately before manifest writing. `plan_env_file_sha256` retains
the old digest; `selection_source` retains the planning source, while
`final_selection_source` reports final resolution (which can see the env-file internally
bound by bootstrap). A failed prerequisite prevents explicitly requested deployment.

`check_env.sh` validates actual ORFS flow data and required runtime health, not just
nonempty paths. EDA/signoff checkers probe OpenROAD/Yosys/Icarus/vvp and Python 3.10+;
the standalone def-graph checker only requires ORFS data and Python. Required processes
must be executable, terminate successfully, and produce output. Probes use `timeout`
or `gtimeout` (30s plus a 2s kill grace); no limiter means readiness failure. These
bounded version/runtime checks do not prove RTL semantics, binary download origins,
library-read closure, or strict signoff.

An existing SDK's OpenROAD/Yosys launchers are preferred to bare payloads;
explicit env-file pins survive skill-local and ORFS defaults. Magic follows
the same precedence. Generated pins and shared resolution export `COLUMNS=8192`
to keep long absolute ABC script commands visible to Yosys' process-reuse
parser even on narrow terminals. Direct-mode detection skips sudo and conda
capability discovery. These changes do not prove missing-tool download origins.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `big-volume=<none>` in the plan | pass `--prefix /path/with/space` (needs ≥ `R2G_MIN_FREE_GB`, default 15) |
| `graph OPT` though a venv exists | pass `--graph-python /path/to/venv/bin/python` (or export `R2G_GRAPH_PYTHON`) |
| conda download blocked | escalated by design — run the printed Miniconda command once the host is reachable |
| conda openroad ≠ ORFS `HEAD` | pin the ORFS clone tag, or use a pre-built OpenROAD binary release |
| klayout conda install fails (openssl/ruby/Qt solve) | expected — litex-hub klayout is often unsatisfiable + conda-forge has none; use the distro package (`dnf`/`apt install klayout`, usually newer). The tier fails soft; `KLAYOUT_CMD` uses whatever klayout resolves. |
