"""Fail-closed, research-only adapters for the two R5 native RTL report formats.

These functions parse immutable report bytes. They do not run tools, locate bugs,
or grant authority to a memory asset. A PASS requires a predeclared test registry.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET


COCOTB_CONTRACT = "r5-cocotb-junit-verdict-v2"
SECWORKS_CONTRACT = "r5-secworks-native-summary-verdict-v1"
_WRAPPER = re.compile(r"(?m)^\s*(?:=+\s*)?1 (passed|failed) in [0-9.]+s(?:\s*=+)?\s*$")
_COCOTB_SUMMARY = re.compile(
    r"\bTESTS=(\d+) PASS=(\d+) FAIL=(\d+) SKIP=(\d+)\b"
)
_SECWORKS_PASS = re.compile(r"(?m)^\*\*\* All (\d+) test cases completed successfully\.?\s*$")
_SECWORKS_FAIL = re.compile(
    r"(?m)^\*\*\* (\d+) tests completed - (\d+) test cases did not complete successfully\.?\s*$"
)
_SECWORKS_END = re.compile(r"(?m)^\s*-= Testbench for ([A-Za-z0-9_]+) completed =-\s*$")
_SECWORKS_ERROR = re.compile(r"(?m)^\*\*\* ERROR: TC (\d+) NOT successful\.\s*$")


def _unknown(contract: str, reason: str) -> dict:
    return {"contract": contract, "verdict": "UNKNOWN", "reason": reason}


def cocotb_verdict(*, junit: bytes | None, runner_log: bytes | None,
                   expected_test_ids: tuple[str, ...], runner_exit: int | None,
                   require_recorded_exit: bool = True) -> dict:
    """Judge one fixed pytest node and its internal cocotb JUnit report.

    ``runner_exit=None`` is allowed only for historical qualification where
    the exit code was not captured. New method runs must record it.
    """
    if not junit or not runner_log:
        return _unknown(COCOTB_CONTRACT, "missing_junit_or_runner_log")
    if require_recorded_exit and runner_exit is None:
        return _unknown(COCOTB_CONTRACT, "runner_exit_unrecorded")
    if not expected_test_ids or len(set(expected_test_ids)) != len(expected_test_ids):
        return _unknown(COCOTB_CONTRACT, "invalid_expected_registry")
    try:
        root = ET.fromstring(junit)
    except ET.ParseError:
        return _unknown(COCOTB_CONTRACT, "invalid_junit")
    if root.tag != "testsuites" or len(root) != 1 or root[0].tag != "testsuite":
        return _unknown(COCOTB_CONTRACT, "unexpected_junit_structure")
    suite = root[0]
    cases = suite.findall("testcase")
    ids = [case.get("name") for case in cases]
    if len(cases) != len(expected_test_ids) or set(ids) != set(expected_test_ids):
        return _unknown(COCOTB_CONTRACT, "test_registry_mismatch")
    if any(case.get("classname") is None for case in cases):
        return _unknown(COCOTB_CONTRACT, "test_identity_missing")
    if any(case.find("error") is not None for case in cases):
        return _unknown(COCOTB_CONTRACT, "test_tool_error")
    failed = sum(case.find("failure") is not None for case in cases)
    skipped = sum(case.find("skipped") is not None for case in cases)
    if any(len(case) > 1 for case in cases) or skipped:
        return _unknown(COCOTB_CONTRACT, "skipped_or_ambiguous_case")
    log = runner_log.decode("utf-8", errors="replace")
    wrapper = _WRAPPER.findall(log)
    summaries = _COCOTB_SUMMARY.findall(log)
    if len(wrapper) != 1 or len(summaries) > 1:
        return _unknown(COCOTB_CONTRACT, "missing_or_duplicated_terminal_summary")
    if summaries:
        total, passed, summary_failed, summary_skipped = map(int, summaries[0])
        if (total, passed, summary_failed, summary_skipped) != (
            len(cases), len(cases) - failed, failed, 0
        ):
            return _unknown(COCOTB_CONTRACT, "junit_log_contradiction")
    elif failed:
        return _unknown(COCOTB_CONTRACT, "missing_failure_summary")
    wrapper_failed = wrapper[0] == "failed"
    if wrapper_failed != bool(failed):
        return _unknown(COCOTB_CONTRACT, "wrapper_junit_contradiction")
    if runner_exit is not None and (runner_exit == 0) == bool(failed):
        return _unknown(COCOTB_CONTRACT, "exit_report_contradiction")
    return {
        "contract": COCOTB_CONTRACT,
        "verdict": "FAIL" if failed else "PASS",
        "test_count": len(cases), "failed": failed, "passed": len(cases) - failed,
        "test_ids": ids, "runner_exit_recorded": runner_exit is not None,
        "random_seed": next((p.get("value") for p in suite.findall("property")
                             if p.get("name") == "random_seed"), None),
    }


def secworks_verdict(*, stdout: bytes | None, stderr: bytes | None,
                     simulate_exit: int | None, expected_count: int,
                     expected_bench: str,
                     require_recorded_exit: bool = True) -> dict:
    """Judge a pinned Secworks self-checking top; native failure outranks rc=0."""
    if stdout is None or stderr is None:
        return _unknown(SECWORKS_CONTRACT, "missing_native_stream")
    if require_recorded_exit and simulate_exit is None:
        return _unknown(SECWORKS_CONTRACT, "simulate_exit_unrecorded")
    if expected_count <= 0 or not expected_bench:
        return _unknown(SECWORKS_CONTRACT, "invalid_scope")
    out = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")
    passed = _SECWORKS_PASS.findall(out)
    failed = _SECWORKS_FAIL.findall(out)
    endings = _SECWORKS_END.findall(out)
    if len(passed) + len(failed) != 1 or endings != [expected_bench]:
        return _unknown(SECWORKS_CONTRACT, "missing_or_contradictory_terminal_summary")
    if err.strip() or re.search(r"\b(?:FATAL|FAIL|ERROR)\b", err, re.I):
        return _unknown(SECWORKS_CONTRACT, "unexpected_stderr")
    errors = _SECWORKS_ERROR.findall(out)
    if passed:
        if int(passed[0]) != expected_count or errors or simulate_exit not in (0, None):
            return _unknown(SECWORKS_CONTRACT, "pass_summary_contradiction")
        return {"contract": SECWORKS_CONTRACT, "verdict": "PASS",
                "test_count": expected_count, "failed": 0, "passed": expected_count,
                "simulate_exit_recorded": simulate_exit is not None}
    total, count_failed = map(int, failed[0])
    if total != expected_count or not 0 < count_failed <= total or len(errors) != count_failed:
        return _unknown(SECWORKS_CONTRACT, "failure_summary_contradiction")
    return {"contract": SECWORKS_CONTRACT, "verdict": "FAIL",
            "test_count": total, "failed": count_failed,
            "passed": total - count_failed, "simulate_exit": simulate_exit,
            "simulate_exit_recorded": simulate_exit is not None}
