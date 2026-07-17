"""fix_signoff.sh must sign off the SAME ORFS workspace the backend GDS was built in
(full-pipeline Issue 9).

A backend built under an explicit FLOW_VARIANT used to be re-staged + reflowed under the
project-basename variant, because run_drc/run_lvs were called project+platform only and
fix_signoff never learned the variant. Now fix_signoff:
  * accepts an explicit --variant,
  * else RECOVERS the variant from the newest backend RUN's run-meta.json flow_variant,
  * else forwards an EMPTY 3rd positional arg — which run_drc/run_lvs/run_orfs treat as
    "derive from basename", preserving today's behavior for variant-less callers
    (e.g. engineer_loop's _run_fix).

The forwarded value is asserted through the R2G_RUN_DRC / R2G_RUN_LVS / R2G_RUN_ORFS
command seams.
"""
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


def _seed_project(tmp_path, name, run_meta_variant=None):
    proj = tmp_path / name
    (proj / "constraints").mkdir(parents=True)
    (proj / "reports").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = nangate45\n")
    if run_meta_variant is not None:
        run = proj / "backend" / "RUN_2026-07-16_09-00-00_1_aaaa"
        run.mkdir(parents=True)
        (run / "run-meta.json").write_text(json.dumps({"flow_variant": run_meta_variant}))
    return proj


def _drc_baseline_recorded_variant(tmp_path, name, *, variant_arg=None, run_meta_variant=None):
    """Run `fix_signoff --check drc` (no seeded report -> _ensure_baseline invokes RUN_DRC)
    and return the bracketed 3rd positional arg RUN_DRC received."""
    proj = _seed_project(tmp_path, name, run_meta_variant=run_meta_variant)
    bindir = proj / "bin"; bindir.mkdir()
    rec = proj / "drc_arg3.txt"
    _stub(bindir / "run_drc.sh", f'printf "[%s]\\n" "${{3-}}" >> "{rec}"\nexit 0')
    _stub(bindir / "extract_drc.py",
          'python3 - "$@" <<\'PY\'\nimport json,sys\n'
          'open(sys.argv[2],"w").write(json.dumps({"status":"clean","total_violations":0,"categories":{}}))\nPY')
    _stub(bindir / "diagnose.py", 'if [[ "$*" == *"--next"* ]]; then echo -e "STOP\\tclean\\tdone"; fi')
    env = dict(os.environ,
               R2G_DIAGNOSE=str(bindir / "diagnose.py"),
               R2G_RUN_DRC=str(bindir / "run_drc.sh"),
               R2G_EXTRACT_DRC=str(bindir / "extract_drc.py"),
               R2G_JOURNAL_DB=str(tmp_path / "journal.sqlite"))
    argv = ["bash", str(FIX_SIGNOFF), str(proj), "nangate45", "--check", "drc", "--max-iters", "1"]
    if variant_arg is not None:
        argv += ["--variant", variant_arg]
    subprocess.run(argv, env=env, check=False, capture_output=True, text=True)
    assert rec.exists(), "RUN_DRC baseline was never invoked"
    return rec.read_text().strip().splitlines()


def test_explicit_variant_forwarded_to_drc(tmp_path):
    got = _drc_baseline_recorded_variant(tmp_path, "proj_a", variant_arg="EXPLICIT_V")
    assert got and all(x == "[EXPLICIT_V]" for x in got), got


def test_recovered_variant_forwarded_to_drc(tmp_path):
    # No --variant, but the backend run-meta records flow_variant=FROMMETA.
    got = _drc_baseline_recorded_variant(tmp_path, "proj_b", run_meta_variant="FROMMETA")
    assert got and all(x == "[FROMMETA]" for x in got), got


def test_absent_variant_forwards_empty_arg(tmp_path):
    # Neither an explicit --variant nor a run-meta flow_variant: an empty 3rd arg is
    # forwarded, which the runners treat as "derive from basename" (behavior unchanged).
    got = _drc_baseline_recorded_variant(tmp_path, "proj_c")
    assert got and all(x == "[]" for x in got), got


def test_explicit_variant_forwarded_to_lvs(tmp_path):
    proj = _seed_project(tmp_path, "proj_lvs")
    bindir = proj / "bin"; bindir.mkdir()
    rec = proj / "lvs_arg3.txt"
    _stub(bindir / "run_lvs.sh", f'printf "[%s]\\n" "${{3-}}" >> "{rec}"\nexit 0')
    _stub(bindir / "extract_lvs.py",
          'python3 - "$@" <<\'PY\'\nimport json,sys\n'
          'open(sys.argv[2],"w").write(json.dumps({"status":"clean","mismatch_count":0}))\nPY')
    _stub(bindir / "diagnose.py", 'if [[ "$*" == *"--next"* ]]; then echo -e "STOP\\tclean\\tdone"; fi')
    env = dict(os.environ,
               R2G_DIAGNOSE=str(bindir / "diagnose.py"),
               R2G_RUN_LVS=str(bindir / "run_lvs.sh"),
               R2G_EXTRACT_LVS=str(bindir / "extract_lvs.py"),
               R2G_JOURNAL_DB=str(tmp_path / "journal.sqlite"))
    subprocess.run(["bash", str(FIX_SIGNOFF), str(proj), "nangate45", "--check", "lvs",
                    "--max-iters", "1", "--variant", "EXPLICIT_V"],
                   env=env, check=False, capture_output=True, text=True)
    assert rec.exists(), "RUN_LVS baseline was never invoked"
    got = rec.read_text().strip().splitlines()
    assert got and all(x == "[EXPLICIT_V]" for x in got), got


def test_explicit_variant_forwarded_to_orfs_on_reflow(tmp_path):
    """A route fix reflows via RUN_ORFS — the variant must ride that reflow too."""
    proj = _seed_project(tmp_path, "proj_orfs")
    bindir = proj / "bin"; bindir.mkdir()
    rec = proj / "orfs_arg3.txt"
    mark = proj / "reflowed.marker"
    # diagnose: one actionable route strategy that reruns from floorplan, then STOP.
    _stub(bindir / "diagnose.py",
          'if [[ "$*" == *"--next"* ]]; then echo -e "route_relief\\tfloorplan\\t"; fi\n'
          'if [[ "$*" == *"--apply"* ]]; then echo "{\\"config_edits\\":{\\"CORE_UTILIZATION\\":\\"40\\"}}"; fi')
    _stub(bindir / "run_orfs.sh", f'printf "[%s]\\n" "${{3-}}" >> "{rec}"\ntouch "{mark}"\nexit 0')
    # extract_route: 5 violations until the reflow marker exists, then clean.
    _stub(bindir / "extract_route.py",
          f'python3 - "$@" "{mark}" <<\'PY\'\nimport json,sys,os\n'
          'clean = os.path.exists(sys.argv[3])\n'
          'open(sys.argv[2],"w").write(json.dumps({"status":"clean" if clean else "fail",'
          '"total_violations":0 if clean else 5}))\nPY')
    env = dict(os.environ,
               R2G_DIAGNOSE=str(bindir / "diagnose.py"),
               R2G_RUN_ORFS=str(bindir / "run_orfs.sh"),
               R2G_EXTRACT_ROUTE=str(bindir / "extract_route.py"),
               R2G_JOURNAL_DB=str(tmp_path / "journal.sqlite"))
    subprocess.run(["bash", str(FIX_SIGNOFF), str(proj), "nangate45", "--check", "route",
                    "--max-iters", "2", "--variant", "EXPLICIT_V"],
                   env=env, check=False, capture_output=True, text=True)
    assert rec.exists(), "RUN_ORFS reflow was never invoked"
    got = rec.read_text().strip().splitlines()
    assert got and all(x == "[EXPLICIT_V]" for x in got), got
