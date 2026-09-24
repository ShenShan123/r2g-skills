"""This suite must not see the machine's references/env.local.sh pins.

Regression (2026-09-23): tests building their own environment ran against the
pinned toolchain in campaign worktrees. conftest.py sets R2G_IGNORE_ENV_LOCAL=1
and drops the pinned variables before any test module is imported.
"""
from __future__ import annotations

import os


def test_this_suite_runs_isolated() -> None:
    assert os.environ.get("R2G_IGNORE_ENV_LOCAL") == "1"
    for name in ("ORFS_ROOT", "PDK_ROOT", "OPENROAD_EXE", "OMP_NUM_THREADS", "R2G_ENV_FILE"):
        assert name not in os.environ, name


def test_conftest_scrubs_pinned_variables_it_is_given() -> None:
    # Independent of the ambient shell: hand conftest pinned values and check that
    # importing it removes every one and sets the knob.
    import subprocess
    import sys
    from pathlib import Path

    conftest = Path(__file__).resolve().parent / "conftest.py"
    pinned = {"ORFS_ROOT": "/pinned/orfs", "PDK_ROOT": "/pinned/pdk",
              "OPENROAD_EXE": "/pinned/openroad", "OMP_NUM_THREADS": "99",
              "R2G_ENV_FILE": "/pinned/env.sh", "R2G_GRAPH_PYTHON": "/pinned/python"}
    code = ("import os, runpy, sys; runpy.run_path(sys.argv[1]); "
            "left = [k for k in sys.argv[2:] if k in os.environ]; "
            "assert not left, left; "
            "assert os.environ.get('R2G_IGNORE_ENV_LOCAL') == '1'")
    env = {k: v for k, v in os.environ.items() if k != "R2G_IGNORE_ENV_LOCAL"}
    env.update(pinned)
    r = subprocess.run([sys.executable, "-c", code, str(conftest), *pinned],
                       env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
