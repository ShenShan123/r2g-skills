"""Fresh evaluator-only R5 TRAIN Asset source-rollback rehearsal.

Restores the pre-action faulted RTL from a pinned backup after constructing
the v3 candidate, then executes both source-specific TRAIN oracle scopes on
the restored source. This is not Mremove, target transfer, or Asset authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from tehm.adapters.research_r5_rtl_scoped import (
    CASES, acquisition, verify_acquisition,
)
from tehm.rtl.skid_payload_action_v3 import (
    apply_skid_payload_action_v3, payload_from_source_v3,
)
from . import research_r5_train_axis as axis
from . import research_r5_train_zipcpu as zipcpu
from . import research_r5_zipcpu_augmented_probe as zip_oracle
from . import research_r5_zipcpu_skid_dev_probe as zip_native


SCHEMA = "tehm-r5-train-asset-source-rollback-v1"
ROLE = "TRAIN_REUSED_DEV_ASSET_ROLLBACK_REHEARSAL"
ROOT = axis.CORPUS / "_r5_pilot" / "training"
ASSET_PREFLIGHT = axis.CORPUS / "_r5_pilot" / "memory" / "asset-validation-preflight-r1.json"
ASSET_PREFLIGHT_SHA = "sha256:c83a83e5caf3ec00980a4bdbc7b055315d08227a659e6646e8f4265709832379"


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":")) + "\n").encode()


def _ref(path: Path) -> dict:
    data = path.read_bytes()
    return {"path": str(path.resolve(strict=True)), "sha256": _sha(data),
            "bytes": len(data)}


def _root(work: Path) -> Path:
    root = work.resolve(strict=False)
    if root.parent != ROOT or root.is_symlink():
        raise ValueError("rollback work must be a direct non-link TRAIN child")
    return root


def _source_paths(case: str, checked: dict) -> tuple[Path, Path, Path]:
    work = Path(checked["work"])
    source = checked["source_file"]
    if case == "axis_register":
        return (work / "fault" / "stage" / source,
                work / "candidate" / "stage" / source,
                work / "fault" / "stage" / axis.TEST)
    return (work / "fault" / "backpressure" / "stage" / source,
            work / "candidate" / "backpressure" / "stage" / source,
            work / "fault" / "backpressure" / "stage" / "tb" / zipcpu.PRIVATE_TB.name)


def _inputs() -> dict:
    if _sha(ASSET_PREFLIGHT.read_bytes()) != ASSET_PREFLIGHT_SHA:
        raise ValueError("TRAIN Asset validation preflight receipt drift")
    preflight = json.loads(ASSET_PREFLIGHT.read_bytes())
    if preflight.get("valid") is not True or preflight.get("authority_recorded") is not False:
        raise ValueError("TRAIN Asset validation preflight role drift")
    checked = {case: verify_acquisition(acquisition(case, "treatment"))
               for case in CASES}
    for case, item in checked.items():
        fault, candidate, test = _source_paths(case, item)
        before, after = fault.read_bytes(), candidate.read_bytes()
        if (_sha(before) != item["before_source_sha256"] or
                _sha(after) != item["after_source_sha256"] or
                preflight["sources"][case]["before_source_sha256"] != _sha(before) or
                preflight["sources"][case]["after_source_sha256"] != _sha(after) or
                not test.is_file()):
            raise ValueError(case + ":TRAIN source/test or Asset preflight drift")
    return checked


def _manifest() -> dict:
    checked = _inputs()
    return {
        "schema": SCHEMA, "role": ROLE, "unseen_transfer": False,
        "asset_authority": False, "memory_mremove": False,
        "asset_preflight_sha256": ASSET_PREFLIGHT_SHA,
        "runner_sha256": _sha(Path(__file__).read_bytes()),
        "cases": {case: {
            "repository": item["repository"], "source_git_sha": item["source_git_sha"],
            "source_file": item["source_file"], "public_context": item["public_context"],
            "before_source_sha256": item["before_source_sha256"],
            "after_source_sha256": item["after_source_sha256"],
            "train_receipt_digest": item["train_receipt_digest"],
            "target_obligation": item["target_obligation"],
            "preservation_obligation": item["preservation_obligation"],
        } for case, item in checked.items()},
        "expected_restored": {
            "axis_register": {"target": "FAIL", "preservation": "PASS"},
            "zipcpu_skidbuffer": {"target": "FAIL", "preservation": "PASS"},
        },
    }


def prepare(work: Path) -> dict:
    root = _root(work)
    if root.exists():
        raise ValueError("refusing to overwrite rollback campaign")
    manifest = _manifest()
    root.mkdir()
    (root / "preregistration.json").write_bytes(_json(manifest))
    return {"prepared": True, "work": str(root),
            "preregistration_sha256": _sha(_json(manifest))}


def _prereg(root: Path) -> dict:
    saved = json.loads((root / "preregistration.json").read_bytes())
    if saved != _manifest():
        raise ValueError("rollback preregistration or code/input lock drift")
    return saved


def _stage_source(arm: Path, case: str, checked: dict) -> tuple[Path, bytes]:
    fault, candidate, test = _source_paths(case, checked)
    before, after = fault.read_bytes(), candidate.read_bytes()
    source = arm / "stage" / checked["source_file"]
    source.parent.mkdir(parents=True)
    (arm / "backup" / checked["source_file"]).parent.mkdir(parents=True)
    backup = arm / "backup" / checked["source_file"]
    backup.write_bytes(before)
    (arm / "candidate-before-rollback.v").write_bytes(after)
    source.write_bytes(after)
    if _sha(source.read_bytes()) != checked["after_source_sha256"]:
        raise ValueError("candidate staging failed")
    shutil.copyfile(backup, source)
    if _sha(source.read_bytes()) != checked["before_source_sha256"]:
        raise ValueError("source rollback failed")
    target_test = (arm / "stage" / axis.TEST if case == "axis_register"
                   else arm / "stage" / "tb" / zipcpu.PRIVATE_TB.name)
    target_test.parent.mkdir(parents=True)
    shutil.copyfile(test, target_test)
    return source, before


def _run_axis(arm: Path) -> None:
    env = {**os.environ, "PYTHONPATH": str(axis.PYDEPS),
           "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
           "RANDOM_SEED": axis.SEED}
    command = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
               "--show-capture=no", "-s", "-o", "log_cli=true",
               "-o", "log_cli_level=INFO", "-q", f"{axis.TEST}::{axis.NODE}"]
    (arm / "command.json").write_bytes(_json(command))
    try:
        result = subprocess.run(command, cwd=arm / "stage", env=env,
                                capture_output=True, timeout=180)
        code, output = result.returncode, result.stdout + result.stderr
    except subprocess.TimeoutExpired as exc:
        code, output = 124, (exc.stdout or b"") + (exc.stderr or b"") + b"\nTIMEOUT\n"
    (arm / "pytest.log").write_bytes(output)
    (arm / "pytest.exit").write_text(str(code) + "\n", encoding="ascii")


def _run_zip(arm: Path, scenario: str) -> None:
    compile_cmd, run_cmd = zip_oracle._cmd(arm, scenario=zipcpu.SCENARIOS[scenario])
    (arm / "commands.json").write_bytes(_json({"compile": compile_cmd,
                                                "run": run_cmd}))
    code, output = zip_native._run(compile_cmd, cwd=arm / "stage", timeout=30)
    (arm / "compile.log").write_bytes(output)
    (arm / "compile.exit").write_text(str(code) + "\n", encoding="ascii")
    if code == 0:
        code, output = zip_native._run(run_cmd, cwd=arm / "stage", timeout=20)
    else:
        code, output = 125, b"run_skipped_due_to_compile_failure\n"
    (arm / "run.log").write_bytes(output)
    (arm / "run.exit").write_text(str(code) + "\n", encoding="ascii")


def run(work: Path) -> dict:
    root = _root(work).resolve(strict=True)
    _prereg(root)
    checked = _inputs()
    if any((root / case).exists() for case in CASES) or (root / "receipt.json").exists():
        raise ValueError("rollback run already exists; no in-place rerun")
    for case in CASES:
        if case == "axis_register":
            arm = root / case
            arm.mkdir()
            _stage_source(arm, case, checked[case])
            _run_axis(arm)
        else:
            for scenario in zipcpu.SCENARIOS:
                arm = root / case / scenario
                arm.mkdir(parents=True)
                (arm / "build").mkdir()
                _stage_source(arm, case, checked[case])
                _run_zip(arm, scenario)
    receipt = verify(root)
    (root / "receipt.json").write_bytes(_json(receipt))
    return receipt


def _verify_stage(arm: Path, case: str, checked: dict) -> dict:
    fault, candidate, test = _source_paths(case, checked)
    stage = arm / "stage"
    source = stage / checked["source_file"]
    backup = arm / "backup" / checked["source_file"]
    staged_test = (stage / axis.TEST if case == "axis_register"
                   else stage / "tb" / zipcpu.PRIVATE_TB.name)
    if (not source.read_bytes() or
            source.read_bytes() != backup.read_bytes() or
            source.read_bytes() != fault.read_bytes() or
            (arm / "candidate-before-rollback.v").read_bytes() != candidate.read_bytes() or
            staged_test.read_bytes() != test.read_bytes() or
            any(path.is_symlink() or path.name == ".git" for path in arm.rglob("*"))):
        raise ValueError(case + ":restored stage or isolated test drift")
    before = source.read_text(encoding="utf-8")
    payload = payload_from_source_v3(before, checked["public_context"])
    repaired, action = apply_skid_payload_action_v3(before, payload)
    if (_sha(repaired.encode()) != checked["after_source_sha256"] or
            action.get("rewritten") != 1):
        raise ValueError(case + ":candidate regeneration drift")
    return {"restored_source": _ref(source), "backup_source": _ref(backup),
            "candidate_before_rollback": _ref(arm / "candidate-before-rollback.v"),
            "staged_test": _ref(staged_test)}


def verify(work: Path) -> dict:
    root = _root(work).resolve(strict=True)
    _prereg(root)
    checked = _inputs()
    arms = {}
    for case in CASES:
        if case == "axis_register":
            arm = root / case
            files = _verify_stage(arm, case, checked[case])
            expected_command = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
                                "--show-capture=no", "-s", "-o", "log_cli=true",
                                "-o", "log_cli_level=INFO", "-q", f"{axis.TEST}::{axis.NODE}"]
            if json.loads((arm / "command.json").read_bytes()) != expected_command:
                raise ValueError("axis rollback command drift")
            result = axis._assess(arm)
            cases = result["cases"]
            if (result["verdict"]["verdict"] != "FAIL" or
                    any(cases[item] != "FAIL" for item in axis.TARGET_IDS) or
                    any(cases[item] != "PASS" for item in axis.PRESERVATION_IDS)):
                raise ValueError("axis rollback target/preservation oracle mismatch")
            arms[case] = {"target": "FAIL", "preservation": "PASS",
                          "oracle": result, "rollback_files": files,
                          "command": _ref(arm / "command.json")}
        else:
            scenarios = {}
            for scenario in zipcpu.SCENARIOS:
                arm = root / case / scenario
                files = _verify_stage(arm, case, checked[case])
                compile_cmd, run_cmd = zip_oracle._cmd(
                    arm, scenario=zipcpu.SCENARIOS[scenario])
                if json.loads((arm / "commands.json").read_bytes()) != {
                        "compile": compile_cmd, "run": run_cmd}:
                    raise ValueError("ZipCPU rollback commands drift")
                image = arm / "build" / "sim.vvp"
                if str(arm / "stage" / zip_native.SOURCE).encode() not in image.read_bytes():
                    raise ValueError("ZipCPU rollback did not compile restored source")
                verdict = zip_oracle._classify(arm, zipcpu.SCENARIOS[scenario])
                expected = "PASS" if scenario == "direct" else "FAIL"
                if verdict["verdict"] != expected:
                    raise ValueError("ZipCPU rollback target/preservation oracle mismatch")
                scenarios[scenario] = {"verdict": verdict, "rollback_files": files,
                                       "compiled_image": _ref(image),
                                       "commands": _ref(arm / "commands.json"),
                                       "compile_log": _ref(arm / "compile.log"),
                                       "compile_exit": _ref(arm / "compile.exit"),
                                       "run_log": _ref(arm / "run.log"),
                                       "run_exit": _ref(arm / "run.exit")}
            arms[case] = {"target": "FAIL", "preservation": "PASS",
                          "oracle": scenarios}
    result = {"schema": SCHEMA, "role": ROLE, "valid": True,
              "preregistration": _ref(root / "preregistration.json"),
              "asset_preflight": _ref(ASSET_PREFLIGHT),
              "arms": arms, "rollback_source_verified": True,
              "fresh_oracle_execution": True, "memory_mremove": False,
              "asset_authority_recorded": False, "heldout_transfer": False}
    result["digest"] = _sha(_json(result))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "run", "verify"):
        sub.add_parser(command).add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    result = {"prepare": prepare, "run": run, "verify": verify}[args.command](args.work)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("prepared") or result.get("valid") else 1


if __name__ == "__main__":
    raise SystemExit(main())
