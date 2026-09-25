"""Fresh generation-2 v4 TRAIN Asset source rollback, never Mremove.

Stage the v4 candidate, restore the pinned faulted source from a backup,
then run each TRAIN evaluator's target and preservation oracle on the restored
source. The v1 runner supplies isolated compiler/test commands only; this
module owns new preregistration, v4 candidate replay and result verification.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tehm.adapters import research_r5_rtl_scoped_v2 as train
from tehm.evaluation import research_r5_train_axis as axis
from tehm.evaluation import research_r5_train_zipcpu as zipcpu
from tehm.evaluation import research_r5_zipcpu_augmented_probe as zip_oracle
from tehm.evaluation import research_r5_zipcpu_skid_dev_probe as zip_native
from tehm.evaluation import research_r5_train_asset_rollback as v1
from tehm.rtl.skid_payload_action_v4 import apply_skid_payload_action_v4, payload_from_source_v4


SCHEMA = "tehm-r5-train-asset-source-rollback-v2"
ROLE = "TRAIN_REUSED_DEV_GEN2_ASSET_ROLLBACK_NOT_MREMOVE"
ROOT = v1.ROOT
PREFLIGHT = axis.CORPUS / "_r5_pilot/memory/asset-validation-v4-preflight-r1.json"
PREFLIGHT_SHA = "sha256:edc36aa40f5e390fc8652e540f2dc53b88f6440e8df9ad3d3d1ea3ccd681362e"


def _inputs() -> dict:
    if v1._sha(PREFLIGHT.read_bytes()) != PREFLIGHT_SHA:
        raise ValueError("generation-2 TRAIN Asset preflight drift")
    preflight = json.loads(PREFLIGHT.read_bytes())
    if (preflight.get("valid") is not True or
            preflight.get("role") != "TRAIN_REUSED_DEV_GEN2_ASSET_PREFLIGHT_NOT_AUTHORITY" or
            preflight.get("authority_recorded") is not False or
            preflight.get("gate_missing") != ["cross_lineage_verified", "rollback_verified"]):
        raise ValueError("generation-2 TRAIN Asset preflight role drift")
    checked = {case: train.verify_acquisition(train.acquisition(case, "treatment"))
               for case in train.CASES}
    for case, item in checked.items():
        fault, candidate, test = v1._source_paths(case, item)
        if (v1._sha(fault.read_bytes()) != item["before_source_sha256"] or
                v1._sha(candidate.read_bytes()) != item["after_source_sha256"] or
                not test.is_file() or test.is_symlink() or
                preflight["sources"][case]["train_receipt_digest"] !=
                item["train_receipt_digest"]):
            raise ValueError(case + ":TRAIN source or evaluator drift")
    return checked


def _manifest() -> dict:
    checked = _inputs()
    preflight = json.loads(PREFLIGHT.read_bytes())
    return {
        "schema": SCHEMA, "role": ROLE, "unseen_transfer": False,
        "asset_authority": False, "memory_mremove": False,
        "preflight_sha256": PREFLIGHT_SHA,
        "runner_sha256": v1._sha(Path(__file__).read_bytes()),
        "shared_contract_digest": train._digest(train.MEASUREMENT_CONTRACT),
        "cases": {case: {
            "repository": item["repository"],
            "source_git_sha": item["source_git_sha"],
            "source_file": item["source_file"],
            "public_context": item["public_context"],
            "before_source_sha256": item["before_source_sha256"],
            "after_source_sha256": item["after_source_sha256"],
            "train_receipt_digest": item["train_receipt_digest"],
            "oracle_instance_witness_digest": preflight["sources"][case][
                "oracle_instance_witness_digest"],
        } for case, item in checked.items()},
        "expected_restored": {case: {"target": "FAIL", "preservation": "PASS"}
                              for case in train.CASES},
    }


def prepare(work: Path) -> dict:
    root = v1._root(work)
    if root.exists():
        raise ValueError("refusing to overwrite generation-2 rollback campaign")
    manifest = _manifest()
    root.mkdir()
    (root / "preregistration.json").write_bytes(v1._json(manifest))
    return {"prepared": True, "work": str(root),
            "preregistration_sha256": v1._sha(v1._json(manifest))}


def _prereg(root: Path) -> dict:
    saved = json.loads((root / "preregistration.json").read_bytes())
    if saved != _manifest():
        raise ValueError("generation-2 rollback preregistration or input drift")
    return saved


def run(work: Path) -> dict:
    root = v1._root(work).resolve(strict=True)
    _prereg(root)
    checked = _inputs()
    if any((root / case).exists() for case in train.CASES) or (root / "receipt.json").exists():
        raise ValueError("generation-2 rollback already run; no in-place rerun")
    for case in train.CASES:
        if case == "axis_register":
            arm = root / case
            arm.mkdir()
            v1._stage_source(arm, case, checked[case])
            v1._run_axis(arm)
        else:
            for scenario in zipcpu.SCENARIOS:
                arm = root / case / scenario
                arm.mkdir(parents=True)
                (arm / "build").mkdir()
                v1._stage_source(arm, case, checked[case])
                v1._run_zip(arm, scenario)
    receipt = verify(root)
    (root / "receipt.json").write_bytes(v1._json(receipt))
    return receipt


def _verify_stage(arm: Path, case: str, checked: dict) -> dict:
    fault, candidate, test = v1._source_paths(case, checked)
    stage = arm / "stage"
    source = stage / checked["source_file"]
    backup = arm / "backup" / checked["source_file"]
    staged_test = (stage / axis.TEST if case == "axis_register"
                   else stage / "tb" / zipcpu.PRIVATE_TB.name)
    if (source.read_bytes() != backup.read_bytes() or
            source.read_bytes() != fault.read_bytes() or
            (arm / "candidate-before-rollback.v").read_bytes() != candidate.read_bytes() or
            staged_test.read_bytes() != test.read_bytes() or
            any(path.is_symlink() or path.name == ".git" for path in arm.rglob("*"))):
        raise ValueError(case + ":restored source/test drift")
    before = source.read_text(encoding="utf-8")
    payload = payload_from_source_v4(before, checked["public_context"])
    repaired, action = apply_skid_payload_action_v4(before, payload)
    if (v1._sha(repaired.encode()) != checked["after_source_sha256"] or
            action.get("rewritten") != 1 or
            action.get("source_binding_rederived") is not True):
        raise ValueError(case + ":v4 candidate regeneration drift")
    return {"restored_source": v1._ref(source), "backup_source": v1._ref(backup),
            "candidate_before_rollback": v1._ref(arm / "candidate-before-rollback.v"),
            "staged_test": v1._ref(staged_test)}


def verify(work: Path) -> dict:
    root = v1._root(work).resolve(strict=True)
    _prereg(root)
    checked = _inputs()
    arms = {}
    for case in train.CASES:
        if case == "axis_register":
            arm = root / case
            files = _verify_stage(arm, case, checked[case])
            command = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
                       "--show-capture=no", "-s", "-o", "log_cli=true",
                       "-o", "log_cli_level=INFO", "-q", f"{axis.TEST}::{axis.NODE}"]
            if json.loads((arm / "command.json").read_bytes()) != command:
                raise ValueError("axis rollback command drift")
            result = axis._assess(arm)
            cases = result["cases"]
            if (result["verdict"]["verdict"] != "FAIL" or
                    any(cases[item] != "FAIL" for item in axis.TARGET_IDS) or
                    any(cases[item] != "PASS" for item in axis.PRESERVATION_IDS)):
                raise ValueError("axis rollback target/preservation mismatch")
            arms[case] = {"target": "FAIL", "preservation": "PASS",
                          "oracle": result, "rollback_files": files,
                          "command": v1._ref(arm / "command.json")}
        else:
            scenarios = {}
            for scenario in zipcpu.SCENARIOS:
                arm = root / case / scenario
                files = _verify_stage(arm, case, checked[case])
                compile_cmd, run_cmd = zip_oracle._cmd(
                    arm, scenario=zipcpu.SCENARIOS[scenario])
                if json.loads((arm / "commands.json").read_bytes()) != {
                        "compile": compile_cmd, "run": run_cmd}:
                    raise ValueError("ZipCPU rollback command drift")
                image = arm / "build" / "sim.vvp"
                if str(arm / "stage" / zip_native.SOURCE).encode() not in image.read_bytes():
                    raise ValueError("ZipCPU rollback did not compile restored source")
                verdict = zip_oracle._classify(arm, zipcpu.SCENARIOS[scenario])
                expected = "PASS" if scenario == "direct" else "FAIL"
                if verdict["verdict"] != expected:
                    raise ValueError("ZipCPU rollback target/preservation mismatch")
                scenarios[scenario] = {
                    "verdict": verdict, "rollback_files": files,
                    "compiled_image": v1._ref(image),
                    "commands": v1._ref(arm / "commands.json"),
                    "compile_log": v1._ref(arm / "compile.log"),
                    "compile_exit": v1._ref(arm / "compile.exit"),
                    "run_log": v1._ref(arm / "run.log"),
                    "run_exit": v1._ref(arm / "run.exit"),
                }
            arms[case] = {"target": "FAIL", "preservation": "PASS",
                          "oracle": scenarios}
    result = {"schema": SCHEMA, "role": ROLE, "valid": True,
              "preregistration": v1._ref(root / "preregistration.json"),
              "asset_preflight": v1._ref(PREFLIGHT), "arms": arms,
              "rollback_source_verified": True, "fresh_oracle_execution": True,
              "memory_mremove": False, "asset_authority_recorded": False,
              "heldout_transfer": False}
    result["digest"] = v1._sha(v1._json(result))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "run", "verify"):
        sub.add_parser(command).add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    result = globals()[args.command](args.work)
    if args.command == "verify" and result != json.loads((args.work / "receipt.json").read_bytes()):
        raise ValueError("generation-2 rollback receipt does not cold replay")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
