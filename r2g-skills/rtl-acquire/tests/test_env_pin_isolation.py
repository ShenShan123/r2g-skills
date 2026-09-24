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


def test_python_fallback_honours_the_knob(tmp_path, monkeypatch) -> None:
    # skill_env parses the pin file itself when bash is unavailable; the knob
    # must keep it out on that path too.
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import skill_env

    (tmp_path / "env.local.sh").write_text('export ORFS_ROOT="/pinned/orfs"\n')
    monkeypatch.setattr(skill_env, "REF_DIR", tmp_path)
    monkeypatch.setattr(skill_env, "ENV_SH", tmp_path / "absent_env.sh")
    assert skill_env.shared_env(refresh=True).get("ORFS_ROOT") != "/pinned/orfs"
    monkeypatch.delenv("R2G_IGNORE_ENV_LOCAL")
    assert skill_env.shared_env(refresh=True).get("ORFS_ROOT") == "/pinned/orfs"
    skill_env.shared_env(refresh=True)
