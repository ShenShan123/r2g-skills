"""Netgen-LVS timeouts must terminate the COMPLETE tool tree (RMD2-P0-01,
extended to run_netgen_lvs.sh 2026-07-24).

run_netgen_lvs.sh had the last remaining `setsid timeout … | tee` sites after
the run_drc.sh / run_lvs.sh migrations: the Magic SPICE extraction combined
BOTH known liveness defects (#40's disabled tree-kill + the outlivable tee
pipe), and the Netgen compare ran `timeout … | tee` whose pipe a TERM-ignoring
descendant could hold open past expiry. Both steps (plus the OpenROAD
powered-netlist write) now run under r2g_bounded_run: own session, output
direct to the step log, TERM → grace → KILL to the whole group, any session
survivor reaped before returning.

Harness: fake ORFS results + fake sky130A PDK collateral, stub MAGIC_EXE /
NETGEN_EXE binaries that ignore SIGTERM and spawn a TERM-ignoring grandchild
(the PPID=1 orphan shape of the pilot incident).
"""
import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]

PLATFORM = "sky130hd"
DESIGN = "demo"

# TERM-ignoring tool + TERM-ignoring grandchild: only a group kill removes it.
STUCK_TOOL = """#!/usr/bin/env bash
trap '' TERM
( trap '' TERM; echo $BASHPID > "{TMP}/{NAME}_child.pid"; exec sleep 300 ) &
echo $$ > "{TMP}/{NAME}_parent.pid"
echo "{NAME} grinding ..."
while :; do sleep 5; done
"""

# A magic that SUCCEEDS: parses the ext2spice output path from the extract TCL
# (its last argument) and writes a minimal two-port top subckt so the portless
# guard and diode normalizer both pass.
OK_MAGIC = """#!/usr/bin/env bash
tcl="${@: -1}"
out=$(sed -n 's/^ext2spice -o "\\(.*\\)"$/\\1/p' "$tcl")
printf '.subckt demo a b\\nX0 a b sky130_fd_sc_hd__inv_1\\n.ends\\n' > "$out"
echo "magic extraction ok"
"""


def _make_exec(path: Path, text: str):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _setup(tmp_path, magic_body: str, netgen_body: str):
    skill = tmp_path / "skill"
    (skill / "scripts").mkdir(parents=True)
    shutil.copytree(SKILL / "scripts" / "flow", skill / "scripts" / "flow")
    (skill / "knowledge").mkdir()
    (skill / "references").mkdir()

    orfs = tmp_path / "orfs"
    flow = orfs / "flow"
    rdir = flow / "results" / PLATFORM / DESIGN / "proj"
    rdir.mkdir(parents=True)
    (flow / "Makefile").write_text("# fake ORFS Makefile\n")
    (rdir / "6_final.gds").write_text("gds-bytes")
    (rdir / "6_final.v").write_text("module demo(); endmodule\n")
    # deliberately NO 6_final.odb -> the powered-netlist OpenROAD step is skipped

    pdk = tmp_path / "pdk"
    (pdk / "sky130A" / "libs.tech" / "magic").mkdir(parents=True)
    (pdk / "sky130A" / "libs.tech" / "netgen").mkdir(parents=True)
    (pdk / "sky130A" / "libs.tech" / "magic" / "sky130A.tech").write_text("# tech\n")
    (pdk / "sky130A" / "libs.tech" / "netgen" / "sky130A_setup.tcl").write_text("# setup\n")

    bindir = tmp_path / "bin"
    bindir.mkdir()
    _make_exec(bindir / "magic", magic_body)
    _make_exec(bindir / "netgen", netgen_body)

    proj = tmp_path / "proj"
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(f"export DESIGN_NAME = {DESIGN}\n")
    run = proj / "backend" / "RUN_A"
    (run / "results").mkdir(parents=True)
    (run / "results" / "6_final.gds").write_text("gds-bytes")
    return skill, orfs, pdk, bindir, proj


def _run(tmp_path, skill, orfs, pdk, bindir, proj):
    env = dict(
        os.environ,
        ORFS_ROOT=str(orfs),
        PDK_ROOT=str(pdk),
        MAGIC_EXE=str(bindir / "magic"),
        NETGEN_EXE=str(bindir / "netgen"),
        NETGEN_TIMEOUT="2",
        NETGEN_KILL_GRACE="2",
    )
    env.pop("R2G_ENV_FILE", None)
    return subprocess.run(
        ["bash", str(skill / "scripts" / "flow" / "run_netgen_lvs.sh"),
         str(proj), PLATFORM],
        env=env, capture_output=True, text=True, timeout=120)


def _assert_dead(tmp_path, name):
    pidfile = tmp_path / name
    assert pidfile.is_file(), f"stub never wrote {name} — harness broken"
    pid = int(pidfile.read_text().strip())
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.5)
    raise AssertionError(f"{name}={pid} survived run_netgen_lvs.sh (RMD2-P0-01)")


def test_magic_extraction_timeout_reaps_tree_and_records_error(tmp_path):
    stuck = STUCK_TOOL.replace("{TMP}", str(tmp_path)).replace("{NAME}", "magic")
    skill, orfs, pdk, bindir, proj = _setup(tmp_path, stuck, "#!/usr/bin/env bash\nexit 0\n")
    t0 = time.monotonic()
    r = _run(tmp_path, skill, orfs, pdk, bindir, proj)
    elapsed = time.monotonic() - t0
    assert r.returncode == 1, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    assert elapsed < 60, f"took {elapsed:.0f}s — supervisor did not bound the run"
    _assert_dead(tmp_path, "magic_parent.pid")
    _assert_dead(tmp_path, "magic_child.pid")
    # The M2 contract holds: the timeout reason lands in the result JSON.
    result = json.loads((proj / "lvs" / "netgen_lvs_result.json").read_text())
    assert result["status"] == "error"
    assert "timeout" in result["reason"]
    # Output captured directly (no tee pipeline).
    assert "magic grinding" in (proj / "lvs" / "magic_extract.log").read_text()


def test_netgen_compare_timeout_reaps_tree(tmp_path):
    stuck = STUCK_TOOL.replace("{TMP}", str(tmp_path)).replace("{NAME}", "netgen")
    skill, orfs, pdk, bindir, proj = _setup(tmp_path, OK_MAGIC, stuck)
    r = _run(tmp_path, skill, orfs, pdk, bindir, proj)
    assert r.returncode == 124, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    _assert_dead(tmp_path, "netgen_parent.pid")
    _assert_dead(tmp_path, "netgen_child.pid")
    # The extraction step genuinely ran in its scratch dir and produced SPICE.
    assert (proj / "lvs" / "extracted.spice").is_file()
    assert "netgen grinding" in (proj / "lvs" / "netgen_lvs.log").read_text()
