"""Cold-audit ZipCPU DEV formal artifacts using the actual SBY status grammar.

This reclassifies a preserved v1 raw run; it never reruns or edits any arm.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from . import research_r5_zipcpu_skid_dev_probe as raw


SCHEMA = "tehm-r5-zipcpu-formal-cold-audit-v2"
_STATUS = re.compile(r"^(PASS|FAIL) [0-9]+ [0-9]+$")


def _native_verdict(proof: Path, exit_file: Path, expected_source: bytes) -> dict:
    required = (proof / "status", proof / "logfile.txt", proof / "src" / "skidbuffer.v",
                exit_file)
    if not all(path.is_file() for path in required):
        return {"verdict": "UNDETERMINED", "reason": "required_native_artifact_missing"}
    status_text = required[0].read_text(encoding="utf-8").strip()
    status = _STATUS.fullmatch(status_text)
    log = required[1].read_text(encoding="utf-8", errors="replace")
    try:
        exit_code = int(exit_file.read_text(encoding="utf-8").strip())
    except ValueError:
        return {"verdict": "UNDETERMINED", "reason": "runner_exit_invalid"}
    if required[2].read_bytes() != expected_source:
        return {"verdict": "UNDETERMINED", "reason": "compiled_source_mismatch"}
    if status is None or "Checking assertions" not in log:
        return {"verdict": "UNDETERMINED", "reason": "status_or_assertion_log_missing"}
    token = status.group(1)
    if token == "PASS" and exit_code == 0 and "DONE (PASS, rc=0)" in log:
        verdict, reason = "PASS", "native_formal_proof_pass"
    elif (token == "FAIL" and exit_code == 1 and "DONE (FAIL, rc=1)" in log and
          any(proof.rglob("*.vcd"))):
        verdict, reason = "FAIL", "native_formal_assertion_counterexample"
    else:
        verdict, reason = "UNDETERMINED", "status_exit_log_or_trace_mismatch"
    return {"verdict": verdict, "reason": reason, "native_status": status_text,
            "runner_exit": exit_code, "status_sha256": raw._sha(required[0].read_bytes()),
            "native_log_sha256": raw._sha(required[1].read_bytes()),
            "compiled_source_sha256": raw._sha(required[2].read_bytes())}


def audit(work: Path) -> dict:
    root = work.resolve(strict=True)
    base = raw.verify(root)
    if not base.get("valid") or base.get("errors"):
        return {"schema": SCHEMA, "valid": False,
                "errors": base.get("errors") or ["raw_stage_or_tool_lock_failed"]}
    prereg = json.loads((root / "preregistration.json").read_text(encoding="utf-8"))
    source = (root.parent.parent / raw.REPO / raw.SOURCE).read_bytes()
    mutant = raw._fault(source)
    arms = {}
    for name, data in (("clean", source), ("fault", mutant)):
        for task in raw.TASKS:
            arm = root / name / task
            arms[f"{name}/{task}"] = _native_verdict(
                arm / "proof", arm / "sby.exit", data)
    clean = all(arms[f"clean/{task}"]["verdict"] == "PASS" for task in raw.TASKS)
    fault_target = arms["fault/prfo"]["verdict"]
    fault_preservation = arms["fault/prfc"]["verdict"]
    valid = all(arm["verdict"] != "UNDETERMINED" for arm in arms.values())
    if clean and fault_target == "FAIL" and fault_preservation == "PASS" and valid:
        qualification = "DETECTED"
    elif clean and fault_target == "PASS" and fault_preservation == "PASS" and valid:
        qualification = "MISSED"
    else:
        qualification = "UNDETERMINED"
    return {"schema": SCHEMA, "valid": valid, "qualification": qualification,
            "role": prereg["role"], "source_git_sha": prereg["source_git_sha"],
            "preregistration_sha256": base["preregistration_sha256"],
            "raw_run": str(root), "arms": arms,
            "note": "native_formal_scope_only_not_TRAIN_or_transfer"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.work)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("valid") else 1


if __name__ == "__main__":
    raise SystemExit(main())
