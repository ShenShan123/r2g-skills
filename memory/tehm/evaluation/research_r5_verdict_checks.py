"""Small in-memory adversarial checks for the R5 native verdict adapters."""
from __future__ import annotations

from .research_r5_verdict import cocotb_verdict, secworks_verdict


def run_checks() -> dict:
    ids = ("case_a", "case_b")
    clean = (b'<testsuites><testsuite><testcase name="case_a" classname="m" />'
             b'<testcase name="case_b" classname="m" /></testsuite></testsuites>')
    fault = (b'<testsuites><testsuite><testcase name="case_a" classname="m">'
             b'<failure /></testcase><testcase name="case_b" classname="m" />'
             b'</testsuite></testsuites>')
    clean_log = b'1 passed in 0.2s\n'
    fault_log = b'TESTS=2 PASS=1 FAIL=1 SKIP=0\n1 failed in 0.2s\n'
    cases = {
        "cocotb_clean": (cocotb_verdict(junit=clean, runner_log=clean_log,
            expected_test_ids=ids, runner_exit=0), "PASS"),
        "cocotb_zero_tests": (cocotb_verdict(junit=clean, runner_log=clean_log,
            expected_test_ids=(), runner_exit=0), "UNKNOWN"),
        "cocotb_missing_report": (cocotb_verdict(junit=None, runner_log=clean_log,
            expected_test_ids=ids, runner_exit=0), "UNKNOWN"),
        "cocotb_missing_terminal": (cocotb_verdict(junit=clean, runner_log=b'run started',
            expected_test_ids=ids, runner_exit=0), "UNKNOWN"),
        "cocotb_functional_failure_rc0": (cocotb_verdict(junit=fault,
            runner_log=fault_log, expected_test_ids=ids, runner_exit=0), "UNKNOWN"),
        "cocotb_functional_failure": (cocotb_verdict(junit=fault,
            runner_log=fault_log, expected_test_ids=ids, runner_exit=1), "FAIL"),
        "cocotb_log_xml_conflict": (cocotb_verdict(junit=clean,
            runner_log=fault_log, expected_test_ids=ids, runner_exit=0), "UNKNOWN"),
        "cocotb_missing_id": (cocotb_verdict(junit=clean, runner_log=clean_log,
            expected_test_ids=("case_a", "other"), runner_exit=0), "UNKNOWN"),
        "cocotb_missing_exit": (cocotb_verdict(junit=clean, runner_log=clean_log,
            expected_test_ids=ids, runner_exit=None), "UNKNOWN"),
        "cocotb_decorated_terminal": (cocotb_verdict(junit=clean,
            runner_log=b"===== 1 passed in 0.2s =====\n", expected_test_ids=ids, runner_exit=0), "PASS"),
    }
    native_clean = (b'*** All 2 test cases completed successfully\n'
                    b'   -= Testbench for AES completed =-\n')
    native_fault = (b'*** ERROR: TC 1 NOT successful.\n'
                    b'*** 2 tests completed - 1 test cases did not complete successfully.\n'
                    b'   -= Testbench for AES completed =-\n')
    native_args = {"stderr": b"", "expected_count": 2, "expected_bench": "AES"}
    cases.update({
        "secworks_clean": (secworks_verdict(stdout=native_clean,
            simulate_exit=0, **native_args), "PASS"),
        "secworks_failure_rc0": (secworks_verdict(stdout=native_fault,
            simulate_exit=0, **native_args), "FAIL"),
        "secworks_zero_tests": (secworks_verdict(stdout=native_clean,
            simulate_exit=0, stderr=b"", expected_count=0, expected_bench="AES"), "UNKNOWN"),
        "secworks_missing_summary": (secworks_verdict(stdout=b'test started',
            simulate_exit=0, **native_args), "UNKNOWN"),
        "secworks_conflicting_summary": (secworks_verdict(
            stdout=native_clean + native_fault, simulate_exit=0, **native_args), "UNKNOWN"),
        "secworks_missing_exit": (secworks_verdict(stdout=native_clean,
            simulate_exit=None, **native_args), "UNKNOWN"),
    })
    failures = {name: {"expected": expected, "actual": actual}
                for name, (actual, expected) in cases.items()
                if actual["verdict"] != expected}
    return {"valid": not failures, "case_count": len(cases), "failures": failures}
