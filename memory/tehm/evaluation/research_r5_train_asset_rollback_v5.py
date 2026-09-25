"""Fresh v5 TRAIN source rollback with target and preservation oracle replay.

The original TRAIN task sources are reused as explicitly researcher-assisted
TRAIN, but no v4 rollback receipt or v4 Asset authority is imported. This is
not Mremove, held-out transfer, or production promotion.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tehm.adapters import research_r5_rtl_scoped_v3 as train
from tehm.evaluation import research_r5_train_axis as axis
from tehm.evaluation import research_r5_train_zipcpu as zipcpu
from tehm.evaluation import research_r5_zipcpu_augmented_probe as zip_oracle
from tehm.evaluation import research_r5_zipcpu_skid_dev_probe as zip_native
from tehm.evaluation import research_r5_train_asset_rollback as runner
from tehm.rtl.skid_payload_action_v5 import (
    apply_skid_payload_action_v5, payload_from_source_v5,
)

SCHEMA = "tehm-r5-train-asset-source-rollback-v5-r2"
ROLE = "TRAIN_REUSED_DEV_V5_ASSET_ROLLBACK_NOT_MREMOVE"
ROOT = runner.ROOT
CASE_ORDER = ("axis_register", "zipcpu_skidbuffer")


def _inputs() -> dict:
    checked = {}
    for case in CASE_ORDER:
        item = train.verify_acquisition(train.acquisition(case, "treatment"))
        record = train.build_record(train.acquisition(case, "treatment"))
        if (train.replay_record(record) !=
                record.verification["scoped_execution"]["pair_receipt"]):
            raise ValueError(case + ":canonical TRAIN replay failed")
        fault, candidate, test = runner._source_paths(case, item)
        if (any(path.is_symlink() or not path.is_file()
                for path in (fault, candidate, test)) or
                runner._sha(fault.read_bytes()) != item["before_source_sha256"] or
                runner._sha(candidate.read_bytes()) != item["after_source_sha256"]):
            raise ValueError(case + ":pinned TRAIN source/test drift")
        source = train._source(item)
        payload = payload_from_source_v5(source, item["public_context"])
        edited, action = apply_skid_payload_action_v5(source, payload)
        if (runner._sha(edited.encode()) != item["after_source_sha256"] or
                action.get("rewritten") != 1 or
                action.get("source_binding_rederived") is not True):
            raise ValueError(case + ":v5 action differs from TRAIN candidate")
        checked[case] = {"item": item, "test_sha256": runner._sha(test.read_bytes()),
                         "record_id": record.record_id,
                         "witness_digest": record.verification["scoped_execution"][
                             "oracle_instance_witness_digest"]}
    return checked


def _manifest() -> dict:
    checked = _inputs()
    return {
        "schema": SCHEMA, "role": ROLE, "unseen_transfer": False,
        "asset_authority": False, "memory_mremove": False,
        "runner_sha256": runner._sha(Path(__file__).read_bytes()),
        "shared_contract_digest": train._digest(train.MEASUREMENT_CONTRACT),
        "cases": {case: {
            "repository": data["item"]["repository"],
            "source_git_sha": data["item"]["source_git_sha"],
            "source_file": data["item"]["source_file"],
            "public_context": data["item"]["public_context"],
            "before_source_sha256": data["item"]["before_source_sha256"],
            "after_source_sha256": data["item"]["after_source_sha256"],
            "train_receipt_digest": data["item"]["train_receipt_digest"],
            "test_sha256": data["test_sha256"],
            "record_id": data["record_id"],
            "oracle_instance_witness_digest": data["witness_digest"],
        } for case, data in checked.items()},
        "expected_restored": {case: {"target": "FAIL", "preservation": "PASS"}
                              for case in CASE_ORDER},
    }


def prepare(work: Path) -> dict:
    root = runner._root(work)
    if root.exists():
        raise ValueError("refusing to overwrite v5 rollback campaign")
    manifest = _manifest()
    root.mkdir()
    (root / "preregistration.json").write_bytes(runner._json(manifest))
    return {"prepared": True, "work": str(root),
            "preregistration_sha256": runner._sha(runner._json(manifest))}


def _prereg(root: Path) -> dict:
    saved = json.loads((root / "preregistration.json").read_bytes())
    if saved != _manifest():
        raise ValueError("v5 rollback preregistration or input drift")
    return saved


def run(work: Path) -> dict:
    root = runner._root(work).resolve(strict=True)
    _prereg(root)
    checked = _inputs()
    if any((root / case).exists() for case in CASE_ORDER) or (root / "receipt.json").exists():
        raise ValueError("v5 rollback already run; no in-place rerun")
    for case in CASE_ORDER:
        if case == "axis_register":
            arm = root / case
            arm.mkdir()
            runner._stage_source(arm, case, checked[case]["item"])
            runner._run_axis(arm)
        else:
            for scenario in zipcpu.SCENARIOS:
                arm = root / case / scenario
                arm.mkdir(parents=True)
                (arm / "build").mkdir()
                runner._stage_source(arm, case, checked[case]["item"])
                runner._run_zip(arm, scenario)
    receipt = verify(root)
    (root / "receipt.json").write_bytes(runner._json(receipt))
    return receipt


def _verify_stage(arm: Path, case: str, checked: dict) -> dict:
    item = checked["item"]
    fault, candidate, test = runner._source_paths(case, item)
    source = arm / "stage" / item["source_file"]
    backup = arm / "backup" / item["source_file"]
    staged_test = (arm / "stage" / axis.TEST if case == "axis_register"
                   else arm / "stage" / "tb" / zipcpu.PRIVATE_TB.name)
    if (source.read_bytes() != backup.read_bytes() or
            source.read_bytes() != fault.read_bytes() or
            (arm / "candidate-before-rollback.v").read_bytes() != candidate.read_bytes() or
            staged_test.read_bytes() != test.read_bytes() or
            runner._sha(staged_test.read_bytes()) != checked["test_sha256"] or
            any(path.is_symlink() or path.name == ".git" for path in arm.rglob("*"))):
        raise ValueError(case + ":v5 restored source/test or isolation drift")
    before = source.read_text(encoding="utf-8")
    payload = payload_from_source_v5(before, item["public_context"])
    repaired, action = apply_skid_payload_action_v5(before, payload)
    if (runner._sha(repaired.encode()) != item["after_source_sha256"] or
            action.get("rewritten") != 1 or
            action.get("source_binding_rederived") is not True):
        raise ValueError(case + ":v5 candidate regeneration drift")
    return {"restored_source": runner._ref(source),
            "backup_source": runner._ref(backup),
            "candidate_before_rollback": runner._ref(arm / "candidate-before-rollback.v"),
            "staged_test": runner._ref(staged_test)}


def verify(work: Path) -> dict:
    root = runner._root(work).resolve(strict=True)
    _prereg(root)
    checked = _inputs()
    arms = {}
    for case in CASE_ORDER:
        if case == "axis_register":
            arm = root / case
            files = _verify_stage(arm, case, checked[case])
            command = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
                       "--show-capture=no", "-s", "-o", "log_cli=true",
                       "-o", "log_cli_level=INFO", "-q", f"{axis.TEST}::{axis.NODE}"]
            if json.loads((arm / "command.json").read_bytes()) != command:
                raise ValueError("v5 axis rollback command drift")
            result = axis._assess(arm)
            cases = result["cases"]
            if (result["verdict"]["verdict"] != "FAIL" or
                    any(cases[name] != "FAIL" for name in axis.TARGET_IDS) or
                    any(cases[name] != "PASS" for name in axis.PRESERVATION_IDS)):
                raise ValueError("v5 axis rollback oracle mismatch")
            arms[case] = {"target": "FAIL", "preservation": "PASS",
                          "oracle": result, "rollback_files": files,
                          "command": runner._ref(arm / "command.json")}
        else:
            scenarios = {}
            for scenario in zipcpu.SCENARIOS:
                arm = root / case / scenario
                files = _verify_stage(arm, case, checked[case])
                compile_cmd, run_cmd = zip_oracle._cmd(
                    arm, scenario=zipcpu.SCENARIOS[scenario])
                if json.loads((arm / "commands.json").read_bytes()) != {
                        "compile": compile_cmd, "run": run_cmd}:
                    raise ValueError("v5 ZipCPU rollback command drift")
                image = arm / "build" / "sim.vvp"
                if str(arm / "stage" / zip_native.SOURCE).encode() not in image.read_bytes():
                    raise ValueError("v5 ZipCPU rollback compiled a different source")
                verdict = zip_oracle._classify(arm, zipcpu.SCENARIOS[scenario])
                if verdict["verdict"] != ("PASS" if scenario == "direct" else "FAIL"):
                    raise ValueError("v5 ZipCPU rollback oracle mismatch")
                scenarios[scenario] = {"verdict": verdict, "rollback_files": files,
                                       "compiled_image": runner._ref(image),
                                       "commands": runner._ref(arm / "commands.json"),
                                       "compile_log": runner._ref(arm / "compile.log"),
                                       "compile_exit": runner._ref(arm / "compile.exit"),
                                       "run_log": runner._ref(arm / "run.log"),
                                       "run_exit": runner._ref(arm / "run.exit")}
            arms[case] = {"target": "FAIL", "preservation": "PASS",
                          "oracle": scenarios}
    result = {"schema": SCHEMA, "role": ROLE, "valid": True,
              "preregistration": runner._ref(root / "preregistration.json"),
              "arms": arms, "rollback_source_verified": True,
              "fresh_oracle_execution": True, "memory_mremove": False,
              "asset_authority_recorded": False, "heldout_transfer": False}
    result["digest"] = runner._sha(runner._json(result))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "run", "verify"):
        sub.add_parser(command).add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    result = globals()[args.command](args.work)
    if args.command == "verify" and result != json.loads((args.work / "receipt.json").read_bytes()):
        raise ValueError("v5 rollback receipt does not cold replay")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
