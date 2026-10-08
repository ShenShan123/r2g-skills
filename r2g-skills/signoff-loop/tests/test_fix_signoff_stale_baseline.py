"""fix_signoff.sh _ensure_baseline staleness (2026-06-18).

A freshly-produced GDS needs fresh signoff. If a backend GDS is NEWER than the
stored signoff report, the report is STALE (the design was re-flowed since the
last signoff) and the baseline tool MUST re-run — even when the stale status is a
definite 'fail'. Without this, an A/B arm dir (copied from the base project with
its old KLayout lvs.json='fail') keeps the stale verdict and the real tool (Netgen
on sky130) never runs on the new layout, yielding a false escalation.
"""
from __future__ import annotations
import json
import os
import stat
import subprocess
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
FIX_SIGNOFF = SKILL / "scripts" / "flow" / "fix_signoff.sh"


def _stub(path: Path, body: str):
    path.write_text("#!/usr/bin/env bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _mk_proj(tmp_path: Path, *, gds_newer: bool) -> Path:
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = sky130hd\n")
    # A stale, DEFINITE 'fail' report (the copied KLayout verdict).
    report = proj / "reports" / "lvs.json"
    report.write_text(json.dumps({"status": "fail", "mismatch_count": None}))
    gds = proj / "backend" / "RUN_2026-06-18_00-00-00" / "final" / "demo.gds"
    gds.parent.mkdir(parents=True)
    gds.write_text("FAKE GDS")
    # Order the mtimes deterministically.
    if gds_newer:
        os.utime(report, (1_000_000, 1_000_000))
        os.utime(gds, (2_000_000, 2_000_000))      # reflow happened after signoff
    else:
        os.utime(gds, (1_000_000, 1_000_000))
        os.utime(report, (2_000_000, 2_000_000))   # signoff is fresh
    return proj


def _run(proj: Path, tmp_path: Path):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    # RUN_LVS stub: record that the baseline tool was invoked.
    _stub(bindir / "run_lvs.sh", f'touch "{proj}/LVS_RAN"\nexit 0')
    # EXTRACT_LVS stub: after a (re)run, report is clean.
    _stub(bindir / "extract_lvs.py",
          'python3 - "$@" <<\'PY\'\nimport json,sys\n'
          'open(sys.argv[2],"w").write(json.dumps({"status":"clean","mismatch_count":0}))\nPY')
    # diagnose --next: STOP immediately (we only exercise _ensure_baseline).
    _stub(bindir / "diagnose.py", 'echo -e "STOP\\tclean\\tdone"')
    env = dict(os.environ,
               R2G_RUN_LVS=str(bindir / "run_lvs.sh"),
               R2G_EXTRACT_LVS=str(bindir / "extract_lvs.py"),
               R2G_DIAGNOSE=str(bindir / "diagnose.py"))
    subprocess.run(["bash", str(FIX_SIGNOFF), str(proj), "sky130hd",
                    "--check", "lvs", "--max-iters", "1"], env=env, check=False)


def test_stale_fail_report_with_newer_gds_reruns_baseline(tmp_path):
    proj = _mk_proj(tmp_path, gds_newer=True)
    _run(proj, tmp_path)
    assert (proj / "LVS_RAN").exists(), \
        "baseline LVS must re-run when GDS is newer than the stale 'fail' report"


def test_fresh_fail_report_does_not_rerun_baseline(tmp_path):
    proj = _mk_proj(tmp_path, gds_newer=False)
    _run(proj, tmp_path)
    assert not (proj / "LVS_RAN").exists(), \
        "a fresh (newer-than-GDS) 'fail' report is a genuine verdict — no baseline re-run"


def _run_both(proj: Path, tmp_path: Path, *, lvs_reflows: bool):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    gds2 = proj / "backend" / "RUN_2026-10-01_00-00-01" / "final" / "demo.gds"
    reflow = (f'mkdir -p "{gds2.parent}"; echo NEW > "{gds2}"; touch -d "+1 hour" "{gds2}"\n'
              if lvs_reflows else "")
    _stub(bindir / "run_lvs.sh", f'{reflow}exit 0')
    _stub(bindir / "run_drc.sh", f'touch "{proj}/DRC_RAN"\nexit 0')
    for tool in ("lvs", "drc"):
        _stub(bindir / f"extract_{tool}.py",
              'python3 - "$@" <<\'PY\'\nimport json,sys\n'
              'open(sys.argv[2],"w").write(json.dumps({"status":"clean","total_violations":0,'
              '"mismatch_count":0}))\nPY')
    _stub(bindir / "diagnose.py", 'echo -e "STOP\\tclean\\tdone"')
    env = dict(os.environ,
               R2G_RUN_LVS=str(bindir / "run_lvs.sh"), R2G_RUN_DRC=str(bindir / "run_drc.sh"),
               R2G_EXTRACT_LVS=str(bindir / "extract_lvs.py"),
               R2G_EXTRACT_DRC=str(bindir / "extract_drc.py"),
               R2G_DIAGNOSE=str(bindir / "diagnose.py"))
    subprocess.run(["bash", str(FIX_SIGNOFF), str(proj), "sky130hd",
                    "--check", "both", "--max-iters", "1"], env=env, check=False)


def test_lvs_phase_reflow_regrades_drc_on_new_layout(tmp_path):
    """2026-10-01 AIC v2.2: the LVS feedthrough repair re-flows from place AFTER DRC was
    graded; drc.json then described the old layout and the manifest refused the design."""
    proj = _mk_proj(tmp_path, gds_newer=True)          # stale lvs -> LVS baseline runs
    drc = proj / "reports" / "drc.json"
    drc.write_text(json.dumps({"status": "clean", "total_violations": 0}))
    os.utime(drc, (3_000_000, 3_000_000))              # DRC fresh vs the ORIGINAL gds
    _run_both(proj, tmp_path, lvs_reflows=True)
    assert (proj / "DRC_RAN").exists(), "DRC must be re-graded on the re-flowed layout"


def test_no_reflow_does_not_regrade_drc(tmp_path):
    proj = _mk_proj(tmp_path, gds_newer=True)
    drc = proj / "reports" / "drc.json"
    drc.write_text(json.dumps({"status": "clean", "total_violations": 0}))
    os.utime(drc, (3_000_000, 3_000_000))
    _run_both(proj, tmp_path, lvs_reflows=False)
    assert not (proj / "DRC_RAN").exists()
