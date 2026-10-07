"""`make lvs` cannot grade a harvested run; the frozen-layout fallback can.

A corpus run keeps only the tail of the flow — 6_final.* plus 5_route.odb —
because keeping every stage costs ~1.1 GB per design. `make lvs` then stops
before running any recipe:

    6_lvs.lvsdb: 6_final.gds + <plat>.lylvs + objects/6_final_concat.cdl
    6_final_concat.cdl: 6_final.cdl + $(CDL_FILE)     # recipe: cat $^ > $@
    6_final.cdl <- cdl.tcl, which loads 6_final.odb

because, asked for 6_final.cdl, make walks past the existing 6_final.odb into
4_cts.odb, 3_place.odb, ... and finally the synthesis inputs:
"No rule to make target '<rtl>.v'".

Note what the RMD-P0-01 preflight does here: it asks about
5_route.odb/6_final.{def,v,sdc}, which ARE up to date, so it prints nothing
and returns 0 — a 0-byte preflight log beside a `make lvs` that cannot start.
The preflight is answering a different question, which is why the fallback
triggers on make's OWN failure: stopping on an unresolvable prerequisite means
no recipe ran, so nothing was rebuilt and the layout is still the flow's own.

Observed on the 2026-10-06 nangate45 corpus run, where every design came back
`lvs_failed` while the layouts were in fact clean.
"""
import os
import shutil
import subprocess
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]

PLATFORM = "nangate45"
DESIGN = "demo"

# make stops on the unresolvable prerequisite: no recipe runs, rc 2.
UNRESOLVABLE_MAKEFILE = """\
lvs:
\t@echo "make: *** No rule to make target '/corpus/sources/demo/rtl/demo.v', needed by 'results/nangate45/demo/base/1_1_yosys_canonicalize.rtlil'.  Stop." >&2; exit 2
"""

# A real LVS verdict — the fallback must NOT fire and overwrite it.
MISMATCH_MAKEFILE = """\
lvs:
\t@echo "Netlists don't match"; exit 1
"""

# A stub openroad standing in for `write_cdl -masters`: the frozen runner
# passes the paths as command-prefix environment, so argv does not matter.
OPENROAD_STUB = """\
#!/usr/bin/env bash
set -u
[ -f "$R2G_LVS_ODB" ] || { echo "stub openroad: no odb at $R2G_LVS_ODB" >&2; exit 1; }
printf '* stub cdl from %s\\n.SUBCKT demo A B\\n.ENDS\\n' "$(basename "$R2G_LVS_ODB")" \\
  > "$R2G_LVS_CDL_OUT"
echo "stub openroad: wrote $R2G_LVS_CDL_OUT"
"""

# A stub klayout standing in for the Makefile's LVS command line.
KLAYOUT_STUB = """\
#!/usr/bin/env bash
set -u
report=""; gds=""; cdl=""
for a in "$@"; do
  case "$a" in
    report_file=*) report="${a#report_file=}" ;;
    in_gds=*)      gds="${a#in_gds=}" ;;
    cdl_file=*)    cdl="${a#cdl_file=}" ;;
  esac
done
[ -f "$gds" ] || { echo "stub klayout: no gds at $gds" >&2; exit 1; }
[ -s "$cdl" ] || { echo "stub klayout: empty concat cdl at $cdl" >&2; exit 1; }
# the concat must carry BOTH the design netlist and the platform masters
grep -q "stub cdl from" "$cdl" || { echo "stub klayout: design netlist missing" >&2; exit 1; }
grep -q "PLATFORM-MASTERS" "$cdl" || { echo "stub klayout: masters missing" >&2; exit 1; }
echo "stub lvsdb" > "$report"
echo "CONGRATULATIONS! Netlists match."
"""


def _setup(tmp_path, makefile: str):
    skill = tmp_path / "skill"
    (skill / "scripts").mkdir(parents=True)
    shutil.copytree(SKILL / "scripts" / "flow", skill / "scripts" / "flow")
    (skill / "knowledge").mkdir()
    (skill / "references").mkdir()
    (skill / "assets").mkdir()

    orfs = tmp_path / "orfs"
    flow = orfs / "flow"
    pdir = flow / "platforms" / PLATFORM
    (pdir / "lvs").mkdir(parents=True)
    (pdir / "cdl").mkdir(parents=True)
    flow.mkdir(exist_ok=True)
    (flow / "Makefile").write_text(makefile)
    # the frozen path resolves CDL masters the same way `make lvs` does
    (pdir / "config.mk").write_text(
        "export CDL_FILE = $(PLATFORM_DIR)/cdl/stdcells.cdl\n")
    (pdir / "cdl" / "stdcells.cdl").write_text("* PLATFORM-MASTERS\n.SUBCKT INV A Z\n.ENDS\n")
    (pdir / "lvs" / "FreePDK45.lylvs").write_text("# lvs deck\n")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("openroad", OPENROAD_STUB), ("klayout", KLAYOUT_STUB)):
        p = bin_dir / name
        p.write_text(body)
        p.chmod(0o755)

    proj = tmp_path / "proj"
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(f"export DESIGN_NAME = {DESIGN}\n")
    run = proj / "backend" / "RUN_A"
    (run / "results").mkdir(parents=True)
    # exactly what a corpus harvest keeps: the tail of the flow, no pre-6 stages
    for name in ("6_final.gds", "6_final.def", "6_final.v", "6_final.sdc",
                 "5_route.odb", "6_final.odb"):
        (run / "results" / name).write_text(f"content-of-{name}")
    (run / "logs").mkdir()
    (run / "logs" / "flow.log").write_text("# preserved backend log\n")
    return skill, orfs, proj, bin_dir


def _run_lvs(tmp_path, skill, orfs, proj, bin_dir, extra_env=None):
    env = dict(
        os.environ,
        ORFS_ROOT=str(orfs),
        R2G_TEST_TMP=str(tmp_path),
        R2G_JOURNAL_DB=str(tmp_path / "journal.sqlite"),
        OPENROAD_EXE=str(bin_dir / "openroad"),
        KLAYOUT_CMD=str(bin_dir / "klayout"),
        LVS_TIMEOUT="60",
    )
    env.pop("R2G_ENV_FILE", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(skill / "scripts" / "flow" / "run_lvs.sh"), str(proj), PLATFORM],
        env=env, capture_output=True, text=True, timeout=180)


def test_unresolvable_chain_falls_back_to_frozen_layout(tmp_path):
    skill, orfs, proj, bin_dir = _setup(tmp_path, UNRESOLVABLE_MAKEFILE)
    r = _run_lvs(tmp_path, skill, orfs, proj, bin_dir)
    out = r.stdout + r.stderr
    assert "frozen layout" in out, f"fallback never engaged:\n{out}"
    assert r.returncode == 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    assert "Netlists match" in (proj / "lvs" / "lvs_run.log").read_text()
    assert (proj / "lvs" / "6_lvs.lvsdb").is_file(), "no lvsdb collected"
    # the provenance sidecar is path-independent (RMD-P0-02)
    assert (proj / "lvs" / "lvs_provenance.json").is_file()
    # a tool-path defect must not be recorded as an LVS verdict
    assert not (proj / "lvs" / "lvs_result.json").is_file(), \
        (proj / "lvs" / "lvs_result.json").read_text()


def test_frozen_fallback_can_be_disabled(tmp_path):
    skill, orfs, proj, bin_dir = _setup(tmp_path, UNRESOLVABLE_MAKEFILE)
    r = _run_lvs(tmp_path, skill, orfs, proj, bin_dir,
                 extra_env={"R2G_LVS_FROZEN": "0"})
    out = r.stdout + r.stderr
    assert r.returncode != 0, "make's failure must stand when the fallback is off"
    assert "frozen path is unavailable" in out, out
    assert not (proj / "lvs" / "6_lvs.lvsdb").is_file()


def test_frozen_can_be_forced_without_make(tmp_path):
    # the Makefile would report a mismatch; forcing the frozen path skips it
    skill, orfs, proj, bin_dir = _setup(tmp_path, MISMATCH_MAKEFILE)
    r = _run_lvs(tmp_path, skill, orfs, proj, bin_dir,
                 extra_env={"R2G_LVS_FROZEN": "1"})
    out = r.stdout + r.stderr
    assert "forced by R2G_LVS_FROZEN=1" in out, out
    assert r.returncode == 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    assert "Netlists match" in (proj / "lvs" / "lvs_run.log").read_text()


def test_real_mismatch_is_not_overwritten_by_the_fallback(tmp_path):
    # The fallback keys on make being UNABLE to run, not on a non-zero rc. A
    # genuine "netlists don't match" is a verdict and must survive untouched.
    skill, orfs, proj, bin_dir = _setup(tmp_path, MISMATCH_MAKEFILE)
    r = _run_lvs(tmp_path, skill, orfs, proj, bin_dir)
    out = r.stdout + r.stderr
    assert "frozen layout" not in out, f"fallback hijacked a real verdict:\n{out}"
    assert r.returncode != 0
    assert "Netlists don't match" in (proj / "lvs" / "lvs_run.log").read_text()


def test_missing_final_odb_keeps_failing_closed(tmp_path):
    skill, orfs, proj, bin_dir = _setup(tmp_path, UNRESOLVABLE_MAKEFILE)
    (proj / "backend" / "RUN_A" / "results" / "6_final.odb").unlink()
    r = _run_lvs(tmp_path, skill, orfs, proj, bin_dir)
    out = r.stdout + r.stderr
    assert r.returncode != 0
    assert "no 6_final.odb" in out, out
