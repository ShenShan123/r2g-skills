"""build_diagnosis.py — the consolidated per-run structured summary (codex #7)
and the synth-error section-scoping bug (failure-patterns.md #38).

- parse_synth_errors used to scan the FULL concatenated log text, so a
  route '[ERROR GRT-…]' or an LVS-mismatch line was mislabeled a SYNTHESIS
  error — a false-positive diagnosis. It is now scoped to the synth.log
  section only (like the DRC/make checks already scope to theirs).
- main() now also emits a `run_summary` unifying stage durations
  (backend/RUN_*/stage_log.jsonl), repair repetitions (reports/fix_log.jsonl),
  and DRC/LVS/route/timing status — the single structured summary the
  suggestion asks for, instead of scattered reports/*.json + a DB row.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

_REPORTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "scripts", "reports")
_BD = os.path.join(_REPORTS, "build_diagnosis.py")

_spec = importlib.util.spec_from_file_location("build_diagnosis", _BD)
bd = importlib.util.module_from_spec(_spec)
sys.modules["build_diagnosis"] = bd
_spec.loader.exec_module(bd)


# ---- the section-scoping bug fix ----------------------------------------------

def test_route_error_not_mislabeled_synthesis_error(tmp_path):
    """A routing GRT error in flow.log must produce routing_congestion, NOT a
    spurious synthesis_errors issue (the pre-fix false positive)."""
    text = ("=== flow.log ===\n"
            "[INFO GRT-0001] starting global route\n"
            "[ERROR GRT-0116] Global routing failed due to congestion\n"
            "make: *** [route] Error 1\n")
    issues = bd.detect_issues(text, tmp_path)
    kinds = {i["kind"] for i in issues}
    assert "routing_congestion" in kinds
    assert "synthesis_errors" not in kinds, kinds


def test_lvs_mismatch_error_line_not_synthesis_error(tmp_path):
    text = ("=== 6_lvs.log ===\n"
            "ERROR: netlists do not match\n"
            "Error: 3 mismatched nets\n")
    issues = bd.detect_issues(text, tmp_path)
    kinds = {i["kind"] for i in issues}
    assert "lvs_mismatch" in kinds
    assert "synthesis_errors" not in kinds, kinds


def test_real_synth_error_still_detected_in_synth_section(tmp_path):
    """The scoping must not hide a GENUINE synthesis error in synth.log."""
    text = ("=== flow.log ===\nall good\n"
            "=== synth.log ===\n"
            "ERROR: syntax error near 'endmodule'\n")
    issues = bd.detect_issues(text, tmp_path)
    synth = [i for i in issues if i["kind"] == "synthesis_errors"]
    assert synth and any("syntax error" in d for d in synth[0]["details"])


def test_section_text_extracts_named_section():
    text = "=== flow.log ===\nAAA\n=== synth.log ===\nBBB\nCCC\n"
    assert bd.section_text(text, "synth.log").strip() == "BBB\nCCC"
    assert bd.section_text(text, "flow.log").strip() == "AAA"
    assert bd.section_text(text, "nope.log") == ""


# ---- the consolidated run_summary (codex #7) ----------------------------------

def _project(tmp_path):
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    run = proj / "backend" / "RUN_2026-07-12_00-00-00"
    run.mkdir(parents=True)
    with open(run / "stage_log.jsonl", "w") as f:
        f.write(json.dumps({"stage": "synth", "status": 0, "elapsed_s": 30,
                            "ts_start": 100, "ts_end": 130, "artifact": "1_synth.odb"}) + "\n")
        f.write(json.dumps({"stage": "floorplan", "status": 0, "elapsed_s": 20}) + "\n")
        f.write(json.dumps({"stage": "route", "status": 0, "elapsed_s": 90}) + "\n")
    with open(proj / "reports" / "fix_log.jsonl", "w") as f:
        f.write(json.dumps({"iter": 1, "verdict": "no_improvement"}) + "\n")
        f.write(json.dumps({"iter": 2, "verdict": "cleared"}) + "\n")
    json.dump({"status": "clean", "total_violations": 0}, open(proj / "reports" / "drc.json", "w"))
    json.dump({"status": "clean"}, open(proj / "reports" / "lvs.json", "w"))
    return proj


def test_run_summary_consolidates_stages_and_fixes(tmp_path):
    proj = _project(tmp_path)
    s = bd.build_run_summary(proj)
    assert [x["stage"] for x in s["stages"]] == ["synth", "floorplan", "route"]
    assert s["total_elapsed_s"] == 140
    assert s["stages"][0]["artifact"] == "1_synth.odb"
    assert s["fix_iterations"] == 2
    assert s["fix_iters_to_clean"] == 2
    assert s["signoff"]["drc"] == "clean" and s["signoff"]["lvs"] == "clean"


def test_run_summary_empty_when_no_backend(tmp_path):
    proj = tmp_path / "bare"
    (proj / "reports").mkdir(parents=True)
    s = bd.build_run_summary(proj)
    assert s["stages"] == [] and s["total_elapsed_s"] == 0
    assert s["fix_iterations"] == 0 and s["fix_iters_to_clean"] is None


def test_main_emits_run_summary(tmp_path):
    proj = _project(tmp_path)
    out = tmp_path / "diagnosis.json"
    r = subprocess.run([sys.executable, _BD, str(proj), str(out)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    diag = json.loads(out.read_text())
    assert "run_summary" in diag
    assert diag["run_summary"]["total_elapsed_s"] == 140
    assert diag["run_summary"]["fix_iterations"] == 2
