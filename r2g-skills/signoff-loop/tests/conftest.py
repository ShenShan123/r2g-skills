"""Shared pytest fixtures for the signoff-loop knowledge-store + flow tests.

The graph dataset-conversion tests (techlib / labels / features / graph) live in the
sibling `def-graph` skill with their own conftest; this file wires only the signoff-loop
subsystems (knowledge store, signoff extractors, reports, flow, loop)."""
from __future__ import annotations

import contextlib
import os
import shutil
import sys
from pathlib import Path

import pytest

# Make knowledge/ importable as plain modules — the knowledge store is a
# self-contained subsystem (data + code) at signoff-loop/knowledge/.
SKILL_ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_DIR = SKILL_ROOT / "knowledge"
if str(KNOWLEDGE_DIR) not in sys.path:
    sys.path.insert(0, str(KNOWLEDGE_DIR))

# Make scripts/extract/ importable so the signoff extractors and presynth.py resolve
# their bare `import report_io` / `import presynth`.
EXTRACT_DIR = SKILL_ROOT / "scripts" / "extract"
if str(EXTRACT_DIR) not in sys.path:
    sys.path.insert(0, str(EXTRACT_DIR))

# Make scripts/reports/ importable for signoff-fixer tests.
REPORTS_DIR = SKILL_ROOT / "scripts" / "reports"
if str(REPORTS_DIR) not in sys.path:
    sys.path.insert(0, str(REPORTS_DIR))

# Make scripts/flow/ importable for flow-helper tests (e.g. antenna_lef_patch).
FLOW_DIR_SCRIPTS = SKILL_ROOT / "scripts" / "flow"
if str(FLOW_DIR_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(FLOW_DIR_SCRIPTS))

# Make scripts/loop/ importable for engineer-loop orchestrator tests.
LOOP_DIR_SCRIPTS = SKILL_ROOT / "scripts" / "loop"
if str(LOOP_DIR_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(LOOP_DIR_SCRIPTS))


@pytest.fixture(autouse=True)
def _isolate_journal(tmp_path: Path, monkeypatch) -> None:
    """Redirect ALL best-effort journal writes to a per-test temp DB so unit tests
    never touch the real knowledge/journal.sqlite. The journal-decision writers
    (ab_runner.record_trial, escalations.open_escalation, engineer_loop ab_launch,
    fix_signoff.sh) honor R2G_JOURNAL_DB; tests that set their own R2G_JOURNAL_DB
    (the subprocess journaling tests) override this via env_extra. Journaling stays
    best-effort, so a write here that fails still never breaks the test."""
    monkeypatch.setenv("R2G_JOURNAL_DB", str(tmp_path / "_isolated_journal.sqlite"))


@pytest.fixture
def tmp_knowledge_dir(tmp_path: Path) -> Path:
    """A throw-away knowledge/ directory with the real schema + families seed."""
    kdir = tmp_path / "knowledge"
    kdir.mkdir()
    shutil.copy(SKILL_ROOT / "knowledge" / "schema.sql", kdir / "schema.sql")
    shutil.copy(SKILL_ROOT / "knowledge" / "families.json", kdir / "families.json")
    return kdir


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures"


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
# What the scrub removed, so the tests that DELIBERATELY resolve the production
# toolchain (the capability probes: "a suite of skips reads exactly like a suite
# of passes", RMD3-P1-02) can restore it through production_toolchain_env().
ORIGINAL_PINNED_ENV = {k: os.environ[k] for k in PINNED_ENV_VARS if k in os.environ}
os.environ["R2G_IGNORE_ENV_LOCAL"] = "1"
for _name in PINNED_ENV_VARS:
    os.environ.pop(_name, None)


@contextlib.contextmanager
def production_toolchain_env():
    """Resolve the toolchain exactly as production does: the skill pin file is
    honoured and the caller's pinned variables are back. Restores isolation on exit."""
    keys = ("R2G_IGNORE_ENV_LOCAL", *PINNED_ENV_VARS)
    saved = {k: os.environ.get(k) for k in keys}
    os.environ.pop("R2G_IGNORE_ENV_LOCAL", None)
    os.environ.update(ORIGINAL_PINNED_ENV)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture
def production_toolchain():
    with production_toolchain_env():
        yield
