"""Shared pytest setup for the eda-install tests."""
from __future__ import annotations

import os


# Machine pin isolation (2026-09-23). Every campaign worktree carries
# references/env.local.sh pins, and _env.sh sources them; a test that builds its
# own environment (a fake ORFS, a staged PDK, NUM_CORES) then silently ran against
# the machine's toolchain instead -- 13 tests failed only in pinned worktrees.
# Skip the skill pin file and drop the variables such pins (or a login shell that
# sourced them) export. $R2G_ENV_FILE-based tests still set their own file.
PINNED_ENV_VARS = (
    "R2G_ENV_FILE", "ORFS_ROOT", "FLOW_DIR", "OPENROAD_EXE", "YOSYS_EXE",
    "IVERILOG_EXE", "VVP_EXE", "VERILATOR_EXE", "KLAYOUT_CMD", "STA_EXE",
    "MAGIC_EXE", "NETGEN_EXE", "PDK_ROOT", "SKY130A_DIR", "R2G_GRAPH_PYTHON",
    "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
)


# Applied at conftest import, before any test module is imported, so module-level
# probes (e.g. a skipif computed from the resolved toolchain) see the same
# isolated environment as the tests.
os.environ["R2G_IGNORE_ENV_LOCAL"] = "1"
for _name in PINNED_ENV_VARS:
    os.environ.pop(_name, None)
