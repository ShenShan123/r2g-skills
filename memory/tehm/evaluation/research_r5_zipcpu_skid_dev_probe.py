"""Preregister, execute, and cold-audit one ZipCPU native-formal DEV probe.

Only the evaluator reads the reference source and injected fault.  This is
oracle qualification, not TRAIN, Memory selection, or held-out transfer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
from pathlib import Path


SCHEMA = "tehm-r5-zipcpu-skid-native-formal-dev-v1"
REPO = "ZipCPU/wb2axip"
SOURCE = "rtl/skidbuffer.v"
NATIVE = "bench/formal/skidbuffer.sby"
TASKS = ("prfc", "prfo")
FAULT_LINE = 213
OLD = b"o_data <= r_data;"
NEW = b"o_data <= i_data;"
TOOL_BIN = Path("/data1/zhangdy/Tools/tehm-toolchain/oss-cad-suite/bin")
TOOL_NAMES = ("sby", "yosys", "yosys-smtbmc", "z3")


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":")) + "\n").encode()


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args],
                            capture_output=True, check=True, timeout=20)
    return result.stdout.decode().strip()


def _fault(source: bytes) -> bytes:
    lines = source.splitlines(keepends=True)
    if (len(lines) < FAULT_LINE or OLD not in lines[FAULT_LINE - 1] or
            source.count(OLD) != 1):
        raise ValueError("preregistered fault site is not unique at line 213")
    lines[FAULT_LINE - 1] = lines[FAULT_LINE - 1].replace(OLD, NEW, 1)
    return b"".join(lines)


def _tools() -> dict:
    entries = {}
    for name in TOOL_NAMES:
        path = (TOOL_BIN / name).resolve(strict=True)
        data = path.read_bytes()
        entries[name] = {"path": str(path), "sha256": _sha(data),
                         "bytes": len(data)}
    return entries


def _run(argv: list[str], *, cwd: Path, timeout: int) -> tuple[int, bytes]:
    env = {**os.environ, "PATH": str(TOOL_BIN) + os.pathsep + os.environ.get("PATH", "")}
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, start_new_session=True)
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            output, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            output, _ = proc.communicate()
        return 124, output
    return proc.returncode, output


def _ref(path: Path) -> dict:
    data = path.read_bytes()
    return {"path": str(path.resolve(strict=True)), "sha256": _sha(data),
            "bytes": len(data)}


def _read_arm(root: Path, *, name: str, task: str, expected_source: bytes) -> dict:
    arm = root / name / task
    stage = arm / "stage"
    proof = arm / "proof"
    status_path = proof / "status"
    native_log = proof / "logfile.txt"
    copied_source = proof / "src" / "skidbuffer.v"
    if not all(path.is_file() for path in (status_path, native_log, copied_source)):
        return {"verdict": "UNDETERMINED", "reason": "native_artifact_missing"}
    status = status_path.read_text(encoding="utf-8").strip()
    native_text = native_log.read_text(encoding="utf-8", errors="replace")
    exit_code = int((arm / "sby.exit").read_text(encoding="utf-8").strip())
    if copied_source.read_bytes() != expected_source:
        return {"verdict": "UNDETERMINED", "reason": "compiled_source_mismatch"}
    if status == "PASS" and exit_code == 0 and "Status: passed" in native_text:
        verdict, reason = "PASS", "native_formal_pass"
    elif (status == "FAIL" and exit_code == 1 and
          "Status: failed" in native_text and
          any((proof / "engine_0").rglob("*.vcd"))):
        verdict, reason = "FAIL", "native_formal_counterexample"
    else:
        verdict, reason = "UNDETERMINED", "native_status_exit_or_trace_mismatch"
    return {"verdict": verdict, "reason": reason, "native_status": status,
            "exit_code": exit_code,
            "artifacts": {key: _ref(path) for key, path in (
                ("staged_source", stage / SOURCE),
                ("staged_native", stage / NATIVE),
                ("compiled_source", copied_source),
                ("native_log", native_log),
                ("status", status_path),
                ("runner_log", arm / "sby.stdout"),
                ("runner_exit", arm / "sby.exit"))}}


def run(*, corpus: Path, work: Path, timeout: int = 180) -> dict:
    corpus = corpus.resolve(strict=True)
    work = work.resolve(strict=False)
    if work.exists() or work.parent != corpus / "_qualification":
        raise ValueError("DEV work must be a new _qualification child")
    repo = corpus / REPO
    if _git(repo, "status", "--porcelain=v1"):
        raise ValueError("upstream ZipCPU checkout is dirty")
    source = (repo / SOURCE).read_bytes()
    native = (repo / NATIVE).read_bytes()
    mutant = _fault(source)
    prereg = {"schema": SCHEMA, "role": "DEV_ORACLE_SENSITIVITY_NOT_TRAIN_OR_TRANSFER",
              "source_repository": REPO, "source_git_sha": _git(repo, "rev-parse", "HEAD"),
              "source_file": SOURCE, "source_sha256": _sha(source),
              "native_test_file": NATIVE, "native_test_sha256": _sha(native),
              "source_group": "ZipCPU_pending_relation_audit",
              "formal_tasks": list(TASKS),
              "target_task": "prfo", "preservation_task": "prfc",
              "public_parameters": {"DW": 8, "OPT_OUTREG": 1, "OPT_LOWPOWER": 0},
              "fault_line": FAULT_LINE, "fault_before": OLD.decode(),
              "fault_after": NEW.decode(),
              "target_obligation": "registered_output_uses_buffered_payload_when_r_valid",
              "preservation_obligation": "combinational_output_formal_scope_remains_pass",
              "qualified_status_on_pass_fail": "DETECTED_only_if_clean_both_pass_and_fault_prfo_fails_with_counterexample",
              "runtime_visible": False, "production_authority": False,
              "upstream_mutation_allowed": False,
              "timeout_seconds_per_task": timeout, "tool_files": _tools()}
    work.mkdir()
    (work / "preregistration.json").write_bytes(_json(prereg))
    for name, staged_source in (("clean", source), ("fault", mutant)):
        for task in TASKS:
            arm = work / name / task
            stage = arm / "stage"
            (stage / "rtl").mkdir(parents=True)
            (stage / "bench" / "formal").mkdir(parents=True)
            (stage / SOURCE).write_bytes(staged_source)
            (stage / NATIVE).write_bytes(native)
            proof = arm / "proof"
            argv = [str(TOOL_BIN / "sby"), "-d", str(proof),
                    str(stage / NATIVE), task]
            (arm / "command.json").write_bytes(_json(argv))
            exit_code, output = _run(argv, cwd=stage / "bench" / "formal",
                                      timeout=timeout)
            (arm / "sby.stdout").write_bytes(output)
            (arm / "sby.exit").write_text(str(exit_code) + "\n", encoding="utf-8")
    return verify(work)


def verify(work: Path) -> dict:
    root = work.resolve(strict=True)
    prereg_path = root / "preregistration.json"
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    errors = []
    if (prereg.get("schema") != SCHEMA or
            prereg.get("role") != "DEV_ORACLE_SENSITIVITY_NOT_TRAIN_OR_TRANSFER" or
            prereg.get("formal_tasks") != list(TASKS) or
            prereg.get("fault_line") != FAULT_LINE or
            prereg.get("fault_before") != OLD.decode() or
            prereg.get("fault_after") != NEW.decode()):
        errors.append("preregistration_contract_drift")
    corpus = root.parent.parent
    repo = corpus / REPO
    try:
        source = (repo / SOURCE).read_bytes()
        native = (repo / NATIVE).read_bytes()
        if (_git(repo, "rev-parse", "HEAD") != prereg["source_git_sha"] or
                _git(repo, "status", "--porcelain=v1") or
                _sha(source) != prereg["source_sha256"] or
                _sha(native) != prereg["native_test_sha256"] or
                _tools() != prereg["tool_files"]):
            errors.append("upstream_or_tool_lock_drift")
        mutant = _fault(source)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        return {"valid": False, "errors": errors + [f"input_unavailable:{exc}"]}
    arms = {}
    for name, expected_source in (("clean", source), ("fault", mutant)):
        for task in TASKS:
            arm = root / name / task
            stage = arm / "stage"
            try:
                if ((stage / SOURCE).read_bytes() != expected_source or
                        (stage / NATIVE).read_bytes() != native or
                        any(path.is_symlink() or path.name == ".git"
                            for path in stage.rglob("*"))):
                    errors.append(f"{name}/{task}:staged_input_drift")
                argv = json.loads((arm / "command.json").read_text(encoding="utf-8"))
                if argv != [str(TOOL_BIN / "sby"), "-d", str(arm / "proof"),
                            str(stage / NATIVE), task]:
                    errors.append(f"{name}/{task}:command_drift")
                arms[f"{name}/{task}"] = _read_arm(
                    root, name=name, task=task, expected_source=expected_source)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                errors.append(f"{name}/{task}:artifact_unavailable:{exc}")
    clean = all(arms.get(f"clean/{task}", {}).get("verdict") == "PASS" for task in TASKS)
    target_fail = arms.get("fault/prfo", {}).get("verdict") == "FAIL"
    preserve = arms.get("fault/prfc", {}).get("verdict") == "PASS"
    qualified = "DETECTED" if clean and target_fail and preserve and not errors else "UNDETERMINED"
    return {"valid": not errors, "qualification": qualified,
            "clean_all_pass": clean, "target_counterexample": target_fail,
            "preservation_pass": preserve, "errors": errors,
            "preregistration_sha256": _sha(prereg_path.read_bytes()), "arms": arms}


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run")
    run_p.add_argument("--corpus", type=Path, required=True)
    run_p.add_argument("--work", type=Path, required=True)
    run_p.add_argument("--timeout", type=int, default=180)
    verify_p = sub.add_parser("verify")
    verify_p.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    result = (run(corpus=args.corpus, work=args.work, timeout=args.timeout)
              if args.command == "run" else verify(args.work))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] and result["qualification"] == "DETECTED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
