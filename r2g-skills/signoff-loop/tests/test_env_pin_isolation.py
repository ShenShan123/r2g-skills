"""Tests must not run against this machine's references/env.local.sh pins.

Regression (2026-09-23, reproduced at 56129ba): every campaign worktree carries
<skill>/references/env.local.sh, and _env.sh sources it unconditionally, so 13
tests that build their own environment (a fake ORFS, a staged PDK, NUM_CORES)
ran against the pinned toolchain instead and failed only in pinned worktrees.
R2G_IGNORE_ENV_LOCAL=1 skips the pin file; each suite's conftest sets it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]


def _pin_seen(tmp_path: Path, **env: str) -> str:
    """Value of a variable only the skill's pin file sets (_env.sh re-validates
    tool paths, so a probe variable shows the sourcing itself)."""
    skill = tmp_path / "skill"
    shutil.copytree(SKILL / "scripts" / "flow", skill / "scripts" / "flow")
    (skill / "references").mkdir()
    (skill / "references" / "env.local.sh").write_text('export R2G_PIN_PROBE="from-pins"\n')
    out = subprocess.run(
        ["bash", "-c", f'source "{skill}/scripts/flow/_env.sh" >/dev/null 2>&1; '
                       'printf %s "${R2G_PIN_PROBE:-}"'],
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path), **env},
        capture_output=True, text=True, timeout=60, check=True)
    return out.stdout


def test_the_knob_keeps_the_skill_pin_file_out(tmp_path: Path) -> None:
    assert _pin_seen(tmp_path, R2G_IGNORE_ENV_LOCAL="1") == ""


def test_production_still_honours_the_pins(tmp_path: Path) -> None:
    assert _pin_seen(tmp_path) == "from-pins"


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
