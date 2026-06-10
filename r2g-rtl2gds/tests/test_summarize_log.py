"""Tests for the deterministic log summarizer (spec decision 10)."""
import summarize_log


PASS_LOG = """[INFO GRT-0001] starting global route
[WARNING GRT-0044] congestion at gcell (1,2)
Finished route: 0 violations.
"""

FAIL_LOG = """[INFO DRT-0001] start detailed routing
[ERROR DRT-0085] cannot fix violation
[WARNING DRT-0009] net u1/n3 ripped up
Signal 11 received
""" + "\n".join(f"tail line {i}" for i in range(40))


def test_pass_log_counts_and_digest():
    s = summarize_log.summarize_text(PASS_LOG, status_hint="pass")
    assert s["error_count"] == 0
    assert s["warning_count"] == 1
    assert s["first_error"] is None
    assert s["last_lines"] is None            # tail only kept on failure
    assert "0 errors, 1 warnings" in s["digest"]


def test_fail_log_first_error_and_bounded_tail():
    s = summarize_log.summarize_text(FAIL_LOG, status_hint="fail")
    assert s["error_count"] == 1
    assert "[ERROR DRT-0085]" in s["first_error"]
    tail = s["last_lines"].splitlines()
    assert len(tail) <= summarize_log.TAIL_LINES
    assert tail[-1] == "tail line 39"


def test_detect_bugs_finds_sigsegv_with_symptom():
    bugs = summarize_log.detect_bugs(FAIL_LOG, check="orfs_stage", vclass="route")
    assert len(bugs) == 1
    b = bugs[0]
    assert "signal 11" in b["signature"].lower()
    assert b["symptom_id"] and len(b["symptom_id"]) == 16


def test_summarize_report_json_extracts_metrics():
    rep = {"status": "fail", "total_violations": 7,
           "categories": {"M3_ANTENNA": {"count": 7}}}
    s = summarize_log.summarize_report(rep, kind="drc")
    assert s["status"] == "fail"
    assert s["metrics"]["total_violations"] == 7
    assert "M3_ANTENNA" in s["digest"]


def test_deterministic():
    a = summarize_log.summarize_text(FAIL_LOG, status_hint="fail")
    b = summarize_log.summarize_text(FAIL_LOG, status_hint="fail")
    assert a == b
