"""Independent DEV live-control for ZipCPU's native skidbuffer formal scope.

The control targets valid signalling, not the payload-source mechanism.  Its
only purpose is to show that the frozen formal command can detect *some*
activated functional fault.  It cannot upgrade the payload MISSED result.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import research_r5_zipcpu_skid_dev_probe as base
from .research_r5_zipcpu_formal_audit_v2 import _native_verdict


SCHEMA = "tehm-r5-zipcpu-formal-live-control-v1"
LINE = 200
OLD = b"assign\to_valid = ro_valid;"
NEW = b"assign\to_valid = 1'b0;"
TASK = "prfo"


def _mutate(source: bytes) -> bytes:
    lines = source.splitlines(keepends=True)
    if (len(lines) < LINE or OLD not in lines[LINE - 1] or
            source.count(OLD) != 1):
        raise ValueError("formal live-control assignment drifted")
    lines[LINE - 1] = lines[LINE - 1].replace(OLD, NEW, 1)
    return b"".join(lines)


def run(*, corpus: Path, work: Path) -> dict:
    corpus = corpus.resolve(strict=True)
    work = work.resolve(strict=False)
    if work.exists() or work.parent != corpus / "_qualification":
        raise ValueError("live-control work must be a fresh _qualification child")
    repo = corpus / base.REPO
    if base._git(repo, "status", "--porcelain=v1"):
        raise ValueError("upstream ZipCPU checkout is dirty")
    source = (repo / base.SOURCE).read_bytes()
    native = (repo / base.NATIVE).read_bytes()
    mutant = _mutate(source)
    prereg = {"schema": SCHEMA, "role": "DEV_LIVE_CONTROL_NOT_PAYLOAD_QUALIFICATION",
              "repository": base.REPO, "commit": base._git(repo, "rev-parse", "HEAD"),
              "source": base.SOURCE, "source_sha256": base._sha(source),
              "native_test": base.NATIVE, "native_test_sha256": base._sha(native),
              "task": TASK, "control_fault_line": LINE,
              "fault_before": OLD.decode(), "fault_after": NEW.decode(),
              "obligation": "registered_valid_output_after_accepted_input",
              "negative_scope": "not_payload_source_sensitivity_evidence",
              "tool_files": base._tools(), "timeout_seconds_per_arm": 120,
              "upstream_mutation_allowed": False, "production_authority": False}
    work.mkdir()
    (work / "preregistration.json").write_bytes(base._json(prereg))
    for name, data in (("clean", source), ("control", mutant)):
        arm = work / name
        stage = arm / "stage"
        (stage / "rtl").mkdir(parents=True)
        (stage / "bench" / "formal").mkdir(parents=True)
        (stage / base.SOURCE).write_bytes(data)
        (stage / base.NATIVE).write_bytes(native)
        argv = [str(base.TOOL_BIN / "sby"), "-d", str(arm / "proof"),
                str(stage / base.NATIVE), TASK]
        (arm / "command.json").write_bytes(base._json(argv))
        rc, output = base._run(argv, cwd=stage / "bench" / "formal", timeout=120)
        (arm / "sby.stdout").write_bytes(output)
        (arm / "sby.exit").write_text(str(rc) + "\n", encoding="utf-8")
    return verify(work)


def verify(work: Path) -> dict:
    root = work.resolve(strict=True)
    prereg_path = root / "preregistration.json"
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    errors = []
    repo = root.parent.parent / base.REPO
    source = (repo / base.SOURCE).read_bytes()
    native = (repo / base.NATIVE).read_bytes()
    if (prereg.get("schema") != SCHEMA or
            prereg.get("role") != "DEV_LIVE_CONTROL_NOT_PAYLOAD_QUALIFICATION" or
            prereg.get("commit") != base._git(repo, "rev-parse", "HEAD") or
            base._git(repo, "status", "--porcelain=v1") or
            prereg.get("source_sha256") != base._sha(source) or
            prereg.get("native_test_sha256") != base._sha(native) or
            prereg.get("tool_files") != base._tools()):
        errors.append("prereg_upstream_or_tools_drift")
    arms = {}
    for name, data in (("clean", source), ("control", _mutate(source))):
        arm = root / name
        stage = arm / "stage"
        argv = [str(base.TOOL_BIN / "sby"), "-d", str(arm / "proof"),
                str(stage / base.NATIVE), TASK]
        if ((stage / base.SOURCE).read_bytes() != data or
                (stage / base.NATIVE).read_bytes() != native or
                json.loads((arm / "command.json").read_text(encoding="utf-8")) != argv or
                any(path.is_symlink() or path.name == ".git" for path in stage.rglob("*"))):
            errors.append(f"{name}:staged_input_or_command_drift")
        arms[name] = _native_verdict(arm / "proof", arm / "sby.exit", data)
    valid = not errors and all(item["verdict"] != "UNDETERMINED" for item in arms.values())
    live = valid and arms["clean"]["verdict"] == "PASS" and arms["control"]["verdict"] == "FAIL"
    return {"valid": valid, "live_control_detected": live,
            "payload_probe_still_missed": "not_changed_by_this_control",
            "errors": errors, "preregistration_sha256": base._sha(prereg_path.read_bytes()),
            "arms": arms}


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run")
    run_p.add_argument("--corpus", type=Path, required=True)
    run_p.add_argument("--work", type=Path, required=True)
    check_p = sub.add_parser("verify")
    check_p.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    result = (run(corpus=args.corpus, work=args.work) if args.command == "run"
              else verify(args.work))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("live_control_detected") else 1


if __name__ == "__main__":
    raise SystemExit(main())
