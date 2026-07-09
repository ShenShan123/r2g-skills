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
import subprocess
from pathlib import Path

import pytest

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


def test_klayout_installs_klayout():
    out = _run("install_klayout.sh", "--dry-run", "--force").stdout
    assert CONDA_CH in out and "klayout" in out


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


def test_graph_idempotent_when_python_pinned():
    # Point R2G_GRAPH_PYTHON at this interpreter (it has torch/pyg/pandas in the suite venv).
    import sys
    out = _run("install_graph.sh", "--dry-run", env={"R2G_GRAPH_PYTHON": sys.executable})
    assert out.returncode == 0
    assert "already satisfied" in out.stderr
    assert "+ " not in out.stdout
