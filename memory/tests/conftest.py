"""Shared pytest fixtures for the TEHM subsystem.

Wires ``memory/`` onto ``sys.path`` so ``import tehm`` works from tests, and
provides hermetic per-test store fixtures (temp sqlite + temp artifact root) so
no test touches a real TEHM DB or the legacy knowledge store.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# memory/ is the TEHM package root (parent of tests/).
MEMORY_ROOT = Path(__file__).resolve().parents[1]
if str(MEMORY_ROOT) not in sys.path:
    sys.path.insert(0, str(MEMORY_ROOT))

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def tmp_tehm(tmp_path: Path):
    """A fresh TEHM store: (conn, artifact_store, tmp_path)."""
    from tehm import db
    from tehm.artifact_store import ArtifactStore

    conn = db.connect(tmp_path / "tehm.sqlite")
    db.ensure_schema(conn)
    store = ArtifactStore(tmp_path / "artifacts")
    return conn, store, tmp_path


@pytest.fixture
def sample_record_dict() -> dict:
    """The canonical sample ExecutionRecord dict (antenna DRC fix)."""
    return json.loads((FIXTURES_DIR / "sample_antenna_fix_record.json").read_text())


@pytest.fixture
def sample_record(sample_record_dict):
    """The sample ExecutionRecord, validated."""
    from tehm.canonical.capture import ExecutionRecord

    return ExecutionRecord.from_dict(sample_record_dict)


@pytest.fixture(autouse=True)
def _reset_backend_lock():
    """Clear the process backend lock so each test may open any backend."""
    import factory

    factory.reset()
    yield
    factory.reset()


def deepcopy_dict(obj: dict) -> dict:
    """Recursive dict copy for tests that mutate a fixture."""
    return json.loads(json.dumps(obj))
