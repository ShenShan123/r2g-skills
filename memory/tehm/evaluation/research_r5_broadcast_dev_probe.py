"""Run and cold-audit one preregistered axis_broadcast DEV oracle probe.

The upstream checkout is read-only. This does not create TRAIN evidence,
qualify other parameter points, or test TEHM Memory selection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import sys
from pathlib import Path

from .research_r5_verdict import cocotb_verdict


SCHEMA = "tehm-r5-broadcast-payload-dev-probe-v3"
REPO = "alexforencich/verilog-axis"
SOURCE = "rtl/axis_broadcast.v"
TEST = "tb/axis_broadcast/test_axis_broadcast.py"
WRAPPER_SCRIPT = "rtl/axis_broadcast_wrap.py"
WRAPPER = "tb/axis_broadcast/axis_broadcast_wrap_2.v"
NODE = "test_axis_broadcast[2-8]"
TEST_IDS = tuple(f"run_test_{i:03d}" for i in range(1, 5))
SEED = "20260924"
LINE = 169
BEFORE = b"        m_axis_tdata_reg <= temp_m_axis_tdata_reg;"
AFTER = b"        m_axis_tdata_reg <= s_axis_tdata;"


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _json(data: object) -> bytes:
    return (json.dumps(data, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":")) + "\n").encode()


def _ref(path: Path) -> dict:
    data = path.read_bytes()
    return {"path": str(path.resolve(strict=True)), "sha256": _sha(data),
            "bytes": len(data)}


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args],
                            capture_output=True, check=True, timeout=20)
    return result.stdout.decode().strip()


def _mutate(source: bytes) -> bytes:
    lines = source.splitlines(keepends=True)
    if len(lines) < LINE or lines[LINE - 1].rstrip(b"\r\n") != BEFORE:
        raise ValueError("preregistered broadcast fault line drifted")
    lines[LINE - 1] = lines[LINE - 1].replace(BEFORE, AFTER, 1)
    return b"".join(lines)


def _junit(arm: Path) -> Path:
    matches = list(arm.glob(
        "stage/tb/axis_broadcast/sim_build/test_axis_broadcast-2-8/*_results.xml"))
    if len(matches) != 1:
        raise ValueError(f"expected one broadcast JUnit, found {len(matches)}")
    return matches[0]


def _vvp(arm: Path) -> Path:
    matches = list(arm.glob(
        "stage/tb/axis_broadcast/sim_build/test_axis_broadcast-2-8/*.vvp"))
    if len(matches) != 1:
        raise ValueError(f"expected one broadcast VVP, found {len(matches)}")
    return matches[0]


def _tool_versions(pydeps: Path, wrapper_python: Path) -> dict:
    env = {**os.environ, "PYTHONPATH": str(pydeps), "PYTHONDONTWRITEBYTECODE": "1"}
    script = ("import json,pytest,cocotb,cocotb_test,cocotbext.axi;"
              "print(json.dumps({'pytest':pytest.__version__,'cocotb':cocotb.__version__}))")
    dep = subprocess.run([sys.executable, "-c", script], env=env,
                         capture_output=True, check=True, timeout=20)
    jinja = subprocess.run([str(wrapper_python), "-c",
                            "import jinja2;print(jinja2.__version__)"],
                           capture_output=True, check=True, timeout=20)
    iverilog = shutil.which("iverilog")
    if iverilog is None:
        raise ValueError("iverilog is unavailable")
    iv = subprocess.run([iverilog, "-V"], capture_output=True,
                        check=True, timeout=20)
    return {"test_python": str(Path(sys.executable).resolve()),
            "test_python_version": sys.version.split()[0],
            "pydeps": str(pydeps),
            "python_packages": json.loads(dep.stdout),
            "wrapper_python": str(wrapper_python),
            "jinja2": jinja.stdout.decode().strip(),
            "iverilog": str(Path(iverilog).resolve()),
            "iverilog_version": (iv.stdout + iv.stderr).decode(errors="replace").splitlines()[0]}


def run(*, corpus: Path, work: Path, pydeps: Path, wrapper_python: Path) -> dict:
    corpus = corpus.resolve(strict=True)
    work = work.resolve(strict=False)
    pydeps = pydeps.resolve(strict=True)
    wrapper_python = wrapper_python.resolve(strict=True)
    if work.exists() or not work.parent.is_dir() or work.parent != corpus / "_qualification":
        raise ValueError("new DEV work must be a fresh _qualification child")
    repo = corpus / REPO
    sha = _git(repo, "rev-parse", "HEAD")
    if _git(repo, "status", "--porcelain=v1"):
        raise ValueError("upstream broadcast checkout is dirty")
    original = (repo / SOURCE).read_bytes()
    mutant = _mutate(original)
    test = (repo / TEST).read_bytes()
    wrapper_script = (repo / WRAPPER_SCRIPT).read_bytes()
    tools = _tool_versions(pydeps, wrapper_python)
    prereg = {
        "schema": SCHEMA, "role": "DEV_ORACLE_SENSITIVITY_NOT_TRAIN_OR_TRANSFER",
        "source_repository": REPO, "source_git_sha": sha,
        "source_file": SOURCE, "source_sha256": _sha(original),
        "testbench_file": TEST, "testbench_sha256": _sha(test),
        "wrapper_script": WRAPPER_SCRIPT, "wrapper_script_sha256": _sha(wrapper_script),
        "test_node": NODE, "expected_test_ids": list(TEST_IDS),
        "public_parameters": {"DATA_WIDTH": 8, "M_COUNT": 2},
        "random_seed": SEED, "fault_line": LINE,
        "fault_before": BEFORE.decode(), "fault_after": AFTER.decode(),
        "target_obligation": "all_two_output_payloads_equal_corresponding_input_under_idle_and_backpressure",
        "preservation_obligation": "not_assessed_in_DEV_sensitivity_probe",
        "hypothesized_fault": "FAIL_IF_TEMP_TO_OUTPUT_PATH_EXERCISED_AND_CHECKED",
        "upstream_mutation_allowed": False, "learner_visible": False,
        "production_authority": False,
        "tool_versions": tools,
    }
    work.mkdir()
    (work / "preregistration.json").write_bytes(_json(prereg))
    arms = {}
    for name, source in (("clean", original), ("fault", mutant)):
        arm = work / name
        stage = arm / "stage"
        (stage / "rtl").mkdir(parents=True)
        (stage / "tb" / "axis_broadcast").mkdir(parents=True)
        (stage / SOURCE).write_bytes(source)
        (stage / TEST).write_bytes(test)
        (stage / WRAPPER_SCRIPT).write_bytes(wrapper_script)
        gen_cmd = [str(wrapper_python), str(stage / WRAPPER_SCRIPT), "-p", "2"]
        generated = subprocess.run(gen_cmd, cwd=stage / "tb" / "axis_broadcast",
                                   capture_output=True, timeout=30)
        (arm / "wrapper.log").write_bytes(generated.stdout + generated.stderr)
        (arm / "wrapper.exit").write_text(str(generated.returncode) + "\n")
        if generated.returncode or not (stage / WRAPPER).is_file():
            raise ValueError(f"wrapper generation failed in {name}")
        cmd = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
               "--show-capture=no", "-s",
               "-o", "log_cli=true", "-o", "log_cli_level=INFO", "-q",
               f"{TEST}::{NODE}"]
        env = {**os.environ, "PYTHONPATH": str(pydeps),
               "PYTHONDONTWRITEBYTECODE": "1", "RANDOM_SEED": SEED,
               "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
        outcome = subprocess.run(cmd, cwd=stage, env=env, capture_output=True,
                                 timeout=180)
        (arm / "pytest.log").write_bytes(outcome.stdout + outcome.stderr)
        (arm / "pytest.exit").write_text(str(outcome.returncode) + "\n")
        junit = _junit(arm)
        vvp = _vvp(arm)
        verdict = cocotb_verdict(junit=junit.read_bytes(),
                                 runner_log=(arm / "pytest.log").read_bytes(),
                                 expected_test_ids=TEST_IDS,
                                 runner_exit=outcome.returncode)
        if verdict.get("random_seed") != SEED:
            raise ValueError(f"seed mismatch in {name}")
        if str(stage / SOURCE).encode() not in vvp.read_bytes():
            raise ValueError(f"VVP did not compile staged source in {name}")
        arms[name] = {"verdict": verdict, "pytest_command": cmd,
                      "wrapper_command": gen_cmd, "artifacts": {
                          label: _ref(path) for label, path in (
                              ("staged_source", stage / SOURCE),
                              ("staged_test", stage / TEST),
                              ("staged_wrapper_script", stage / WRAPPER_SCRIPT),
                              ("generated_wrapper", stage / WRAPPER),
                              ("wrapper_log", arm / "wrapper.log"),
                              ("wrapper_exit", arm / "wrapper.exit"),
                              ("pytest_log", arm / "pytest.log"),
                              ("pytest_exit", arm / "pytest.exit"),
                              ("cocotb_junit", junit), ("compiled_vvp", vvp))}}
    receipt = {"schema": SCHEMA, "role": prereg["role"],
               "preregistration": _ref(work / "preregistration.json"),
               "source_git_sha": sha, "pydeps_path": str(pydeps),
               "wrapper_python": str(wrapper_python),
               "verdict_adapter_code": _ref(Path(cocotb_verdict.__code__.co_filename)),
               "auditor_code": _ref(Path(__file__)), "arms": arms,
               "limit": "one fixed DEV parameter point and seed; no TRAIN, transfer, or Memory"}
    receipt["digest"] = _sha(_json(receipt))
    out = work / "receipt-r1.json"
    out.write_bytes(_json(receipt))
    return verify(out)


def _strict_replay_errors(receipt: dict, prereg: dict, repo: Path, work: Path) -> list[str]:
    """Rebuild wrapper and check exact arm containment/compile inputs."""
    errors = []
    expected = {
        "schema": SCHEMA, "role": "DEV_ORACLE_SENSITIVITY_NOT_TRAIN_OR_TRANSFER",
        "source_repository": REPO, "source_file": SOURCE,
        "testbench_file": TEST, "wrapper_script": WRAPPER_SCRIPT,
        "test_node": NODE, "expected_test_ids": list(TEST_IDS),
        "public_parameters": {"DATA_WIDTH": 8, "M_COUNT": 2},
        "random_seed": SEED, "fault_line": LINE,
        "fault_before": BEFORE.decode(), "fault_after": AFTER.decode(),
        "upstream_mutation_allowed": False, "learner_visible": False,
        "production_authority": False,
    }
    if any(prereg.get(key) != value for key, value in expected.items()):
        errors.append("preregistration_contract_drift")
    if receipt.get("source_git_sha") != prereg.get("source_git_sha"):
        errors.append("receipt_source_sha_drift")
    tools = prereg.get("tool_versions") or {}
    if (receipt.get("pydeps_path") != tools.get("pydeps") or
            receipt.get("wrapper_python") != tools.get("wrapper_python")):
        errors.append("receipt_tool_path_drift")
    with tempfile.TemporaryDirectory(prefix="tehm-r5-broadcast-wrapper-") as tmp:
        rebuilt = Path(tmp) / "axis_broadcast_wrap_2.v"
        command = [str(tools["wrapper_python"]), str(repo / WRAPPER_SCRIPT),
                   "-p", "2", "-o", str(rebuilt)]
        build = subprocess.run(command, cwd=tmp, capture_output=True, timeout=30)
        if build.returncode or not rebuilt.is_file():
            errors.append("wrapper_rebuild_failed")
            rebuilt_bytes = None
        else:
            rebuilt_bytes = rebuilt.read_bytes()
    for name in ("clean", "fault"):
        arm = work / name
        stage = arm / "stage"
        artifacts = receipt["arms"][name]["artifacts"]
        for label, ref in artifacts.items():
            if not Path(ref["path"]).resolve().is_relative_to(arm):
                errors.append(name + ":artifact_outside_arm:" + label)
        if stage.is_symlink():
            errors.append(name + ":stage_symlink")
        wrapper = stage / WRAPPER
        if rebuilt_bytes is None or wrapper.read_bytes() != rebuilt_bytes:
            errors.append(name + ":wrapper_rebuild_drift")
        if (arm / "wrapper.exit").read_text().strip() != "0":
            errors.append(name + ":wrapper_exit")
        expected_wrapper = [str(tools["wrapper_python"]),
                            str(stage / WRAPPER_SCRIPT), "-p", "2"]
        if receipt["arms"][name].get("wrapper_command") != expected_wrapper:
            errors.append(name + ":wrapper_command_drift")
        command = receipt["arms"][name].get("pytest_command") or []
        if (not command or str(Path(command[0]).resolve()) != tools.get("test_python") or
                command[1:] != ["-m", "pytest", "-p", "no:cacheprovider",
                               "--show-capture=no", "-s", "-o", "log_cli=true",
                               "-o", "log_cli_level=INFO", "-q", f"{TEST}::{NODE}"]):
            errors.append(name + ":pytest_command_drift")
        log = (arm / "pytest.log").read_text(errors="replace")
        compile_lines = [line for line in log.splitlines()
                         if "Running command: iverilog " in line]
        if len(compile_lines) != 1:
            errors.append(name + ":compile_command_count")
        else:
            compile_line = compile_lines[0]
            required = (str(stage / SOURCE), str(wrapper),
                        "-s axis_broadcast_wrap_2",
                        "-Paxis_broadcast_wrap_2.DATA_WIDTH=8")
            if any(value not in compile_line for value in required):
                errors.append(name + ":compile_command_inputs")
    return errors


def verify(receipt_path: Path) -> dict:
    path = receipt_path.resolve(strict=True)
    receipt = json.loads(path.read_text(encoding="utf-8"))
    errors = []
    no_digest = {k: v for k, v in receipt.items() if k != "digest"}
    if receipt.get("schema") != SCHEMA or receipt.get("digest") != _sha(_json(no_digest)):
        errors.append("receipt_digest_or_schema")
    prereg = json.loads(Path(receipt["preregistration"]["path"]).read_text())
    work = path.parent
    corpus = work.parent.parent
    repo = corpus / REPO
    original = (repo / SOURCE).read_bytes()
    mutant = _mutate(original)
    if (_git(repo, "rev-parse", "HEAD") != prereg["source_git_sha"] or
            _git(repo, "status", "--porcelain=v1")):
        errors.append("upstream_git_drift")
    for label, expected in (("source_sha256", original),
                            ("testbench_sha256", (repo / TEST).read_bytes()),
                            ("wrapper_script_sha256", (repo / WRAPPER_SCRIPT).read_bytes())):
        if prereg.get(label) != _sha(expected):
            errors.append(label + "_drift")
    refs = [receipt["preregistration"], receipt["verdict_adapter_code"],
            receipt["auditor_code"]]
    tools = prereg.get("tool_versions")
    if (not isinstance(tools, dict) or
            tools != _tool_versions(Path(tools["pydeps"]), Path(tools["wrapper_python"]))):
        errors.append("tool_versions_drift")
    refs += [r for arm in receipt["arms"].values() for r in arm["artifacts"].values()]
    for ref in refs:
        try:
            data = Path(ref["path"]).read_bytes()
            if len(data) != ref["bytes"] or _sha(data) != ref["sha256"]:
                errors.append("artifact_drift:" + ref["path"])
        except OSError:
            errors.append("artifact_missing:" + ref["path"])
    for name, source in (("clean", original), ("fault", mutant)):
        arm = receipt["arms"][name]
        artifacts = arm["artifacts"]
        stage = work / name / "stage"
        if (Path(artifacts["staged_source"]["path"]).read_bytes() != source or
                Path(artifacts["staged_test"]["path"]).read_bytes() != (repo / TEST).read_bytes() or
                Path(artifacts["staged_wrapper_script"]["path"]).read_bytes() != (repo / WRAPPER_SCRIPT).read_bytes()):
            errors.append(name + ":stage_drift")
        if any(p.is_symlink() or p.name == ".git" for p in stage.rglob("*")):
            errors.append(name + ":stage_visibility")
        if str(stage / SOURCE).encode() not in Path(
                artifacts["compiled_vvp"]["path"]).read_bytes():
            errors.append(name + ":compiled_source_drift")
        verdict = cocotb_verdict(
            junit=Path(artifacts["cocotb_junit"]["path"]).read_bytes(),
            runner_log=Path(artifacts["pytest_log"]["path"]).read_bytes(),
            expected_test_ids=TEST_IDS,
            runner_exit=int(Path(artifacts["pytest_exit"]["path"]).read_text()))
        if verdict != arm["verdict"] or verdict.get("random_seed") != SEED:
            errors.append(name + ":verdict_or_seed_drift")
    errors.extend(_strict_replay_errors(receipt, prereg, repo, work))
    return {"valid": not errors, "errors": errors,
            "digest": receipt.get("digest"),
            "clean": receipt["arms"]["clean"]["verdict"],
            "fault": receipt["arms"]["fault"]["verdict"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run")
    run_p.add_argument("--corpus", type=Path, required=True)
    run_p.add_argument("--work", type=Path, required=True)
    run_p.add_argument("--pydeps", type=Path, required=True)
    run_p.add_argument("--wrapper-python", type=Path, required=True)
    verify_p = sub.add_parser("verify")
    verify_p.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    result = (run(corpus=args.corpus, work=args.work, pydeps=args.pydeps,
                  wrapper_python=args.wrapper_python)
              if args.command == "run" else verify(args.receipt))
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
