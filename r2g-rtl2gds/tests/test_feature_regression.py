"""Golden regression gate for the feature-extraction workers.

Each refactored worker is run against the feature_test_v2/input/ac97_top fixture
(5_route.def + 6_final.spef + constraint.sdc + config.mk) with the bundled Nangate
liberty/tech-lef injected via env, and its output is asserted byte-for-byte equal to the
feature_test_v2/output/ac97_top/*.csv golden. This guards the light refactor +
parameterization against any behavior drift on nangate45.

feature_test_v2/ is an external (untracked) dev fixture, not part of the repo; this test
is skipped when it is absent. Restore it under the repo root to re-run the golden check.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_ROOT.parent
FEATURES_SRC = SKILL_ROOT / "scripts" / "extract" / "features"
FIX = REPO_ROOT / "feature_test_v2" / "input" / "ac97_top"
GOLDEN = REPO_ROOT / "feature_test_v2" / "output" / "ac97_top"
LIB = REPO_ROOT / "feature_test_v2" / "input" / "NangateOpenCellLibrary_typical.lib"
TLEF = REPO_ROOT / "feature_test_v2" / "input" / "NangateOpenCellLibrary.tech.lef"

WORKERS = [
    "metadata", "nodes_gate", "nodes_net", "nodes_iopin", "nodes_pin",
    "edges_gate_pin", "edges_pin_net", "edges_iopin_net",
]

pytestmark = pytest.mark.skipif(
    not (FIX.is_dir() and GOLDEN.is_dir() and LIB.is_file()),
    reason="feature_test_v2 ac97_top fixture/golden not present",
)


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("worker", WORKERS)
def test_worker_reproduces_golden(worker, tmp_path):
    out = tmp_path / f"{worker}.csv"
    env = dict(os.environ)
    env.update({
        "R2G_SDC": str(FIX / "constraint.sdc"),
        "R2G_SPEF": str(FIX / "6_final.spef"),
        "R2G_CONFIG": str(FIX / "config.mk"),
        "R2G_LIB_FILES": str(LIB),
        "R2G_TECH_LEF": str(TLEF),
        "R2G_PLATFORM": "nangate45",
    })
    res = subprocess.run(
        [sys.executable, str(FEATURES_SRC / f"{worker}.py"),
         str(FIX / "5_route.def"), str(out), "ac97_top"],
        env=env, capture_output=True, text=True,
    )
    assert res.returncode == 0, f"{worker} exited {res.returncode}: {res.stderr}"
    assert out.is_file(), f"{worker} produced no output"
    assert _md5(out) == _md5(GOLDEN / f"{worker}.csv"), (
        f"{worker}.csv differs from golden (refactor changed nangate45 output)"
    )
