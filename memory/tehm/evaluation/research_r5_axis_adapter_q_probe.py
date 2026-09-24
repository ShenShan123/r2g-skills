"""Isolated, evaluator-only native sensitivity probe for one R5 axis_adapter scope."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

from .research_r5_verdict import cocotb_verdict


SCHEMA = "tehm-r5-axis-adapter-skid-qualification-v1"
REPO = "alexforencich/verilog-axis"
SOURCE = "rtl/axis_adapter.v"
TEST = "tb/axis_adapter/test_axis_adapter.py"
NODE = "test_axis_register[8-16]"
SEED = "20260924"
BEFORE = b"            s_axis_tdata_reg <= s_axis_tdata;"
AFTER = b"            s_axis_tdata_reg <= ~s_axis_tdata;"
TEST_IDS = (
    *(f"run_test_{index:03d}" for index in range(1, 5)),
    "run_test_tuser_assert_001",
    *(f"run_stress_test_{index:03d}" for index in range(1, 5)),
)
COMMAND = ["/usr/bin/python3", "-m", "pytest", "-p", "no:cacheprovider",
           "--show-capture=no", "-s", "-o", "log_cli=true", "-o",
           "log_cli_level=INFO", "-q", f"{TEST}::{NODE}"]


def sha(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def canonical(payload: dict) -> bytes:
    return (json.dumps(payload, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":")) + "\n").encode()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def artifact(path: Path) -> dict:
    payload = path.read_bytes()
    return {"path": str(path.resolve(strict=True)), "sha256": sha(payload),
            "bytes": len(payload)}


def stage_inputs(work: Path, corpus: Path) -> dict:
    prereg_path = work / "preregistration.json"
    prereg = json.loads(prereg_path.read_text())
    repo = corpus / REPO
    require(prereg["role"] == "EVALUATOR_ONLY_QUALIFICATION_NOT_TRAIN_OR_TRANSFER",
            "wrong preregistered role")
    require(prereg["test_node"] == NODE and prereg["seed"] == SEED,
            "node or seed drift")
    require(prereg["fault"]["before"].encode() == BEFORE and
            prereg["fault"]["after"].encode() == AFTER and
            prereg["fault"]["line_number_at_locked_source"] == 211,
            "fault preregistration drift")
    require(git(repo, "rev-parse", "HEAD") == prereg["source_commit"] and
            not git(repo, "status", "--porcelain"), "upstream checkout drift")
    original = (repo / SOURCE).read_bytes()
    test = (repo / TEST).read_bytes()
    require(sha(original)[7:] == prereg["source_sha256"] and
            sha(test)[7:] == prereg["test_sha256"], "upstream file drift")
    lines = original.splitlines(keepends=True)
    require(lines[210].rstrip(b"\r\n") == BEFORE, "fault line drift")
    lines[210] = lines[210].replace(BEFORE, AFTER, 1)
    mutant = b"".join(lines)
    for name, source in (("clean", original), ("fault", mutant)):
        stage = work / name / "stage"
        require(not stage.is_symlink() and not any(p.is_symlink() for p in stage.rglob("*")),
                f"symlink in {name} stage")
        require(not (stage / ".git").exists(), f"git metadata in {name} stage")
        require((stage / SOURCE).read_bytes() == source and
                (stage / TEST).read_bytes() == test, f"staged input drift: {name}")
    return {"prereg": prereg, "repo": repo, "original": original,
            "mutant": mutant, "test": test}


def locate_junit(stage: Path) -> Path:
    matches = list((stage / "tb/axis_adapter/sim_build/test_axis_register-8-16")
                   .glob("*_results.xml"))
    require(len(matches) == 1, f"expected one cocotb JUnit, found {len(matches)}")
    return matches[0]


def locate_vvp(stage: Path) -> Path:
    matches = list((stage / "tb/axis_adapter/sim_build/test_axis_register-8-16")
                   .glob("*.vvp"))
    require(len(matches) == 1, f"expected one compiled VVP, found {len(matches)}")
    return matches[0]


def interpret(clean: dict, fault: dict, fault_log: bytes) -> str:
    if clean.get("verdict") != "PASS":
        return "UNDETERMINED"
    if fault.get("verdict") == "PASS":
        return "MISSED"
    if (fault.get("verdict") == "FAIL" and
            b"assert rx_frame.tdata == test_frame.tdata" in fault_log):
        return "DETECTED"
    return "UNDETERMINED"


def run(work: Path, corpus: Path, pydeps: Path) -> dict:
    work = work.resolve(strict=True)
    corpus = corpus.resolve(strict=True)
    pydeps = pydeps.resolve(strict=True)
    require(not (work / "receipt-r1.json").exists(), "receipt already exists")
    stage_inputs(work, corpus)
    arms = {}
    for name in ("clean", "fault"):
        arm = work / name
        stage = arm / "stage"
        env = {**os.environ, "PYTHONPATH": str(pydeps),
               "PYTHONDONTWRITEBYTECODE": "1", "RANDOM_SEED": SEED,
               "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
        outcome = subprocess.run(COMMAND, cwd=stage, env=env, capture_output=True,
                                 timeout=180)
        log_path = arm / "pytest.log"
        exit_path = arm / "pytest.exit"
        log_path.write_bytes(outcome.stdout + outcome.stderr)
        exit_path.write_text(str(outcome.returncode) + "\n")
        junit = locate_junit(stage)
        vvp = locate_vvp(stage)
        require(str(stage / SOURCE).encode() in vvp.read_bytes(),
                f"VVP did not compile staged source: {name}")
        verdict = cocotb_verdict(junit=junit.read_bytes(),
                                 runner_log=log_path.read_bytes(),
                                 expected_test_ids=TEST_IDS,
                                 runner_exit=outcome.returncode)
        require(verdict.get("random_seed") == SEED, f"seed drift: {name}")
        arms[name] = {"verdict": verdict, "pytest_command": COMMAND,
                      "artifacts": {
                          label: artifact(path) for label, path in (
                              ("staged_source", stage / SOURCE),
                              ("staged_test", stage / TEST),
                              ("pytest_log", log_path),
                              ("pytest_exit", exit_path),
                              ("cocotb_junit", junit),
                              ("compiled_vvp", vvp))}}
    sensitivity = interpret(arms["clean"]["verdict"], arms["fault"]["verdict"],
                            (work / "fault/pytest.log").read_bytes())
    receipt = {"schema": SCHEMA, "role": "QUALIFICATION_ONLY",
               "preregistration": artifact(work / "preregistration.json"),
               "source_git_sha": git(corpus / REPO, "rev-parse", "HEAD"),
               "pydeps_path": str(pydeps),
               "auditor_code": artifact(Path(__file__)),
               "verdict_adapter_code": artifact(Path(cocotb_verdict.__code__.co_filename)),
               "arms": arms, "sensitivity": sensitivity,
               "limit": "one native fault and one parameter scope; no TRAIN, transfer, or Memory"}
    receipt["digest"] = sha(canonical(receipt))
    receipt_path = work / "receipt-r1.json"
    receipt_path.write_bytes(canonical(receipt))
    return verify(receipt_path, corpus)


def verify(receipt_path: Path, corpus: Path) -> dict:
    receipt_path = receipt_path.resolve(strict=True)
    corpus = corpus.resolve(strict=True)
    work = receipt_path.parent
    receipt = json.loads(receipt_path.read_text())
    unsigned = {key: value for key, value in receipt.items() if key != "digest"}
    require(receipt["schema"] == SCHEMA and receipt["role"] == "QUALIFICATION_ONLY" and
            receipt["digest"] == sha(canonical(unsigned)), "receipt digest or schema")
    stage_inputs(work, corpus)
    require(artifact(work / "preregistration.json") == receipt["preregistration"],
            "preregistration artifact drift")
    require(receipt["source_git_sha"] == git(corpus / REPO, "rev-parse", "HEAD"),
            "source commit drift")
    require(artifact(Path(__file__)) == receipt["auditor_code"] and
            artifact(Path(cocotb_verdict.__code__.co_filename)) ==
            receipt["verdict_adapter_code"], "code identity drift")
    require(receipt["pydeps_path"] == str(Path(receipt["pydeps_path"]).resolve(strict=True)),
            "pydeps path drift")
    replay = {}
    for name in ("clean", "fault"):
        arm = work / name
        stage = arm / "stage"
        row = receipt["arms"][name]
        require(row["pytest_command"] == COMMAND, f"runner command drift: {name}")
        paths = {label: Path(ref["path"]) for label, ref in row["artifacts"].items()}
        for label, path in paths.items():
            require(path.resolve(strict=True).is_relative_to(arm) and
                    artifact(path) == row["artifacts"][label],
                    f"artifact drift or escape: {name}:{label}")
        junit = locate_junit(stage)
        vvp = locate_vvp(stage)
        require(paths["cocotb_junit"] == junit and paths["compiled_vvp"] == vvp and
                str(stage / SOURCE).encode() in vvp.read_bytes(),
                f"compiled input drift: {name}")
        log = paths["pytest_log"].read_bytes()
        require(str(stage / SOURCE).encode() in log,
                f"compile command omitted staged RTL: {name}")
        exit_code = int(paths["pytest_exit"].read_text().strip())
        verdict = cocotb_verdict(junit=junit.read_bytes(), runner_log=log,
                                 expected_test_ids=TEST_IDS, runner_exit=exit_code)
        require(verdict == row["verdict"] and verdict.get("random_seed") == SEED,
                f"verdict replay drift: {name}")
        replay[name] = verdict
    sensitivity = interpret(replay["clean"], replay["fault"],
                            (work / "fault/pytest.log").read_bytes())
    require(sensitivity == receipt["sensitivity"], "sensitivity drift")
    return {"valid": True, "scope": "evaluator-only native qualification",
            "sensitivity": sensitivity,
            "clean": replay["clean"], "fault": replay["fault"],
            "receipt_digest": receipt["digest"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    run_parser = subs.add_parser("run")
    run_parser.add_argument("--work", type=Path, required=True)
    run_parser.add_argument("--corpus", type=Path, required=True)
    run_parser.add_argument("--pydeps", type=Path, required=True)
    verify_parser = subs.add_parser("verify")
    verify_parser.add_argument("--receipt", type=Path, required=True)
    verify_parser.add_argument("--corpus", type=Path, required=True)
    args = parser.parse_args()
    result = (run(args.work, args.corpus, args.pydeps) if args.command == "run"
              else verify(args.receipt, args.corpus))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
