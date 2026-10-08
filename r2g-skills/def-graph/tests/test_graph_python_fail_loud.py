"""A configured graph interpreter that cannot start must fail loud, never skip.

Regression (independent review, 2026-09-23): run_graphs.sh probed R2G_GRAPH_PYTHON
with `import torch, ...` only, so a pinned venv that could not even start (missing
path, broken venv, leaked PYTHONHOME) looked exactly like "torch not installed" and
exited 0 as a benign SKIP. The owner's rule is fail loud: a broken configuration
exits non-zero; only a working interpreter without torch still SKIPs.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

FLOW = Path(__file__).resolve().parents[1] / "scripts" / "flow"
GRAPH_PYTHON_BROKEN_EXIT = 4


def _stub(tmp_path: Path, *, starts: bool) -> Path:
    """A python that either cannot start, or starts but has no torch."""
    stub = tmp_path / "graph_python"
    if starts:
        body = ('if [ "$1" = -c ] && [ "$2" = pass ]; then exit 0; fi\n'
                "echo \"ModuleNotFoundError: No module named 'torch'\" >&2\nexit 1\n")
    else:
        body = "echo 'Fatal Python error: init_fs_encoding' >&2\nexit 1\n"
    stub.write_text("#!/bin/sh\n" + body)
    stub.chmod(0o755)
    return stub


def _project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = d\nexport PLATFORM = sky130hd\n")
    return proj


def _run(script: str, proj: Path, graph_python: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(FLOW / script), str(proj)],
                          env={**os.environ, "R2G_GRAPH_PYTHON": graph_python},
                          capture_output=True, text=True, timeout=120)


def test_run_graphs_fails_loud_when_the_configured_python_cannot_start(tmp_path: Path) -> None:
    stub = _stub(tmp_path, starts=False)
    proj = _project(tmp_path)
    r = _run("run_graphs.sh", proj, str(stub))
    assert r.returncode == GRAPH_PYTHON_BROKEN_EXIT, (r.returncode, r.stderr)
    assert "cannot start" in r.stderr and str(stub) in r.stderr, r.stderr
    assert "init_fs_encoding" in r.stderr          # the interpreter's own error is shown
    # A toolchain error is not a skip: no skip manifest claims the stage ran clean.
    assert not (proj / "reports" / "graph_dataset.json").exists()


def test_run_graphs_fails_loud_when_the_configured_python_is_missing(tmp_path: Path) -> None:
    r = _run("run_graphs.sh", _project(tmp_path), str(tmp_path / "gone" / "python"))
    assert r.returncode == GRAPH_PYTHON_BROKEN_EXIT, (r.returncode, r.stderr)
    assert "cannot start" in r.stderr, r.stderr


def test_run_graphs_still_skips_when_torch_is_absent(tmp_path: Path) -> None:
    proj = _project(tmp_path)
    r = _run("run_graphs.sh", proj, str(_stub(tmp_path, starts=True)))
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert "SKIP: no torch+torch_geometric" in r.stderr, r.stderr
    manifest = json.loads((proj / "reports" / "graph_dataset.json").read_text())
    assert manifest["status"] == "skipped"


def test_stage_dataset_names_an_interpreter_that_cannot_start(tmp_path: Path) -> None:
    r = _run("run_stage_dataset.sh", _project(tmp_path), str(_stub(tmp_path, starts=False)))
    assert r.returncode == 3, (r.returncode, r.stderr)
    assert "cannot start" in r.stderr and "init_fs_encoding" in r.stderr, r.stderr
    assert "cannot import torch" not in r.stderr   # the old, misleading diagnosis
