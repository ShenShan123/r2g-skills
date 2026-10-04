"""The graph venv must not inherit the caller's PYTHONHOME.

Regression (independent review, 2026-09-23): run_graphs.sh and
run_stage_dataset.sh launched $R2G_GRAPH_PYTHON with the inherited environment.
A driver running under the oss-cad python3 wrapper exports PYTHONHOME, the venv
died at init ("No module named 'encodings'"), and run_graphs.sh's torch import
probe turned that into a benign graph-stage SKIP. The same leak was fixed for
rtl-acquire in 9ba9bc4.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

FLOW = Path(__file__).resolve().parents[1] / "scripts" / "flow"


def _stub_python(tmp_path: Path) -> tuple[Path, Path]:
    seen = tmp_path / "seen.txt"
    stub = tmp_path / "graph_python"
    stub.write_text("#!/bin/sh\n"
                    f'printf "PYTHONHOME=%s\\n" "${{PYTHONHOME-<unset>}}" >> "{seen}"\n'
                    "exit 1\n")
    stub.chmod(0o755)
    return stub, seen


def _project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = d\nexport PLATFORM = sky130hd\n")
    return proj


def _env(stub: Path) -> dict[str, str]:
    return {**os.environ, "R2G_GRAPH_PYTHON": str(stub),
            "PYTHONHOME": "/opt/OpenROAD/oss-cad-suite",
            "PYTHONEXECUTABLE": "/opt/OpenROAD/oss-cad-suite/bin/tabbypy3"}


def test_run_graphs_probe_runs_the_venv_without_pythonhome(tmp_path: Path) -> None:
    stub, seen = _stub_python(tmp_path)
    subprocess.run(["bash", str(FLOW / "run_graphs.sh"), str(_project(tmp_path))],
                   env=_env(stub), capture_output=True, text=True, timeout=120)
    lines = seen.read_text().splitlines()
    assert lines and set(lines) == {"PYTHONHOME=<unset>"}, lines


def test_stage_dataset_runs_the_venv_without_pythonhome(tmp_path: Path) -> None:
    stub, seen = _stub_python(tmp_path)
    subprocess.run(["bash", str(FLOW / "run_stage_dataset.sh"), str(_project(tmp_path))],
                   env=_env(stub), capture_output=True, text=True, timeout=120)
    lines = seen.read_text().splitlines()
    assert lines and set(lines) == {"PYTHONHOME=<unset>"}, lines
