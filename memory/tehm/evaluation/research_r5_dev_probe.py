"""Audit one preregistered R5 DEV oracle-sensitivity probe; not a TEHM run."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from .research_r5_qualification import COCOTB_SCOPES
from .research_r5_verdict import cocotb_verdict


SCHEMA = "tehm-r5-skid-payload-dev-probe-v1"


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode()


def _ref(path: Path) -> dict:
    data = path.read_bytes()
    return {"path": str(path.resolve(strict=True)), "sha256": _sha(data), "bytes": len(data)}


def _one_junit(arm: Path) -> Path:
    matches = list(arm.glob("stage/tb/axis_register/sim_build/test_axis_register-8-2/*_results.xml"))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one JUnit in {arm}")
    return matches[0]


def audit_dev_probe(*, work: str | Path, output: str | Path) -> dict:
    root = Path(work).resolve(strict=True)
    target = Path(output).resolve(strict=False)
    if target.exists():
        raise ValueError(f"refusing to overwrite DEV receipt: {target}")
    prereg = root / "preregistration.json"
    manifest = json.loads(prereg.read_text(encoding="utf-8"))
    if manifest.get("schema") != "tehm-r5-dev-mechanism-probe-v1":
        raise ValueError("wrong preregistration schema")
    if manifest.get("role") != "DEV_ORACLE_SENSITIVITY_NOT_TRANSFER":
        raise ValueError("probe role drift")
    corpus = root.parent.parent
    repo = corpus / manifest["source_repository"]
    git = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                         capture_output=True, check=True, timeout=20).stdout.decode().strip()
    dirty = subprocess.run(["git", "-C", str(repo), "status", "--porcelain=v1"],
                           capture_output=True, check=True, timeout=20).stdout
    if git != manifest["source_git_sha"] or dirty:
        raise ValueError("source clone SHA or clean state drift")
    source = (repo / manifest["source_file"]).read_bytes()
    test = (repo / manifest["testbench_file"]).read_bytes()
    if (_sha(source)[7:] != manifest["source_sha256"] or
            _sha(test)[7:] != manifest["testbench_sha256"]):
        raise ValueError("upstream source or test drift")
    before = manifest["fault_before"].encode()
    after = manifest["fault_after"].encode()
    line = manifest["fault_line"]
    lines = source.splitlines(keepends=True)
    if not isinstance(line, int) or lines[line - 1].rstrip(b"\r\n") != before:
        raise ValueError("preregistered line no longer matches upstream")
    lines[line - 1] = lines[line - 1].replace(before, after, 1)
    mutant = b"".join(lines)
    expected_ids = COCOTB_SCOPES[0][5]
    arms = {}
    for name, expected_source in (("clean", source), ("fault", mutant)):
        arm = root / name
        staged_source = arm / "stage" / manifest["source_file"]
        staged_test = arm / "stage" / manifest["testbench_file"]
        if staged_source.read_bytes() != expected_source or staged_test.read_bytes() != test:
            raise ValueError(f"staged source/test drift: {name}")
        if any(path.is_symlink() or path.name == ".git" for path in (arm / "stage").rglob("*")):
            raise ValueError(f"agent-ineligible link/git found in stage: {name}")
        log = arm / "pytest.log"
        exit_file = arm / "pytest.exit"
        exit_code = int(exit_file.read_text().strip())
        xml = _one_junit(arm)
        result = cocotb_verdict(junit=xml.read_bytes(), runner_log=log.read_bytes(),
                                expected_test_ids=expected_ids, runner_exit=exit_code)
        if result.get("random_seed") != str(manifest["random_seed"]):
            raise ValueError(f"seed drift: {name}")
        build = xml.parent / "axis_register.vvp"
        if str(staged_source).encode() not in build.read_bytes():
            raise ValueError(f"VVP does not identify staged source: {name}")
        arms[name] = {"result": result,
                      "artifacts": {label: _ref(path) for label, path in (
                          ("staged_source", staged_source), ("staged_test", staged_test),
                          ("runner_log", log), ("runner_exit", exit_file),
                          ("cocotb_junit", xml), ("compiled_vvp", build))}}
    if arms["clean"]["result"]["verdict"] != "PASS" or arms["fault"]["result"]["verdict"] != "FAIL":
        raise ValueError("DEV probe did not establish clean PASS and fault FAIL")
    receipt = {
        "schema": SCHEMA, "role": "DEV_ORACLE_SENSITIVITY_NOT_TRANSFER",
        "preregistration": _ref(prereg), "source_git_sha": git,
        "verdict_adapter_code": _ref(Path(cocotb_verdict.__code__.co_filename)),
        "auditor_code": _ref(Path(__file__)), "arms": arms,
        "fault_effect": "native_payload_mismatch_detected_in_backpressure_cases",
        "limit": "one chosen seed and one constructed fault; no independent transfer or memory use",
    }
    receipt["digest"] = _sha(_json(receipt))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_json(receipt))
    return {"valid": True, "output": str(target), "digest": receipt["digest"],
            "clean": arms["clean"]["result"], "fault": arms["fault"]["result"]}


def verify_dev_probe(receipt: str | Path) -> dict:
    path = Path(receipt).resolve(strict=True)
    body = json.loads(path.read_text(encoding="utf-8"))
    errors = []
    without_digest = {key: value for key, value in body.items() if key != "digest"}
    if body.get("schema") != SCHEMA or body.get("digest") != _sha(_json(without_digest)):
        errors.append("receipt_digest_or_schema")
    refs = [body["preregistration"], body["verdict_adapter_code"], body["auditor_code"]]
    refs += [ref for arm in body["arms"].values() for ref in arm["artifacts"].values()]
    for ref in refs:
        try:
            data = Path(ref["path"]).read_bytes()
            if len(data) != ref["bytes"] or _sha(data) != ref["sha256"]:
                errors.append("artifact_drift:" + ref["path"])
        except OSError:
            errors.append("artifact_missing:" + ref["path"])
    try:
        prereg = Path(body["preregistration"]["path"])
        manifest = json.loads(prereg.read_text(encoding="utf-8"))
        root = prereg.parent
        repo = root.parent.parent / manifest["source_repository"]
        source = (repo / manifest["source_file"]).read_bytes()
        test = (repo / manifest["testbench_file"]).read_bytes()
        lines = source.splitlines(keepends=True)
        line = manifest["fault_line"]
        before = manifest["fault_before"].encode()
        after = manifest["fault_after"].encode()
        if (not isinstance(line, int) or not 1 <= line <= len(lines) or
                lines[line - 1].rstrip(b"\r\n") != before):
            errors.append("fault_preregistration_drift")
        else:
            lines[line - 1] = lines[line - 1].replace(before, after, 1)
            mutant = b"".join(lines)
            if _sha(source)[7:] != manifest["source_sha256"] or _sha(test)[7:] != manifest["testbench_sha256"]:
                errors.append("upstream_source_or_test_drift")
            if subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, check=True, timeout=20).stdout.decode().strip() != body["source_git_sha"]:
                errors.append("source_git_sha_drift")
            if subprocess.run(["git", "-C", str(repo), "status", "--porcelain=v1"],
                              capture_output=True, check=True, timeout=20).stdout:
                errors.append("source_clone_dirty")
            for name, expected_source in (("clean", source), ("fault", mutant)):
                arm = body["arms"][name]
                refs = arm["artifacts"]
                if any(not Path(ref["path"]).resolve().is_relative_to(root / name)
                       for ref in refs.values()):
                    errors.append(name + ":artifact_outside_arm")
                staged = Path(refs["staged_source"]["path"])
                if staged.read_bytes() != expected_source or Path(refs["staged_test"]["path"]).read_bytes() != test:
                    errors.append(name + ":stage_drift")
                build = Path(refs["compiled_vvp"]["path"]).read_bytes()
                if str(staged).encode() not in build:
                    errors.append(name + ":compiled_source_path")
                result = cocotb_verdict(
                    junit=Path(refs["cocotb_junit"]["path"]).read_bytes(),
                    runner_log=Path(refs["runner_log"]["path"]).read_bytes(),
                    expected_test_ids=COCOTB_SCOPES[0][5],
                    runner_exit=int(Path(refs["runner_exit"]["path"]).read_text().strip()))
                if result != arm["result"] or result.get("random_seed") != str(manifest["random_seed"]):
                    errors.append(name + ":verdict_or_seed_drift")
            if (body["arms"]["clean"]["result"]["verdict"] != "PASS" or
                    body["arms"]["fault"]["result"]["verdict"] != "FAIL"):
                errors.append("expected_clean_fault_pair_missing")
    except (OSError, ValueError, KeyError, IndexError, subprocess.CalledProcessError) as exc:
        errors.append("replay_unreadable:" + type(exc).__name__)
    return {"valid": not errors, "errors": errors, "digest": body.get("digest"),
            "clean_verdict": body["arms"]["clean"]["result"]["verdict"],
            "fault_verdict": body["arms"]["fault"]["result"]["verdict"]}
