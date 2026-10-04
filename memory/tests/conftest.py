"""Live tests for the TEHM modules changed by the R2G memory redesign (2026-10-01).

The historical memory/tests tree was retired (f2a0882); these cover only current,
live code. Run: python3 -m pytest -q memory/tests
"""
from __future__ import annotations

import sys
from pathlib import Path

MEMORY = Path(__file__).resolve().parents[1]
if str(MEMORY) not in sys.path:
    sys.path.insert(0, str(MEMORY))
