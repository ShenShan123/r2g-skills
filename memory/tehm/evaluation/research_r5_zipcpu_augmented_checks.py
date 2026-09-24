"""Adversarial verdict checks for the R5 ZipCPU DEV augmented scoreboard."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .research_r5_zipcpu_augmented_probe import _classify


def _verdict(*, scenario: int, compile_rc: int = 0, run_rc: int = 0,
             compile_log: str = "", run_log: str = "") -> str:
    with tempfile.TemporaryDirectory(prefix="r5-zipcpu-augmented-check-") as tmp:
        arm = Path(tmp)
        (arm / "compile.exit").write_text(str(compile_rc) + "\n")
        (arm / "run.exit").write_text(str(run_rc) + "\n")
        (arm / "compile.log").write_text(compile_log)
        (arm / "run.log").write_text(run_log)
        return _classify(arm, scenario)["verdict"]


def check() -> dict:
    passed = "TEHM_AUGMENTED_PASS scenario=1 sent=5 received=5\n"
    failed = ("TEHM_AUGMENTED_FAIL payload_mismatch scenario=1 "
              "cycle=4 index=1 got=c3 expected=b2\nFATAL: payload mismatch\n")
    cases = {
        "clean_pass": _verdict(scenario=1, run_log=passed) == "PASS",
        "functional_fail": _verdict(scenario=1, run_rc=1, run_log=failed) == "FAIL",
        "zero_tests_unknown": _verdict(scenario=1, run_log="0 tests completed\n")
            == "UNDETERMINED",
        "missing_terminal_unknown": _verdict(scenario=1, run_log="starting\n")
            == "UNDETERMINED",
        "functional_fail_rc0_unknown": _verdict(scenario=1, run_rc=0, run_log=failed)
            == "UNDETERMINED",
        "pass_rc1_unknown": _verdict(scenario=1, run_rc=1, run_log=passed)
            == "UNDETERMINED",
        "contradictory_summary_unknown": _verdict(
            scenario=1, run_rc=0, run_log=passed + failed) == "UNDETERMINED",
        "wrong_scenario_unknown": _verdict(scenario=0, run_log=passed)
            == "UNDETERMINED",
        "compile_error_unknown": _verdict(
            scenario=1, compile_rc=1, compile_log="error: syntax",
            run_log=passed) == "UNDETERMINED",
        "timeout_unknown": _verdict(scenario=1, run_rc=124,
                                    run_log="timeout\n") == "UNDETERMINED",
        "missing_fatal_unknown": _verdict(scenario=1, run_rc=1,
            run_log=failed.replace("FATAL:", "WARNING:")) == "UNDETERMINED",
    }
    return {"valid": all(cases.values()), "case_count": len(cases),
            "failed": sorted(key for key, ok in cases.items() if not ok),
            "cases": cases}


if __name__ == "__main__":
    result = check()
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["valid"] else 1)
