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
