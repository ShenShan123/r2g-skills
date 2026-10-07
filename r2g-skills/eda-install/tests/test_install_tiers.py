"""Tests for the per-tier installers (install_<tier>.sh) + shared _setup_lib.sh.

ALL assertions run under `--dry-run`, which prints '+ cmd' instead of executing —
so the suite verifies command *construction* (right channel, packages, paths,
sudo-vs-conda branch) with zero network access and zero real installs. Idempotency
is checked in dry-run too: a satisfied tier short-circuits to "already satisfied"
BEFORE any '+ cmd' is emitted, so even a detection miss cannot trigger an install.

Design doc: docs/superpowers/plans/r2g-skills-bootstrap-2026-07-08.md.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def _has_graph_trio() -> bool:
    """True when THIS interpreter imports the torch/torch_geometric/pandas trio.

    The graph-idempotency test pins R2G_GRAPH_PYTHON=sys.executable, so it can only
    observe the "already satisfied" short-circuit when the running interpreter has
    the trio (i.e. the suite is run under the graph venv). Run under a plain python3
    the precondition is absent — guard it rather than emit a spurious failure, exactly
    as test_idempotent_when_present guards on the pinned env.local.sh."""
    return subprocess.run(
        [sys.executable, "-c", "import torch, torch_geometric, pandas"],
        capture_output=True,
    ).returncode == 0

EDA_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = EDA_ROOT.parent
SETUP = EDA_ROOT / "scripts" / "setup"
SIGNOFF_ENVFILE = SKILLS_ROOT / "signoff-loop" / "references" / "env.local.sh"

# tier name → installer file (must match bootstrap.sh's install_<tier>.sh dispatch)
TIER_FILES = {
    "core": "install_core.sh",
    "frontend": "install_frontend.sh",
    "sky130": "install_sky130.sh",
    "klayout": "install_klayout.sh",
    "pdk": "install_pdk.sh",
    "graph": "install_graph.sh",
}
CONDA_CH = "--override-channels -c litex-hub -c conda-forge"


def _run(script: str, *args: str, env: dict | None = None):
    e = os.environ.copy()
    e.setdefault("R2G_MIN_FREE_GB", "0")   # any existing writable dir qualifies as the big volume
    if env:
        e.update(env)
    return subprocess.run(
        ["bash", str(SETUP / script), *args],
        capture_output=True, text=True, env=e,
    )


# --- wiring: bootstrap.sh dispatch names ↔ files ------------------------------

def test_tier_files_exist_for_every_tier():
    for tier, fname in TIER_FILES.items():
        assert (SETUP / fname).is_file(), f"bootstrap dispatches to install_{tier}.sh but it is missing"


def test_shared_lib_present():
    assert (SETUP / "_setup_lib.sh").is_file()


# --- command construction (dry-run --force bypasses the present-check) ---------

def test_frontend_uses_conda_litexhub():
    out = _run("install_frontend.sh", "--dry-run", "--force").stdout
    assert CONDA_CH in out
    assert "iverilog" in out and "verilator" in out


def test_sky130_installs_magic_netgen():
    out = _run("install_sky130.sh", "--dry-run", "--force").stdout
    assert CONDA_CH in out
    assert "magic" in out and "netgen" in out


def test_klayout_installs_into_dedicated_env():
    # klayout's Qt/Ruby deps conflict with magic/netgen/iverilog in a shared env,
    # so it MUST solve into its own env — never the shared 'eda' toolchain env.
    out = _run("install_klayout.sh", "--dry-run", "--force").stdout
    assert CONDA_CH in out and "klayout" in out
    assert "-n klayout" in out          # dedicated env
    assert "-n eda" not in out          # NOT the shared toolchain env


@pytest.mark.skipif(not shutil.which("klayout"),
                    reason="no system klayout to exercise the prefer-system path")
def test_klayout_prefers_existing_system_klayout():
    # A klayout on PATH (system, often newer than conda's) satisfies this OPTIONAL
    # tier: exit 0, "already satisfied", and NO install command emitted.
    out = _run("install_klayout.sh", "--dry-run")
    assert out.returncode == 0
    assert "already satisfied" in out.stderr
    assert "+ " not in out.stdout


def test_pdk_installs_open_pdks_never_volare():
    out = _run("install_pdk.sh", "--dry-run", "--force")
    assert "open_pdks.sky130a" in out.stdout
    assert "volare" not in (out.stdout + out.stderr).lower()


def test_graph_builds_cpu_torch_venv(tmp_path):
    out = _run("install_graph.sh", "--dry-run", "--force",
               env={"R2G_PREFIX": str(tmp_path)}).stdout
    assert "python3 -m venv" in out
    assert "download.pytorch.org/whl/cpu" in out
    assert "torch_geometric" in out and "pandas" in out
    assert f"{tmp_path}/pyenvs/r2g-graph" in out            # honors R2G_PREFIX


def test_core_nosudo_uses_conda_no_build():
    # On a machine with an ORFS checkout the clone is skipped; the no-sudo binary
    # path is conda openroad/yosys, and it must NEVER build from source.
    out = _run("install_core.sh", "--dry-run", "--force").stdout
    assert CONDA_CH in out
    assert "openroad" in out and "yosys" in out
    assert "build_openroad" not in out          # no-sudo default never builds


def test_core_build_flag_builds_from_source():
    out = _run("install_core.sh", "--dry-run", "--force", "--build").stdout
    assert "build_openroad.sh" in out


# --- idempotency: a satisfied tier short-circuits before any command ----------

@pytest.mark.skipif(not SIGNOFF_ENVFILE.exists(),
                    reason="needs a pinned env.local.sh so the conda tools resolve as present")
@pytest.mark.parametrize("tier", ["frontend", "sky130", "pdk"])
def test_idempotent_when_present(tier):
    # dry-run (no --force): if the tool resolves, it must exit 0 with no '+ cmd'.
    out = _run(TIER_FILES[tier], "--dry-run", env={"R2G_ENV_FILE": str(SIGNOFF_ENVFILE)})
    assert out.returncode == 0
    assert "already satisfied" in out.stderr
    assert "+ " not in out.stdout, f"{tier}: emitted an install command despite being present"


@pytest.mark.skipif(not _has_graph_trio(),
                    reason="this interpreter lacks torch/torch_geometric/pandas, so "
                           "R2G_GRAPH_PYTHON=sys.executable cannot short-circuit; run the "
                           "suite under the graph venv (as the V1 gates runner does)")
def test_graph_idempotent_when_python_pinned():
    # Point R2G_GRAPH_PYTHON at this interpreter (it has torch/pyg/pandas in the suite venv).
    out = _run("install_graph.sh", "--dry-run", env={"R2G_GRAPH_PYTHON": sys.executable})
    assert out.returncode == 0
    assert "already satisfied" in out.stderr
    assert "+ " not in out.stdout


def test_ensure_conda_returns_only_the_path_on_stdout(tmp_path):
    """ensure_conda's stdout is captured as a command; nothing else may land there.

    `conda_env_install` does `conda="$(ensure_conda)"`, so every byte
    ensure_conda writes to stdout becomes part of the command it then runs. The
    Miniconda installer prints PREFIX=..., "Unpacking payload...",
    "installation finished." to stdout, and on a host with no conda those lines
    were captured too:

        _setup_lib.sh: line 96: PREFIX=/.../miniconda3: No such file or directory
        ERROR: magic/netgen install failed

    Only on the FIRST run -- the next tier found conda already present, skipped
    the installer and worked, so the failure looked intermittent (216,
    2026-10-07: sky130 failed, pdk right behind it succeeded).

    The fake installer here writes chatter to stdout exactly as the real one
    does, and creates bin/conda so ensure_conda reaches its final echo.
    """
    import subprocess

    lib = SETUP / "_setup_lib.sh"
    assert lib.is_file()

    bigv = tmp_path / "bigv"
    bigv.mkdir()
    # stands in for the downloaded miniconda.sh: noisy on stdout, like the real one
    fake = bigv / "miniconda.sh"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "while [ $# -gt 0 ]; do case $1 in -p) shift; target=$1;; esac; shift; done\n"
        'echo "PREFIX=$target"\n'
        'echo "Unpacking payload ..."\n'
        'echo "installation finished."\n'
        'mkdir -p "$target/bin"\n'
        'printf "#!/usr/bin/env bash\\necho fake-conda\\n" > "$target/bin/conda"\n'
        'chmod +x "$target/bin/conda"\n'
    )
    fake.chmod(0o755)

    script = f"""
set -uo pipefail
export R2G_PREFIX={bigv}
export R2G_CONDA=""
export PATH=/nonexistent-so-no-real-conda:/usr/bin:/bin
source {lib}
# pick_big_volume and the download are both bypassed: the prefix is writable and
# miniconda.sh is already in place, so ensure_conda goes straight to running it.
pick_big_volume() {{ echo {bigv}; }}
have_cmd() {{ case "$1" in curl) return 0 ;; *) command -v "$1" >/dev/null 2>&1 ;; esac; }}
curl() {{ return 0; }}
out="$(ensure_conda)"
printf 'CAPTURED<%s>\\n' "$out"
"""
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       timeout=60)
    captured = ""
    for line in r.stdout.splitlines():
        if line.startswith("CAPTURED<"):
            captured = line[len("CAPTURED<"):-1]
    assert captured, f"ensure_conda produced nothing\nstdout:{r.stdout}\nstderr:{r.stderr}"
    assert "\n" not in captured, \
        f"installer chatter leaked into the captured path:\n{captured!r}"
    assert captured.endswith("/bin/conda"), captured
    assert "PREFIX=" not in captured and "Unpacking" not in captured, captured
