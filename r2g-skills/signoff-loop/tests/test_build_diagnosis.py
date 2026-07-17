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


# ---- generic-diagnosis misclassification fixes (2026-07-16 full-pipeline #12) --

def test_yosys_100pct_utilization_info_not_overflow(tmp_path):
    """Yosys prints a healthy INFO line 'Design area NNN um^2 ~100% utilization'.
    The bare '100%' trigger false-positived it as placement_utilization_overflow;
    it must now yield NO such issue."""
    text = ("=== synth.log ===\n"
            "[INFO] Design area 5678 um^2 100% utilization\n")
    kinds = {i["kind"] for i in bd.detect_issues(text, tmp_path)}
    assert "placement_utilization_overflow" not in kinds, kinds


def test_real_gpl0053_overflow_still_detected(tmp_path):
    """A genuine placement-overflow error code (GPL-0053) — which does NOT contain
    the word 'utilization' — must still raise placement_utilization_overflow."""
    text = ("=== flow.log ===\n"
            "[ERROR GPL-0053] Utilization exceeds max: place cannot converge\n")
    kinds = {i["kind"] for i in bd.detect_issues(text, tmp_path)}
    assert "placement_utilization_overflow" in kinds, kinds


def test_real_flw0024_overflow_still_detected(tmp_path):
    """FLW-0024 (die too small for the cells) also lacks the word 'utilization'
    but is a real overflow abort — still detected by the error-code branch."""
    text = ("=== flow.log ===\n"
            "[ERROR FLW-0024] Placement failed: not enough room for cells\n")
    kinds = {i["kind"] for i in bd.detect_issues(text, tmp_path)}
    assert "placement_utilization_overflow" in kinds, kinds


def test_clean_setup_report_not_timing_violation(tmp_path):
    """'No setup violations found' CONTAINS the substring 'setup violation'; a
    clean STA report (no paired hold-clean line) must NOT yield timing_violation."""
    text = "=== flow.log ===\nNo setup violations found\n"
    kinds = {i["kind"] for i in bd.detect_issues(text, tmp_path)}
    assert "timing_violation" not in kinds, kinds


def test_real_setup_violation_still_detected(tmp_path):
    """A genuine setup violation must still be flagged after the negation scrub."""
    text = "=== flow.log ===\nPath has setup violation of 0.42ns\n"
    kinds = {i["kind"] for i in bd.detect_issues(text, tmp_path)}
    assert "timing_violation" in kinds, kinds


def test_ppl0024_is_io_pin_capacity_overflow_leading(tmp_path):
    """A PPL-0024 pin-overflow abort must yield a LEADING io_pin_capacity_overflow
    kind (not placement_utilization_overflow or timing_violation), and parse the
    current/required die perimeter into the issue payload."""
    text = ("=== flow.log ===\n"
            "[ERROR PPL-0024] Number of IO pins (1521) exceeds maximum number of "
            "available positions (718). Increase the die perimeter from 800.00um "
            "to 2068.56um.\n"
            "[ERROR PPL-0024] Cannot place IO pins.\n"
            "make: *** [place] Error 2\n")
    issues = bd.detect_issues(text, tmp_path)
    kinds = [i["kind"] for i in issues]
    assert kinds[0] == "io_pin_capacity_overflow", kinds
    assert "placement_utilization_overflow" not in kinds, kinds
    assert "timing_violation" not in kinds, kinds
    pin = issues[0]
    assert pin["current_perimeter_um"] == 800.00
    assert pin["required_perimeter_um"] == 2068.56
    assert "perimeter" in pin["suggestion"].lower()


def test_ppl0024_without_perimeter_numbers_still_detected(tmp_path):
    """The 'die/core perimeter.' variant carries no from/to numbers; the kind must
    still fire, just without the optional perimeter payload."""
    text = ("=== flow.log ===\n"
            "[ERROR PPL-0024] Number of IO pins (342) exceeds maximum number of "
            "available positions (248). Increase the die/core perimeter.\n")
    issues = bd.detect_issues(text, tmp_path)
    assert issues[0]["kind"] == "io_pin_capacity_overflow"
    assert "current_perimeter_um" not in issues[0]


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


# ---- the orfs_status fallback kind (codex-debug 2026-07-13 #4) -----------------
# A backend stage abort/timeout leaves no text `make` error line, so every
# text-log rule misses it and the old code emitted kind:none. The ORFS stage
# ledger (ppa.json orfs_status/orfs_fail_stage) now names the failed stage.

def test_orfs_fallback_fail_names_stage():
    fb = bd._orfs_fallback_kind({"signoff": {"orfs_status": "fail",
                                             "orfs_fail_stage": "finish"}})
    assert fb["kind"] == "orfs_stage_failed"
    assert "finish" in fb["summary"]


def test_orfs_fallback_partial_incomplete():
    fb = bd._orfs_fallback_kind({"signoff": {"orfs_status": "partial",
                                             "orfs_fail_stage": "route"}})
    assert fb["kind"] == "orfs_stage_incomplete"
    assert "route" in fb["summary"]


def test_orfs_fallback_none_when_clean_or_absent():
    assert bd._orfs_fallback_kind({"signoff": {"orfs_status": "clean"}}) is None
    assert bd._orfs_fallback_kind({}) is None
    assert bd._orfs_fallback_kind(None) is None


def test_main_orfs_fail_not_kind_none(tmp_path):
    """A backend abort/timeout with no text signature must yield a stage-named
    kind (not kind:none) AND inject no issue (so ingest fabricates no event)."""
    proj = _project(tmp_path)
    json.dump({"orfs_status": "fail", "orfs_fail_stage": "finish"},
              open(proj / "reports" / "ppa.json", "w"))
    out = tmp_path / "diagnosis.json"
    r = subprocess.run([sys.executable, _BD, str(proj), str(out)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    diag = json.loads(out.read_text())
    assert diag["kind"] == "orfs_stage_failed", diag["kind"]
    assert "finish" in diag["summary"]
    assert diag["issues"] == []          # no fabricated failure_event source
    assert diag["run_summary"]["signoff"]["orfs_status"] == "fail"


def test_main_clean_run_still_kind_none(tmp_path):
    """Regression: a run with no failure signature AND no orfs failure keeps
    the honest kind:none — the fallback must not fire on a clean/partial-less run."""
    proj = _project(tmp_path)          # no ppa.json → no orfs_status
    out = tmp_path / "diagnosis.json"
    r = subprocess.run([sys.executable, _BD, str(proj), str(out)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert json.loads(out.read_text())["kind"] == "none"


def test_run_summary_echoes_antenna_nonconverged(tmp_path):
    """The terminal antenna-repair verdict (fix_signoff.sh marker) rides the
    structured summary so 'the fix loop gave up' is visible in diagnosis.json."""
    proj = _project(tmp_path)
    json.dump({"class": "antenna", "residual_count": 2, "fix_iters": 8},
              open(proj / "reports" / "antenna_nonconverged.json", "w"))
    s = bd.build_run_summary(proj)
    assert s["antenna_nonconverged"]["residual_count"] == 2
