"""LVS timeout must terminate the COMPLETE checker process tree (RMD2-P0-01,
extended to run_lvs.sh 2026-07-24).

run_lvs.sh used `setsid timeout … make lvs | tee` — BOTH known liveness
defects at once: `setsid` made timeout a process-group leader and silently
disabled its tree-kill (failure-patterns #40), and `tee` held the output pipe
open, so a deep TERM-ignoring klayout grandchild could outlive the run and hang
the script exactly like the Nangate45 SHA-256 DRC pilot incident. The checker
now runs under r2g_bounded_run: own session, output direct to the run log,
TERM → grace → KILL to the whole group, and ANY session survivor is reaped
before returning (this also replaces the pattern-scoped pkill reaper that
covered the 2026-06-03 make-died-klayout-leaked case).

Harness mirrors test_run_drc_timeout_group_kill.py, except the fake ORFS ships
a real Makefile (run_lvs.sh is the Make-based path): its `lvs` recipe spawns a
TERM-ignoring parent + TERM-ignoring grandchild, and the preflight
`make --question` targets exist as staged files so the frozen-layout gate
passes rc=0.
"""
import os
import shutil
import subprocess
import time
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]

PLATFORM = "nangate45"
DESIGN = "demo"

# `lvs` recipe: TERM-ignoring shell that spawns a TERM-ignoring grandchild
# (ignored dispositions survive exec), then loops — only a group kill removes
# the tree. $(R2G_TEST_TMP) comes from the test environment; make's $$ → shell $.
MAKEFILE = """\
lvs:
\t@bash -c 'trap "" TERM; ( trap "" TERM; echo $$BASHPID > "$(R2G_TEST_TMP)/lvs_child.pid"; exec sleep 300 ) & echo $$$$ > "$(R2G_TEST_TMP)/lvs_parent.pid"; echo "extracting netlist ..."; while :; do sleep 5; done'
"""

CLEAN_MAKEFILE = """\
lvs:
\t@echo "Netlists match"
"""


def _setup(tmp_path, makefile: str):
    skill = tmp_path / "skill"
    (skill / "scripts").mkdir(parents=True)
    shutil.copytree(SKILL / "scripts" / "flow", skill / "scripts" / "flow")
    (skill / "knowledge").mkdir()
    (skill / "references").mkdir()
    # run_lvs.sh resolves the r2g-corrected sky130 rule via ../../assets — the
    # nangate45 path never uses it, but keep the dir shape valid.
    (skill / "assets").mkdir()

    orfs = tmp_path / "orfs"
    flow = orfs / "flow"
    pdir = flow / "platforms" / PLATFORM
    (pdir / "lvs").mkdir(parents=True)
    flow.mkdir(exist_ok=True)
    (flow / "Makefile").write_text(makefile)
    (pdir / "config.mk").write_text("# platform config (LVS rule found by glob)\n")
    (pdir / "lvs" / "FreePDK45.lylvs").write_text("# lvs deck\n")

    proj = tmp_path / "proj"
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(f"export DESIGN_NAME = {DESIGN}\n")
    run = proj / "backend" / "RUN_A"
    (run / "results").mkdir(parents=True)
    # The preflight `make --question` targets must exist post-restage so the
    # frozen-layout gate reads rc=0 (files with no rule are "up to date").
    for name in ("6_final.gds", "6_final.def", "6_final.v", "6_final.sdc",
                 "5_route.odb", "6_final.odb"):
        (run / "results" / name).write_text(f"content-of-{name}")
    # Real backends preserve logs/ — without it the ORFS logs dir is never
    # restaged and the cell-count `find` aborts the script under `set -e`.
    (run / "logs").mkdir()
    (run / "logs" / "flow.log").write_text("# preserved backend log\n")
    return skill, orfs, proj


def _run_lvs(tmp_path, skill, orfs, proj, extra_env=None):
    env = dict(
        os.environ,
        ORFS_ROOT=str(orfs),
        R2G_TEST_TMP=str(tmp_path),
        R2G_JOURNAL_DB=str(tmp_path / "journal.sqlite"),
    )
    env.pop("R2G_ENV_FILE", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(skill / "scripts" / "flow" / "run_lvs.sh"), str(proj), PLATFORM],
        env=env, capture_output=True, text=True, timeout=120)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_term_ignoring_lvs_tree_fully_reaped(tmp_path):
    skill, orfs, proj = _setup(tmp_path, MAKEFILE)
    t0 = time.monotonic()
    r = _run_lvs(tmp_path, skill, orfs, proj,
                 extra_env={"LVS_TIMEOUT": "2", "LVS_KILL_GRACE": "2",
                            "LVS_CRASH_RETRIES": "4"})
    elapsed = time.monotonic() - t0
    assert r.returncode == 124, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    # One bounded attempt, not 4 × timeout: 124 must break the retry loop.
    assert elapsed < 60, f"run_lvs.sh took {elapsed:.0f}s — supervisor/retry gating broken"
    assert "timed out" in (r.stdout + r.stderr)

    for name in ("lvs_parent.pid", "lvs_child.pid"):
        pidfile = tmp_path / name
        assert pidfile.is_file(), f"stub never wrote {name} — harness broken"
        pid = int(pidfile.read_text().strip())
        for _ in range(20):
            if not _alive(pid):
                break
            time.sleep(0.5)
        assert not _alive(pid), f"{name}={pid} survived run_lvs.sh (RMD2-P0-01)"

    # The checker output was captured (no tee pipeline) and preserved.
    assert "extracting netlist" in (proj / "lvs" / "lvs_run.log").read_text()


def test_clean_lvs_still_reports_and_preserves_log(tmp_path):
    skill, orfs, proj = _setup(tmp_path, CLEAN_MAKEFILE)
    r = _run_lvs(tmp_path, skill, orfs, proj, extra_env={"LVS_TIMEOUT": "60"})
    assert r.returncode == 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    assert "Netlists match" in (proj / "lvs" / "lvs_run.log").read_text()
    # Provenance sidecar still written (RMD-P0-02).
    assert (proj / "lvs" / "lvs_provenance.json").is_file()
