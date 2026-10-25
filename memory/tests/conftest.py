"""Live tests for the TEHM modules changed by the R2G memory redesign (2026-10-01).

The historical memory/tests tree was retired (f2a0882); these cover only current,
live code. Run: python3 -m pytest -q memory/tests
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

MEMORY = Path(__file__).resolve().parents[1]
if str(MEMORY) not in sys.path:
    sys.path.insert(0, str(MEMORY))


@pytest.fixture(autouse=True)
def _isolated_process_backend_lock():
    """Each unit test models a new process; do not unlock inside a test/run."""
    for name in ('factory', 'memory.factory'):
        if name in sys.modules: sys.modules[name].reset()
    yield
    for name in ('factory', 'memory.factory'):
        if name in sys.modules: sys.modules[name].reset()
