"""Shared pytest fixtures for the def-graph (dataset-conversion) tests.

def-graph is self-contained: the graph extractors (techlib / labels / features / graph)
import each other via `scripts/extract/` as the common package root, exactly as the
runtime shell runners set it up. This conftest reproduces that sys.path so the tests
import the workers as plain modules.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[1]

# scripts/extract/ first so `import techlib.def_parse` (the consolidated DEF/SDC parser
# package) resolves; then the per-stage worker dirs so `import compute_feature_stats`,
# `import extract_congestion`, `import build_graphs`, `import graph_lib` etc. resolve.
for _sub in (
    SKILL_ROOT / "scripts" / "extract",
    SKILL_ROOT / "scripts" / "extract" / "labels",
    SKILL_ROOT / "scripts" / "extract" / "features",
    SKILL_ROOT / "scripts" / "extract" / "graph",
):
    if str(_sub) not in sys.path:
        sys.path.insert(0, str(_sub))


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures"
