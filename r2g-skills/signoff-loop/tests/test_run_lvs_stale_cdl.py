"""LVS must grade the layout against ITS OWN netlist, never a leftover one.

The schematic KLayout compares against is derived from the layout under test:

    6_final.cdl: 6_final.v          # recipe: cdl.tcl, which loads 6_final.odb
    6_final_concat.cdl: 6_final.cdl + $(CDL_FILE)
    6_lvs.lvsdb: 6_final.gds + objects/6_final_concat.cdl

No ORFS clean target deletes 6_final.cdl, and the restage stamps every 6_*
result at one epoch. After a signoff repair re-ran route, the PRE-repair
6_final.cdl therefore read as up to date beside the new 6_final.v, make only
re-catted it, and KLayout graded the repaired GDS against the old netlist.
Observed 2026-10-28 on nangate45 C00392/C00838/C00458 (174/7/10349
mismatches); all three match against a CDL written from their own odb.
"""
import os
import shutil
import subprocess
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]

PLATFORM = "nangate45"
DESIGN = "demo"
VARIANT = "proj"

# The real ORFS dependency chain, with stub recipes. The cdl recipe marks its
# output so the checker can tell a regenerated netlist from a leftover one.
MAKEFILE = f"""\
R := results/{PLATFORM}/{DESIGN}/$(FLOW_VARIANT)
O := objects/{PLATFORM}/{DESIGN}/$(FLOW_VARIANT)

lvs: $(R)/6_lvs.lvsdb

$(R)/6_final.cdl: $(R)/6_final.v
\t@echo "* cdl regenerated from 6_final.odb" > $@

$(O)/6_final_concat.cdl: $(R)/6_final.cdl
\t@mkdir -p $(O); cat $^ > $@

$(R)/6_lvs.lvsdb: $(R)/6_final.gds $(O)/6_final_concat.cdl
\t@if grep -q regenerated $(O)/6_final_concat.cdl; then \\
\t  echo "CONGRATULATIONS! Netlists match."; echo lvsdb > $@; \\
\telse echo "ERROR : Netlists don't match"; exit 1; fi
"""


def _setup(tmp_path):
    skill = tmp_path / "skill"
    (skill / "scripts").mkdir(parents=True)
    shutil.copytree(SKILL / "scripts" / "flow", skill / "scripts" / "flow")
    for d in ("knowledge", "references", "assets"):
        (skill / d).mkdir()

    flow = tmp_path / "orfs" / "flow"
    pdir = flow / "platforms" / PLATFORM
    (pdir / "lvs").mkdir(parents=True)
    (pdir / "cdl").mkdir(parents=True)
    (flow / "Makefile").write_text(MAKEFILE)
    (pdir / "config.mk").write_text("export CDL_FILE = $(PLATFORM_DIR)/cdl/stdcells.cdl\n")
    (pdir / "cdl" / "stdcells.cdl").write_text("* masters\n")
    (pdir / "lvs" / "FreePDK45.lylvs").write_text("# lvs deck\n")

    proj = tmp_path / VARIANT
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(f"export DESIGN_NAME = {DESIGN}\n")
    run = proj / "backend" / "RUN_B"
    (run / "results").mkdir(parents=True)
    for name in ("6_final.gds", "6_final.def", "6_final.v", "6_final.sdc",
                 "5_route.odb", "6_final.odb"):
        (run / "results" / name).write_text(f"repaired-{name}")
    (run / "logs").mkdir()
    (run / "logs" / "flow.log").write_text("# repaired run\n")

    # What the pre-repair LVS left in the shared ORFS workspace.
    results = flow / "results" / PLATFORM / DESIGN / VARIANT
    objects = flow / "objects" / PLATFORM / DESIGN / VARIANT
    results.mkdir(parents=True)
    objects.mkdir(parents=True)
    (results / "6_final.cdl").write_text("* pre-repair netlist\n")
    (results / "6_lvs.lvsdb").write_text("pre-repair lvsdb\n")
    (objects / "6_final_concat.cdl").write_text("* pre-repair netlist\n* masters\n")
    return skill, flow.parent, proj, results


def test_leftover_pre_repair_cdl_is_not_graded(tmp_path):
    skill, orfs, proj, results = _setup(tmp_path)
    env = dict(os.environ, ORFS_ROOT=str(orfs), R2G_TEST_TMP=str(tmp_path),
               R2G_JOURNAL_DB=str(tmp_path / "journal.sqlite"), LVS_TIMEOUT="60",
               R2G_LVS_FROZEN="0")
    env.pop("R2G_ENV_FILE", None)
    r = subprocess.run(["bash", str(skill / "scripts" / "flow" / "run_lvs.sh"),
                        str(proj), PLATFORM],
                       env=env, capture_output=True, text=True, timeout=180)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "Netlists match" in (proj / "lvs" / "lvs_run.log").read_text(), out
    assert "regenerated" in (results / "6_final.cdl").read_text()
    # the collected copy is the netlist that was actually graded
    assert "regenerated" in (proj / "lvs" / "6_final.cdl").read_text()
