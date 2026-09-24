"""_env.sh must pin the OpenMP / MKL / OpenBLAS pools to NUM_CORES.

Regression (CORRECTIONS #13, 2026-09-22): ORFS bounds openroad with
`-threads $(NUM_CORES)`, which does not bound the OpenMP/MKL pools inside it.
At NUM_CORES=4 one openroad still ran at 1,020-1,170% CPU; 8 such flows put
2,458 threads on 152 cores. Capping OMP/MKL to 4 brought it to 130-220%. The
documented per-flow budget only held if the caller knew to set three more
variables, which the skill never mentioned.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

ENV_SH = Path(__file__).resolve().parents[1] / "scripts" / "flow" / "_env.sh"
POOLS = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")


def _pools_after_sourcing(**env: str) -> dict[str, str]:
    base = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp"),
            "R2G_IGNORE_ENV_LOCAL": "1", **env}
    out = subprocess.run(
        ["bash", "-c", f'source "{ENV_SH}" >/dev/null 2>&1; '
                       + "; ".join(f'echo "{v}=${{{v}-}}"' for v in POOLS)],
        env=base, capture_output=True, text=True, timeout=60, check=True)
    return dict(line.split("=", 1) for line in out.stdout.splitlines())


def test_pools_follow_num_cores() -> None:
    assert _pools_after_sourcing(NUM_CORES="4") == {v: "4" for v in POOLS}


def test_an_explicit_pool_setting_wins() -> None:
    pools = _pools_after_sourcing(NUM_CORES="4", OMP_NUM_THREADS="1")
    assert pools["OMP_NUM_THREADS"] == "1"
    assert pools["MKL_NUM_THREADS"] == "4"


def test_default_budget_is_the_affinity_mask() -> None:
    # Without NUM_CORES the budget is ORFS's own default, nproc, which honours a
    # cpuset: `taskset -c 0-1` gives 2 threads per pool.
    out = subprocess.run(
        ["taskset", "-c", "0-1", "bash", "-c",
         f'source "{ENV_SH}" >/dev/null 2>&1; echo "$OMP_NUM_THREADS $MKL_NUM_THREADS"'],
        env={"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp"),
             "R2G_IGNORE_ENV_LOCAL": "1"},
        capture_output=True, text=True, timeout=60, check=True)
    assert out.stdout.split() == ["2", "2"]
