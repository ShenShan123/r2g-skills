"""Evaluator-only R5 TRAIN replay of an observed axis_register DEV fault.

Fresh native cocotb runs are a training causal record, not an unseen transfer
or an automatic TEHM Knowledge/Asset authority decision.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from . import research_r5_dev_probe as dev
from .research_r5_qualification import COCOTB_SCOPES
from .research_r5_skid_binding_v3 import (
    CONTEXTS, TEMPLATE, apply_bound_skid_payload_v3, bind_skid_payload_v3,
)
from .research_r5_verdict import cocotb_verdict


SCHEMA = "tehm-r5-axis-register-reused-dev-train-v1"
ROLE = "TRAIN_REUSED_DEV_RESEARCHER_ASSISTED"
CORPUS = Path("/data1/zhangdy/RTL/RTL_testbench")
TRAIN_ROOT = CORPUS / "_r5_pilot" / "training"
REPO = "alexforencich/verilog-axis"
HEAD = "48ff7a7e2ef782cf778d47910cf85835c64b1bce"
SOURCE = "rtl/axis_register.v"
TEST = "tb/axis_register/test_axis_register.py"
SOURCE_SHA = "sha256:599fde2d6c2d806643bbffb7c444297e69a71871f962d4b741ec1914342e0d39"
TEST_SHA = "sha256:46fd1f60b14f9057694251aa2fc31ea155d54a970adb57197ecf179380e8d0cb"
PYDEPS = CORPUS / "_qualification" / "pydeps-py310"
SEED = "20260924"
NODE = "test_axis_register[8-2]"
IDS = COCOTB_SCOPES[0][5]
TARGET_IDS = ("run_test_002", "run_stress_test_002")
PRESERVATION_IDS = tuple(item for item in IDS if item not in TARGET_IDS)
CONTEXT = CONTEXTS["register"]
ARMS = ("clean", "fault", "candidate")


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":")) + "\n").encode()


def _ref(path: Path) -> dict:
    data = path.read_bytes()
    return {"path": str(path.resolve(strict=True)), "sha256": _sha(data),
            "bytes": len(data)}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, timeout=20).stdout.decode().strip()


def _inputs() -> tuple[bytes, bytes]:
    repo = CORPUS / REPO
    if _git(repo, "rev-parse", "HEAD") != HEAD or _git(repo, "status", "--porcelain=v1"):
        raise ValueError("axis checkout HEAD or clean state drift")
    source, test = (repo / SOURCE).read_bytes(), (repo / TEST).read_bytes()
    if _sha(source) != SOURCE_SHA or _sha(test) != TEST_SHA:
        raise ValueError("frozen axis source or test drift")
    return source, test


def _fault(source: bytes) -> bytes:
    lines = source.splitlines(keepends=True)
    before = b"            m_axis_tdata_reg <= temp_m_axis_tdata_reg;"
    after = b"            m_axis_tdata_reg <= s_axis_tdata;"
    if (len(lines) < 173 or lines[172].rstrip(b"\r\n") != before or
            source.count(before) != 1):
        raise ValueError("preregistered axis fault site drift")
    lines[172] = lines[172].replace(before, after, 1)
    return b"".join(lines)


def _tree_lock() -> str:
    if not PYDEPS.is_dir() or PYDEPS.is_symlink():
        raise ValueError("axis Python dependency tree missing or linked")
    entries = []
    for path in sorted(PYDEPS.rglob("*")):
        if path.is_symlink():
            raise ValueError("axis Python dependency tree contains link")
        if path.is_file():
            entries.append([path.relative_to(PYDEPS).as_posix(), _sha(path.read_bytes())])
    return _sha(_json(entries))


def _code_locks() -> dict:
    from . import research_r5_skid_binding as v1
    from . import research_r5_skid_binding_v2 as v2
    from . import research_r5_skid_binding_v3 as v3
    from tehm.rtl import verilog_parse
    modules = (sys.modules[__name__], v1, v2, v3, verilog_parse, dev)
    return {module.__name__: _sha(Path(module.__file__).read_bytes())
            for module in modules}


def _runtime() -> dict:
    return {"python": _ref(Path(sys.executable)),
            "iverilog": _ref(Path("/usr/bin/iverilog")),
            "vvp": _ref(Path("/usr/bin/vvp")),
            "pydeps_tree_sha256": _tree_lock(),
            "code_locks": _code_locks()}


def _root(work: Path) -> Path:
    root = work.resolve(strict=False)
    if root.parent != TRAIN_ROOT or root.is_symlink():
        raise ValueError("TRAIN work must be a direct non-link child of R5 training")
    return root


def prepare(work: Path) -> dict:
    root = _root(work)
    if root.exists():
        raise ValueError("refusing to overwrite TRAIN campaign")
    source, test = _inputs()
    manifest = {
        "schema": SCHEMA, "role": ROLE, "dev_reuse_disclosed": True,
        "unseen_transfer": False, "production_authority": False,
        "source_repository": REPO, "source_git_sha": HEAD,
        "source_file": SOURCE, "source_sha256": _sha(source),
        "native_test": TEST, "native_test_sha256": _sha(test),
        "pytest_node": NODE, "expected_test_ids": list(IDS),
        "target_ids": list(TARGET_IDS),
        "preservation_ids": list(PRESERVATION_IDS),
        "fault_line": 173,
        "fault_before": "            m_axis_tdata_reg <= temp_m_axis_tdata_reg;",
        "fault_after": "            m_axis_tdata_reg <= s_axis_tdata;",
        "public_context": CONTEXT, "seed": SEED,
        "candidate_method": "frozen_v3_source_only_researcher_assisted",
        "oracle": "native_cocotb_junit_v2",
        "expected": {"clean": "PASS", "fault": "FAIL", "candidate": "PASS"},
        "timeout_seconds": 180, "runtime": _runtime(),
        "visibility": {"binder": ["buggy_rtl", "public_context", "draft_template"],
                       "agent_stage": ["buggy_rtl"],
                       "evaluator_private": ["clean_source", "fault_metadata",
                                             "native_test", "expected_verdicts"]},
    }
    root.mkdir()
    data = _json(manifest)
    (root / "preregistration.json").write_bytes(data)
    (root / "preregistration.sha256").write_text(_sha(data) + "\n", encoding="ascii")
    return {"prepared": True, "preregistration_sha256": _sha(data), "work": str(root)}


def _prereg(root: Path) -> dict:
    data = (root / "preregistration.json").read_bytes()
    manifest = json.loads(data)
    expected = {"schema": SCHEMA, "role": ROLE, "dev_reuse_disclosed": True,
                "unseen_transfer": False, "production_authority": False,
                "source_repository": REPO, "source_git_sha": HEAD,
                "source_file": SOURCE, "source_sha256": SOURCE_SHA,
                "native_test": TEST, "native_test_sha256": TEST_SHA,
                "pytest_node": NODE, "expected_test_ids": list(IDS),
                "target_ids": list(TARGET_IDS),
                "preservation_ids": list(PRESERVATION_IDS),
                "public_context": CONTEXT, "seed": SEED,
                "oracle": "native_cocotb_junit_v2",
                "expected": {"clean": "PASS", "fault": "FAIL", "candidate": "PASS"},
                "timeout_seconds": 180, "runtime": _runtime()}
    if ((root / "preregistration.sha256").read_text(encoding="ascii").strip() != _sha(data) or
            any(manifest.get(key) != value for key, value in expected.items()) or
            manifest.get("fault_line") != 173 or
            manifest.get("fault_before") !=
            "            m_axis_tdata_reg <= temp_m_axis_tdata_reg;" or
            manifest.get("fault_after") !=
            "            m_axis_tdata_reg <= s_axis_tdata;"):
        raise ValueError("TRAIN preregistration or software epoch drift")
    return manifest


def _paths(arm: Path) -> tuple[Path, Path]:
    build = arm / "stage" / "tb" / "axis_register" / "sim_build" / "test_axis_register-8-2"
    matches = list(build.glob("*_results.xml"))
    if len(matches) != 1:
        raise ValueError("exactly one cocotb JUnit required")
    return matches[0], build / "axis_register.vvp"


def _case_results(junit: Path) -> dict:
    suite = ET.fromstring(junit.read_bytes())[0]
    return {case.get("name"): "FAIL" if case.find("failure") is not None else "PASS"
            for case in suite.findall("testcase")}


def _assess(arm: Path) -> dict:
    junit, vvp = _paths(arm)
    log = arm / "pytest.log"
    result = cocotb_verdict(junit=junit.read_bytes(), runner_log=log.read_bytes(),
                            expected_test_ids=IDS,
                            runner_exit=int((arm / "pytest.exit").read_text().strip()))
    if result.get("random_seed") != SEED:
        raise ValueError("native random seed drift")
    cases = _case_results(junit)
    if set(cases) != set(IDS):
        raise ValueError("native case registry drift")
    staged = arm / "stage" / SOURCE
    if str(staged).encode() not in vvp.read_bytes():
        raise ValueError("compiled VVP did not identify staged RTL")
    compile_lines = re.findall(r"(?m)^.*Running command: iverilog .*axis_register\.v\s*$",
                               log.read_text(encoding="utf-8", errors="replace"))
    if len(compile_lines) != 1 or str(staged) not in compile_lines[0]:
        raise ValueError("native compile argv did not identify staged RTL")
    return {"verdict": result, "cases": cases,
            "compile_command": compile_lines[0].split("Running command: ", 1)[1],
            "artifacts": {name: _ref(path) for name, path in (
                ("staged_source", staged), ("native_test", arm / "stage" / TEST),
                ("runner_log", log), ("runner_exit", arm / "pytest.exit"),
                ("junit", junit), ("compiled_image", vvp))}}


def run(work: Path) -> dict:
    root = _root(work).resolve(strict=True)
    _prereg(root)
    source, test = _inputs()
    fault = _fault(source)
    binding = bind_skid_payload_v3({"binding_template": TEMPLATE},
                                   fault.decode("utf-8"), CONTEXT)
    if binding.get("status") != "BOUND":
        raise ValueError("v3 binder did not bind TRAIN source")
    candidate, action = apply_bound_skid_payload_v3(
        {"binding_template": TEMPLATE}, fault.decode("utf-8"), CONTEXT, binding)
    if any((root / name).exists() for name in ARMS) or (root / "receipt.json").exists():
        raise ValueError("TRAIN attempt exists; no in-place rerun")
    (root / "agent-inputs" / "rtl").mkdir(parents=True)
    (root / "agent-inputs" / SOURCE).write_bytes(fault)
    (root / "binding.json").write_bytes(_json({"binding": binding, "action": action}))
    env = {**os.environ, "PYTHONPATH": str(PYDEPS), "PYTHONDONTWRITEBYTECODE": "1",
           "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "RANDOM_SEED": SEED}
    command = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
               "--show-capture=no", "-s", "-o", "log_cli=true",
               "-o", "log_cli_level=INFO", "-q", f"{TEST}::{NODE}"]
    for name, data in (("clean", source), ("fault", fault),
                       ("candidate", candidate.encode("utf-8"))):
        arm = root / name
        stage = arm / "stage"
        (stage / "rtl").mkdir(parents=True)
        (stage / "tb" / "axis_register").mkdir(parents=True)
        (stage / SOURCE).write_bytes(data)
        (stage / TEST).write_bytes(test)
        (arm / "command.json").write_bytes(_json(command))
        try:
            outcome = subprocess.run(command, cwd=stage, env=env,
                                     capture_output=True, timeout=180)
            code, log = outcome.returncode, outcome.stdout + outcome.stderr
        except subprocess.TimeoutExpired as exc:
            code, log = 124, (exc.stdout or b"") + (exc.stderr or b"") + b"\nTIMEOUT\n"
        (arm / "pytest.log").write_bytes(log)
        (arm / "pytest.exit").write_text(str(code) + "\n", encoding="ascii")
    receipt = verify(root)
    (root / "receipt.json").write_bytes(_json(receipt))
    return receipt


def verify(work: Path) -> dict:
    root = _root(work).resolve(strict=True)
    manifest = _prereg(root)
    source, test = _inputs()
    fault = _fault(source)
    errors: list[str] = []
    agent = root / "agent-inputs"
    if (agent / SOURCE).read_bytes() != fault or sorted(
            path.relative_to(agent).as_posix() for path in agent.rglob("*") if path.is_file()
            ) != [SOURCE] or any(path.is_symlink() or path.name == ".git"
                                for path in agent.rglob("*")):
        errors.append("agent_stage_drift")
    binding = bind_skid_payload_v3({"binding_template": TEMPLATE},
                                   fault.decode("utf-8"), CONTEXT)
    candidate, action = apply_bound_skid_payload_v3(
        {"binding_template": TEMPLATE}, fault.decode("utf-8"), CONTEXT, binding)
    if json.loads((root / "binding.json").read_text(encoding="utf-8")) != {
            "binding": binding, "action": action}:
        errors.append("source_only_binding_drift")
    expected = {"clean": source, "fault": fault,
                "candidate": candidate.encode("utf-8")}
    arms = {}
    for name, data in expected.items():
        arm = root / name
        stage = arm / "stage"
        try:
            if ((stage / SOURCE).read_bytes() != data or
                    (stage / TEST).read_bytes() != test or
                    any(path.is_symlink() or path.name == ".git"
                        for path in stage.rglob("*"))):
                errors.append(name + ":stage_drift")
            command = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
                       "--show-capture=no", "-s", "-o", "log_cli=true",
                       "-o", "log_cli_level=INFO", "-q", f"{TEST}::{NODE}"]
            if json.loads((arm / "command.json").read_text(encoding="utf-8")) != command:
                errors.append(name + ":command_drift")
            arm_result = _assess(arm)
            if arm_result["verdict"]["verdict"] != manifest["expected"][name]:
                errors.append(name + ":unexpected_verdict")
            if name == "fault" and (
                    any(arm_result["cases"][item] != "FAIL" for item in TARGET_IDS) or
                    any(arm_result["cases"][item] != "PASS" for item in PRESERVATION_IDS)):
                errors.append("fault_target_or_preservation_mismatch")
            if name != "fault" and any(value != "PASS"
                                       for value in arm_result["cases"].values()):
                errors.append(name + ":case_failure")
            arms[name] = arm_result
        except (OSError, ValueError, KeyError, ET.ParseError) as exc:
            errors.append(name + ":artifact_unavailable:" + type(exc).__name__)
    if expected["candidate"] != source:
        errors.append("private_candidate_clean_mismatch")
    result = {"schema": SCHEMA, "role": ROLE, "valid": not errors,
              "errors": errors,
              "training_transition": "researcher_assisted_not_core_admitted",
              "source_lineage_status": "same_group_as_broadcast_other_group_not_verified",
              "transfer_status": "NOT_RUN", "memory_status": "M_MINUS_ONLY",
              "preregistration": _ref(root / "preregistration.json"),
              "agent_input": _ref(agent / SOURCE),
              "binding": _ref(root / "binding.json"), "arms": arms}
    result["digest"] = _sha(_json(result))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "run", "verify"):
        sub.add_parser(command).add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    result = {"prepare": prepare, "run": run, "verify": verify}[args.command](args.work)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("prepared") or result.get("valid") else 1


if __name__ == "__main__":
    raise SystemExit(main())
