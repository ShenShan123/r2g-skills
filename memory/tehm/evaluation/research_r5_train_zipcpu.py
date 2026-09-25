"""Evaluator-only, researcher-assisted R5 TRAIN replay for ZipCPU payload.

This re-registers an observed DEV case as TRAIN.  It is not an unseen target,
native-oracle success, a verified TEHM transition, or Memory authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from . import research_r5_zipcpu_augmented_probe as oracle
from . import research_r5_zipcpu_skid_dev_probe as native
from .research_r5_skid_binding_v3 import (
    TEMPLATE, ZIPCPU_CONTEXT, apply_bound_skid_payload_v3,
    bind_skid_payload_v3,
)


SCHEMA = "tehm-r5-zipcpu-reused-dev-train-v1"
ROLE = "TRAIN_REUSED_DEV_RESEARCHER_ASSISTED"
CORPUS = Path("/data1/zhangdy/RTL/RTL_testbench")
PRIVATE_TB = (CORPUS / "_qualification" /
              "r5-zipcpu-augmented-oracle-input-v1" / "tb_skidbuffer_payload.sv")
SOURCE_SHA = "sha256:ed1fda9192563fd7fd7033b564e8cf47ad6cb63ba4f578ed2c302037d47a5389"
TB_SHA = "sha256:ed5e2162902b128114e37bc29e8c540218809601b0ab6b150de1b69af6f7f745"
HEAD = "2e8d3bc2d26ddc33d1881022a2a2b9d3f0c16b9b"
SCENARIOS = oracle.SCENARIOS
ARMS = ("clean", "fault", "candidate")


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _json(data: object) -> bytes:
    return (json.dumps(data, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode()


def _ref(path: Path) -> dict:
    data = path.read_bytes()
    return {"path": str(path.resolve(strict=True)), "sha256": _sha(data),
            "bytes": len(data)}


def _inputs() -> tuple[bytes, bytes]:
    repo = CORPUS / native.REPO
    if native._git(repo, "rev-parse", "HEAD") != HEAD or native._git(repo, "status", "--porcelain=v1"):
        raise ValueError("ZipCPU checkout HEAD or clean state drift")
    source = (repo / native.SOURCE).read_bytes()
    tb = PRIVATE_TB.read_bytes()
    if _sha(source) != SOURCE_SHA or _sha(tb) != TB_SHA:
        raise ValueError("frozen source or private oracle drift")
    return source, tb


def _code_locks() -> dict:
    from . import research_r5_skid_binding as v1
    from . import research_r5_skid_binding_v2 as v2
    from . import research_r5_skid_binding_v3 as v3
    from tehm.rtl import verilog_parse
    return {module.__name__: _sha(Path(module.__file__).read_bytes())
            for module in (v1, v2, v3, verilog_parse, oracle, native)}


def _root(work: Path) -> Path:
    root = work.resolve(strict=False)
    if root.parent != CORPUS / "_r5_pilot" / "training" or root.is_symlink():
        raise ValueError("TRAIN work must be a direct non-link child of _r5_pilot/training")
    return root


def prepare(work: Path) -> dict:
    root = _root(work)
    if root.exists():
        raise ValueError("refusing to overwrite TRAIN campaign")
    source, tb = _inputs()
    manifest = {
        "schema": SCHEMA, "role": ROLE, "dev_reuse_disclosed": True,
        "unseen_transfer": False, "production_authority": False,
        "source_repository": native.REPO, "source_git_sha": HEAD,
        "source_file": native.SOURCE, "source_sha256": _sha(source),
        "private_testbench": str(PRIVATE_TB), "private_testbench_sha256": _sha(tb),
        "oracle_kind": "research_augmented_evaluator_private_not_native",
        "native_payload_oracle": "MISSED",
        "public_context": ZIPCPU_CONTEXT,
        "fault_before": native.OLD.decode(), "fault_after": native.NEW.decode(),
        "fault_line": native.FAULT_LINE,
        "candidate_method": "frozen_v3_source_only_researcher_assisted",
        "target_obligation": "backpressure_payload_order_five_beats",
        "preservation_obligation": "direct_payload_order_five_beats",
        "expected_clean": {label: "PASS" for label in SCENARIOS},
        "expected_fault": {"direct": "PASS", "backpressure": "FAIL"},
        "expected_candidate": {label: "PASS" for label in SCENARIOS},
        "seed": "deterministic_five_beat_schedule",
        "timeout_seconds_compile": 30, "timeout_seconds_run": 20,
        "tools": oracle._tools(), "code_locks": _code_locks(),
        "visibility": {"binder": ["buggy_rtl", "public_context", "draft_template"],
                       "agent_stage": ["buggy_rtl"],
                       "evaluator_private": ["upstream_clean", "fault_metadata",
                                             "testbench", "expected_verdicts"]},
    }
    root.mkdir()
    (root / "preregistration.json").write_bytes(_json(manifest))
    (root / "preregistration.sha256").write_text(
        _sha(_json(manifest)) + "\n", encoding="ascii")
    return {"prepared": True, "work": str(root),
            "preregistration_sha256": _sha(_json(manifest))}


def _prereg(root: Path) -> dict:
    path = root / "preregistration.json"
    data = path.read_bytes()
    manifest = json.loads(data)
    if ((root / "preregistration.sha256").read_text(encoding="ascii").strip() != _sha(data) or
            manifest.get("schema") != SCHEMA or manifest.get("role") != ROLE or
            manifest.get("source_git_sha") != HEAD or
            manifest.get("source_sha256") != SOURCE_SHA or
            manifest.get("private_testbench_sha256") != TB_SHA or
            manifest.get("public_context") != ZIPCPU_CONTEXT or
            manifest.get("fault_before") != native.OLD.decode() or
            manifest.get("fault_after") != native.NEW.decode() or
            manifest.get("fault_line") != native.FAULT_LINE or
            manifest.get("tools") != oracle._tools() or
            manifest.get("code_locks") != _code_locks()):
        raise ValueError("TRAIN preregistration or runtime drift")
    return manifest


def run(work: Path) -> dict:
    root = _root(work).resolve(strict=True)
    prereg = _prereg(root)
    source, tb = _inputs()
    fault = native._fault(source)
    # The binder receives only the agent-visible faulty RTL and public knobs.
    binding = bind_skid_payload_v3({"binding_template": TEMPLATE},
                                   fault.decode("utf-8"), ZIPCPU_CONTEXT)
    if binding.get("status") != "BOUND":
        raise ValueError("frozen v3 source-only binder did not bind")
    candidate, action = apply_bound_skid_payload_v3(
        {"binding_template": TEMPLATE}, fault.decode("utf-8"),
        ZIPCPU_CONTEXT, binding)
    if any((root / name).exists() for name in ARMS) or (root / "receipt.json").exists():
        raise ValueError("TRAIN execution already exists; no in-place rerun")
    agent = root / "agent-inputs"
    agent.mkdir()
    (agent / "skidbuffer.v").write_bytes(fault)
    (root / "binding.json").write_bytes(_json({"binding": binding, "action": action}))
    for name, data in (("clean", source), ("fault", fault),
                       ("candidate", candidate.encode("utf-8"))):
        for label, scenario in SCENARIOS.items():
            arm = root / name / label
            stage = arm / "stage"
            (stage / "rtl").mkdir(parents=True)
            (stage / "tb").mkdir()
            (arm / "build").mkdir()
            (stage / native.SOURCE).write_bytes(data)
            (stage / "tb" / PRIVATE_TB.name).write_bytes(tb)
            compile_cmd, run_cmd = oracle._cmd(arm, scenario=scenario)
            (arm / "commands.json").write_bytes(_json({"compile": compile_cmd,
                                                        "run": run_cmd}))
            rc, output = native._run(compile_cmd, cwd=stage,
                                     timeout=prereg["timeout_seconds_compile"])
            (arm / "compile.log").write_bytes(output)
            (arm / "compile.exit").write_text(str(rc) + "\n", encoding="ascii")
            if rc == 0:
                rc, output = native._run(run_cmd, cwd=stage,
                                         timeout=prereg["timeout_seconds_run"])
            else:
                rc, output = 125, b"run_skipped_due_to_compile_failure\n"
            (arm / "run.log").write_bytes(output)
            (arm / "run.exit").write_text(str(rc) + "\n", encoding="ascii")
    result = verify(root)
    (root / "receipt.json").write_bytes(_json(result))
    return result


def verify(work: Path) -> dict:
    root = _root(work).resolve(strict=True)
    prereg = _prereg(root)
    source, tb = _inputs()
    fault = native._fault(source)
    errors: list[str] = []
    if (root / "agent-inputs" / "skidbuffer.v").read_bytes() != fault:
        errors.append("agent_input_not_fault")
    if set(path.name for path in (root / "agent-inputs").iterdir()) != {"skidbuffer.v"}:
        errors.append("agent_input_excess_files")
    if any(path.is_symlink() or path.name == ".git"
           for path in (root / "agent-inputs").rglob("*")):
        errors.append("agent_input_link_or_git")
    binding_data = json.loads((root / "binding.json").read_text(encoding="utf-8"))
    binding = bind_skid_payload_v3({"binding_template": TEMPLATE},
                                   fault.decode("utf-8"), ZIPCPU_CONTEXT)
    candidate, action = apply_bound_skid_payload_v3(
        {"binding_template": TEMPLATE}, fault.decode("utf-8"),
        ZIPCPU_CONTEXT, binding)
    if binding_data != {"binding": binding, "action": action}:
        errors.append("binding_or_action_drift")
    expected = {"clean": source, "fault": fault,
                "candidate": candidate.encode("utf-8")}
    arms = {}
    for name, data in expected.items():
        for label, scenario in SCENARIOS.items():
            key = name + "/" + label
            arm = root / name / label
            stage = arm / "stage"
            try:
                if ((stage / native.SOURCE).read_bytes() != data or
                        (stage / "tb" / PRIVATE_TB.name).read_bytes() != tb or
                        any(path.is_symlink() or path.name == ".git"
                            for path in stage.rglob("*"))):
                    errors.append(key + ":stage_drift")
                compile_cmd, run_cmd = oracle._cmd(arm, scenario=scenario)
                if json.loads((arm / "commands.json").read_text(encoding="utf-8")) != {
                        "compile": compile_cmd, "run": run_cmd}:
                    errors.append(key + ":command_drift")
                image = arm / "build" / "sim.vvp"
                if str(stage / native.SOURCE).encode() not in image.read_bytes():
                    errors.append(key + ":compiled_source_not_staged")
                verdict = oracle._classify(arm, scenario)
                if verdict["verdict"] != prereg["expected_" + name][label]:
                    errors.append(key + ":unexpected_verdict")
                arms[key] = {"verdict": verdict, "artifacts": {
                    item: _ref(path) for item, path in (
                        ("staged_source", stage / native.SOURCE),
                        ("private_test", stage / "tb" / PRIVATE_TB.name),
                        ("compiled_image", image),
                        ("commands", arm / "commands.json"),
                        ("compile_log", arm / "compile.log"),
                        ("compile_exit", arm / "compile.exit"),
                        ("run_log", arm / "run.log"),
                        ("run_exit", arm / "run.exit"))}}
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                errors.append(key + ":artifact_unavailable:" + type(exc).__name__)
    if expected["candidate"] != source:
        errors.append("private_candidate_clean_mismatch")
    result = {
        "schema": SCHEMA, "role": ROLE, "valid": not errors,
        "errors": errors, "training_transition": "researcher_assisted_not_core_admitted",
        "native_payload_oracle": "MISSED", "transfer_status": "NOT_RUN",
        "memory_status": "M_MINUS_ONLY", "source_lineage_status": "candidate_group_not_verified",
        "preregistration": _ref(root / "preregistration.json"),
        "binding": _ref(root / "binding.json"),
        "agent_input": _ref(root / "agent-inputs" / "skidbuffer.v"),
        "arms": arms,
    }
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
