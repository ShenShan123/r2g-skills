"""ORFS_MAX_CPUS is a thread cap, never `taskset -c 0-(N-1)`.

Regression (CORRECTIONS #17, 2026-09-22): run_orfs.sh pinned every flow to cores
0..N-1, so N concurrent flows all contended for the SAME N cores (15 flows at
ORFS_MAX_CPUS=4 drove loadavg past 700). Now it sets NUM_CORES, and _env.sh sizes
the OpenMP/MKL/OpenBLAS pools from that. Pinning happens only for an explicit
ORFS_CPU_SET.

Harness: a hermetic copy of scripts/flow/ against a fake ORFS checkout. A stub
`make` first in PATH records the budget it sees and the CPUs it may run on (last call wins),
then fails the stage; nothing real is built.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]


def _run_orfs(tmp_path: Path, **env_extra: str) -> list[str]:
    skill = tmp_path / "skill"
    (skill / "scripts").mkdir(parents=True)
    shutil.copytree(SKILL / "scripts" / "flow", skill / "scripts" / "flow")
    (skill / "knowledge").mkdir()
    flow = tmp_path / "orfs" / "flow"
    (flow / "platforms" / "nangate45").mkdir(parents=True)
    (flow / "Makefile").write_text("# fake\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    seen = tmp_path / "make_env.txt"
    make = bindir / "make"
    make.write_text("#!/usr/bin/env bash\n"
                    f'echo "$NUM_CORES|$OMP_NUM_THREADS|$MKL_NUM_THREADS|$(env -u OMP_NUM_THREADS -u OMP_THREAD_LIMIT nproc)" > "{seen}"\n'
                    "exit 2\n")
    make.chmod(make.stat().st_mode | stat.S_IXUSR)

    proj = tmp_path / "proj"
    (proj / "constraints").mkdir(parents=True)
    (proj / "rtl").mkdir()
    (proj / "rtl" / "w.v").write_text("module w(); endmodule\n")
    sdc = proj / "constraints" / "constraint.sdc"
    sdc.write_text("create_clock -period 10\n")
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = w\nexport PLATFORM = nangate45\n"
        f"export VERILOG_FILES = {proj / 'rtl' / 'w.v'}\nexport SDC_FILE = {sdc}\n")

    env = {k: v for k, v in os.environ.items()
           if k not in ("NUM_CORES", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
                        "OPENBLAS_NUM_THREADS", "R2G_ENV_FILE")}
    env.update(ORFS_ROOT=str(tmp_path / "orfs"), PATH=f"{bindir}:{os.environ['PATH']}",
               R2G_LOCK_DIR=str(tmp_path), R2G_JOURNAL_DB=str(tmp_path / "j.sqlite"),
               ORFS_STAGES="synth", **env_extra)
    subprocess.run(["bash", str(skill / "scripts" / "flow" / "run_orfs.sh"),
                    str(proj), "nangate45", "v1"],
                   env=env, capture_output=True, text=True, timeout=120)
    assert seen.exists(), "the stub make never ran"
    return seen.read_text().strip().split("|")


def test_max_cpus_caps_threads_without_pinning(tmp_path: Path) -> None:
    num_cores, omp, mkl, allowed = _run_orfs(tmp_path, ORFS_MAX_CPUS="3")
    assert (num_cores, omp, mkl) == ("3", "3", "3")
    # Not pinned to cores 0..2: the stage may run on every CPU this test may use.
    assert int(allowed) == len(os.sched_getaffinity(0))


def test_explicit_num_cores_wins(tmp_path: Path) -> None:
    num_cores, omp, _mkl, _allowed = _run_orfs(tmp_path, ORFS_MAX_CPUS="3", NUM_CORES="2")
    assert (num_cores, omp) == ("2", "2")


def test_an_explicit_cpu_set_still_pins(tmp_path: Path) -> None:
    cpu = min(os.sched_getaffinity(0))
    _num_cores, _omp, _mkl, allowed = _run_orfs(tmp_path, ORFS_MAX_CPUS="3",
                                                ORFS_CPU_SET=str(cpu))
    assert allowed == "1"
