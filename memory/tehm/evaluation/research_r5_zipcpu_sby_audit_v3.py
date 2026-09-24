"""Cold replay of two ZipCPU DEV formal generations with SBY PASS/FAIL grammar.

Raw probe roots are immutable.  The valid-output live control establishes
that native assertions run, but it never upgrades the payload-source miss.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from . import research_r5_zipcpu_formal_live_control as control
from . import research_r5_zipcpu_skid_dev_probe as payload


SCHEMA = "tehm-r5-zipcpu-native-formal-cold-audit-v3"
_STATUS = re.compile(r"^(PASS|FAIL) [0-9]+ [0-9]+$")


def classify(*, status: str, exit_code: int, log: str,
             has_trace: bool) -> tuple[str, str]:
    match = _STATUS.fullmatch(status)
    if match is None or "Checking assertions" not in log:
        return "UNDETERMINED", "status_or_assertions_missing"
    if (match.group(1) == "PASS" and exit_code == 0 and
            "DONE (PASS, rc=0)" in log and not has_trace and
            "Assert failed" not in log):
        return "PASS", "native_formal_proof_pass"
    if (match.group(1) == "FAIL" and exit_code == 2 and
            "DONE (FAIL, rc=2)" in log and has_trace and
            "BMC failed!" in log and "Assert failed" in log):
        return "FAIL", "native_formal_assertion_counterexample"
    return "UNDETERMINED", "status_exit_log_or_trace_mismatch"


def _arm(arm: Path, expected_source: bytes) -> dict:
    proof = arm / "proof"
    paths = {"status": proof / "status", "log": proof / "logfile.txt",
             "compiled_source": proof / "src" / "skidbuffer.v",
             "runner_exit": arm / "sby.exit"}
    if not all(path.is_file() for path in paths.values()):
        return {"verdict": "UNDETERMINED", "reason": "required_native_artifact_missing"}
    if paths["compiled_source"].read_bytes() != expected_source:
        return {"verdict": "UNDETERMINED", "reason": "compiled_source_mismatch"}
    status = paths["status"].read_text(encoding="utf-8").strip()
    log = paths["log"].read_text(encoding="utf-8", errors="replace")
    try:
        exit_code = int(paths["runner_exit"].read_text(encoding="utf-8").strip())
    except ValueError:
        return {"verdict": "UNDETERMINED", "reason": "runner_exit_invalid"}
    traces = sorted(proof.rglob("*.vcd"))
    verdict, reason = classify(status=status, exit_code=exit_code,
                               log=log, has_trace=bool(traces))
    return {"verdict": verdict, "reason": reason, "native_status": status,
            "runner_exit": exit_code, "compiled_source_sha256":
            payload._sha(paths["compiled_source"].read_bytes()),
            "status_sha256": payload._sha(paths["status"].read_bytes()),
            "native_log_sha256": payload._sha(paths["log"].read_bytes()),
            "counterexample_vcds": [
                {"path": str(path.resolve(strict=True)),
                 "sha256": payload._sha(path.read_bytes())} for path in traces]}


def _checks() -> dict:
    cases = {
        "pass_exact": classify(status="PASS 0 0", exit_code=0,
            log="Checking assertions\nDONE (PASS, rc=0)", has_trace=False)[0] == "PASS",
        "fail_exact": classify(status="FAIL 2 0", exit_code=2,
            log="Checking assertions\nBMC failed!\nAssert failed\nDONE (FAIL, rc=2)",
            has_trace=True)[0] == "FAIL",
        "wrong_exit_unknown": classify(status="FAIL 2 0", exit_code=0,
            log="Checking assertions\nBMC failed!\nAssert failed\nDONE (FAIL, rc=2)",
            has_trace=True)[0] == "UNDETERMINED",
        "missing_trace_unknown": classify(status="FAIL 2 0", exit_code=2,
            log="Checking assertions\nBMC failed!\nAssert failed\nDONE (FAIL, rc=2)",
            has_trace=False)[0] == "UNDETERMINED",
        "zero_assertions_unknown": classify(status="PASS 0 0", exit_code=0,
            log="DONE (PASS, rc=0)", has_trace=False)[0] == "UNDETERMINED",
        "missing_status_unknown": classify(status="", exit_code=0,
            log="Checking assertions\nDONE (PASS, rc=0)", has_trace=False)[0]
            == "UNDETERMINED",
        "pass_with_failure_unknown": classify(status="PASS 0 0", exit_code=0,
            log="Checking assertions\nAssert failed\nDONE (PASS, rc=0)",
            has_trace=False)[0] == "UNDETERMINED",
    }
    return {"valid": all(cases.values()), "case_count": len(cases),
            "failed": sorted(name for name, ok in cases.items() if not ok)}


def audit(*, payload_work: Path, control_work: Path) -> dict:
    payload_root = payload_work.resolve(strict=True)
    control_root = control_work.resolve(strict=True)
    base = payload.verify(payload_root)
    live = control.verify(control_root)
    adapter = _checks()
    # The original live-control producer's rc=1-only adapter returns valid=false
    # for an actual SBY rc=2 FAIL. Its independent input/command checks still
    # return errors=[]; this v3 adapter owns the corrected native classification.
    if (not base.get("valid") or base.get("errors") or live.get("errors") or
            not adapter["valid"]):
        return {"schema": SCHEMA, "valid": False, "errors": {
            "payload": base.get("errors"), "control": live.get("errors"),
            "adapter": adapter["failed"]}}
    repo = payload_root.parent.parent / payload.REPO
    if repo != control_root.parent.parent / payload.REPO:
        return {"schema": SCHEMA, "valid": False,
                "errors": {"cohort": "different_corpus_roots"}}
    source = (repo / payload.SOURCE).read_bytes()
    mutant = payload._fault(source)
    control_mutant = control._mutate(source)
    arms = {}
    for name, data in (("clean", source), ("fault", mutant)):
        for task in payload.TASKS:
            arms[f"payload/{name}/{task}"] = _arm(payload_root / name / task, data)
    for name, data in (("clean", source), ("control", control_mutant)):
        arms[f"live/{name}/prfo"] = _arm(control_root / name, data)
    known = all(item["verdict"] != "UNDETERMINED" for item in arms.values())
    clean = all(arms[f"payload/clean/{task}"]["verdict"] == "PASS"
                for task in payload.TASKS)
    preservation = arms["payload/fault/prfc"]["verdict"] == "PASS"
    target = arms["payload/fault/prfo"]["verdict"]
    qualification = ("MISSED" if clean and preservation and target == "PASS" else
                     "DETECTED" if clean and preservation and target == "FAIL" else
                     "UNDETERMINED")
    live_control_detected = (arms["live/clean/prfo"]["verdict"] == "PASS" and
                             arms["live/control/prfo"]["verdict"] == "FAIL")
    result = {"schema": SCHEMA, "valid": known, "qualification": qualification,
            "live_control_detected": live_control_detected,
            "adapter_checks": adapter, "source_git_sha": payload._git(repo, "rev-parse", "HEAD"),
            "payload_preregistration_sha256": base["preregistration_sha256"],
            "control_preregistration_sha256": live["preregistration_sha256"],
            "payload_work": str(payload_root), "control_work": str(control_root),
            "arms": arms, "role": "DEV_not_TRAIN_or_TRANSFER"}
    result["audit_digest"] = payload._sha(payload._json(result))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload-work", type=Path, required=True)
    parser.add_argument("--control-work", type=Path, required=True)
    args = parser.parse_args()
    result = audit(payload_work=args.payload_work, control_work=args.control_work)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("valid") and result.get("live_control_detected") else 1


if __name__ == "__main__":
    raise SystemExit(main())
