"""run_drc.sh must never grade, or kill, another run's DRC (CORRECTIONS #16).

1. With $3 omitted FLOW_VARIANT is the project basename, so base/<task> and
   fix/<task> share one ORFS workspace. Graded concurrently they restaged into one
   results dir and graded each other's GDS. The checker now holds the same
   workspace lock as run_orfs.sh from restage to verdict.
2. The stuck-DRC path ran a UID-wide `pkill -9 -f "klayout.*${FLOW_VARIANT}.*6_drc"`,
   which killed any other session's KLayout whose command line contained the
   variant. r2g_bounded_run already reaps the checker's own session.

Reuses the hermetic harness of test_run_drc_checker_only.py.
"""
from __future__ import annotations

import fcntl
import hashlib
import subprocess
import time
from pathlib import Path

from .test_run_drc_checker_only import DESIGN, PLATFORM, _make_exec, _run_drc, _setup


def test_checker_refuses_a_workspace_another_run_holds(tmp_path: Path) -> None:
    skill, orfs, proj, bindir, _deck = _setup(tmp_path)
    lockdir = tmp_path / "locks"
    lockdir.mkdir()
    variant = proj.name                     # the $3-omitted default
    key = f"{PLATFORM}/{DESIGN}/{variant}".encode()
    lockfile = lockdir / f"r2g_ws_{hashlib.md5(key).hexdigest()}.lock"

    with open(lockfile, "w") as held:       # e.g. the fix/<task> run grading now
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = _run_drc(tmp_path, skill, orfs, proj, bindir,
                     extra_env={"R2G_LOCK_DIR": str(lockdir)})

    assert r.returncode != 0
    assert "another run holds the ORFS workspace" in r.stderr
    assert not (tmp_path / "klayout_invocations").exists()   # nothing was graded
    assert not (orfs / "flow" / "results" / PLATFORM / DESIGN / variant / "6_final.gds").exists()

    # Once the other run releases the workspace the same call grades normally.
    r2 = _run_drc(tmp_path, skill, orfs, proj, bindir,
                  extra_env={"R2G_LOCK_DIR": str(lockdir)})
    assert r2.returncode == 0, r2.stderr


def test_stuck_drc_does_not_kill_other_sessions_klayout(tmp_path: Path) -> None:
    skill, orfs, proj, bindir, _deck = _setup(tmp_path)
    # A stuck checker: names a deck rule, writes no report database.
    _make_exec(bindir / "klayout",
               '#!/usr/bin/env bash\n'
               'if [[ "${1:-}" == "-v" ]]; then echo "KLayout 0.0.stub"; exit 0; fi\n'
               'echo "FreePDK45.lydrc:123"; exit 1\n')
    # Another session's DRC whose command line contains our variant ("proj").
    decoy = subprocess.Popen(
        ["bash", "-c", 'exec -a "klayout -rd in_gds=/elsewhere/proj/6_final.gds '
                       '-rd report_file=/elsewhere/6_drc.lyrdb" sleep 120'],
        start_new_session=True)
    try:
        time.sleep(0.3)
        r = _run_drc(tmp_path, skill, orfs, proj, bindir,
                     extra_env={"R2G_LOCK_DIR": str(tmp_path)})
        assert r.returncode != 0                    # stuck is a failed check ...
        assert '"status": "stuck"' in (proj / "drc" / "drc_result.json").read_text()
        assert decoy.poll() is None, "run_drc.sh killed another session's KLayout"
    finally:
        decoy.kill()
        decoy.wait()
