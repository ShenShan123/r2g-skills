"""Shared pytest fixtures for r2g-rtl2gds knowledge-store tests."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

# Make knowledge/ importable as plain modules — the knowledge store is a
# self-contained subsystem (data + code) at r2g-rtl2gds/knowledge/.
SKILL_ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_DIR = SKILL_ROOT / "knowledge"
if str(KNOWLEDGE_DIR) not in sys.path:
    sys.path.insert(0, str(KNOWLEDGE_DIR))

# Make scripts/extract/labels/ importable as plain modules for label-extractor tests.
LABELS_DIR = SKILL_ROOT / "scripts" / "extract" / "labels"
if str(LABELS_DIR) not in sys.path:
    sys.path.insert(0, str(LABELS_DIR))

# Make scripts/extract/features/ importable as plain modules for feature-extractor tests.
FEATURES_DIR = SKILL_ROOT / "scripts" / "extract" / "features"
if str(FEATURES_DIR) not in sys.path:
    sys.path.insert(0, str(FEATURES_DIR))

# Make scripts/extract/ importable so `import techlib.def_parse` resolves (the
# consolidated DEF/SDC parser package). Additive; Task 9 finalizes conftest.
EXTRACT_DIR = SKILL_ROOT / "scripts" / "extract"
if str(EXTRACT_DIR) not in sys.path:
    sys.path.insert(0, str(EXTRACT_DIR))


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
