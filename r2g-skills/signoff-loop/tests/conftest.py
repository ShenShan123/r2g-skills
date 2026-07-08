"""Shared pytest fixtures for the signoff-loop knowledge-store + flow tests.

The graph dataset-conversion tests (techlib / labels / features / graph) live in the
sibling `def-graph` skill with their own conftest; this file wires only the signoff-loop
subsystems (knowledge store, signoff extractors, reports, flow, loop)."""
from __future__ import annotations

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
