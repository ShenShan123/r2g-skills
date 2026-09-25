"""Fresh evaluator-only v7 rollback for the observed LibSV TRAIN source.

This verifies restoration and the original native/private fault oracles. It is
one component, not the full three-lineage v7 Asset rollback or Mremove.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

from tehm.evaluation import research_r5_skid_binding_v7 as binder
from tehm.rtl import rtl_actions
from tehm.rtl import skid_payload_action_v7 as action_v7

ROOT = Path("/data1/zhangdy/RTL/RTL_testbench/_r5_pilot")
WORK = ROOT / "training/asset-rollback-v7-l1"
TRAIN = ROOT / "training/skid-v7-r1"
QUALIFY_PATH = ROOT / "qualification/libsv-skid-oracle-v1/qualify.py"
PRIVATE = QUALIFY_PATH.parent / "evaluator-private"
SCHEMA = "r5-libsv-train-source-rollback-v7-r1"
ROLE = "TRAIN_REUSED_DEV_V7_LIBSV_SOURCE_ROLLBACK_NOT_MREMOVE"
FAULT_SHA = "1536de8a2e007048e9538f218b3eb83960c5328ac1daac828b360393a3939d0f"
CANDIDATE_SHA = "a5e2e2d3f753a43d6d643c3d030809e00198a6478dcfaf9225a47fa096df472a"
VERILATOR_SHA = "aa644932964f713353b9293ea0d390af282009ab6a882c31de9cf6627e29df9d"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_qualify():
    spec = importlib.util.spec_from_file_location("r5_libsv_rollback_qualify", QUALIFY_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":")) + "\n").encode()


def _manifest() -> dict:
    oracle = load_qualify()
    if sha(PRIVATE / "source/fault/skid_buffer.sv") != FAULT_SHA or sha(
            PRIVATE / "source/clean/skid_buffer.sv") != CANDIDATE_SHA:
        raise ValueError("LibSV qualified RTL drift")
    for relative, expected in oracle.EXPECTED.items():
        if sha(PRIVATE / relative) != expected:
            raise ValueError(f"qualified private input drift: {relative}")
    for relative, expected in oracle.NATIVE_INPUTS.items():
        if sha(oracle.UPSTREAM / relative) != expected:
            raise ValueError(f"native input drift: {relative}")
    if sha(oracle.VERILATOR) != VERILATOR_SHA:
        raise ValueError("Verilator tool drift")
    audit = json.loads((TRAIN / "audit-r1.json").read_bytes())
    if audit.get("raw_six_arm_valid") is not True or audit.get("heldout_transfer") is not False:
        raise ValueError("LibSV TRAIN audit role drift")
    source = (PRIVATE / "source/fault/skid_buffer.sv").read_text()
    payload = action_v7.payload_from_source_v7(source, binder.LIBSV_CONTEXT)
    candidate, receipt = action_v7.apply_skid_payload_action_v7(source, payload)
    if hashlib.sha256(candidate.encode()).hexdigest() != CANDIDATE_SHA or receipt["rewritten"] != 1:
        raise ValueError("v7 regenerated TRAIN candidate drift")
    code = {"rollback": Path(__file__), "binder": Path(binder.__file__),
            "action": Path(action_v7.__file__), "catalog": Path(rtl_actions.__file__),
            "qualifier": QUALIFY_PATH}
    return {"schema": SCHEMA, "role": ROLE, "observed_dev_reused_as_train": True,
            "heldout_transfer": False, "asset_authority_recorded": False,
            "memory_mremove": False, "public_context": binder.LIBSV_CONTEXT,
            "source_git_sha": "c5aff5decba04e7290db6babb122cc1a5d9460ed",
            "fault_sha256": FAULT_SHA, "candidate_sha256": CANDIDATE_SHA,
            "train_prereg_sha256": sha(TRAIN / "preregistration.md"),
            "train_receipt_sha256": sha(TRAIN / "fresh-r1/receipt.json"),
            "train_audit_sha256": sha(TRAIN / "audit-r1.json"),
            "qualified_inputs": oracle.EXPECTED,
            "native_inputs": oracle.NATIVE_INPUTS,
            "verilator_sha256": VERILATOR_SHA,
            "code_sha256": {name: sha(path) for name, path in code.items()},
            "expected_restored": {"native": "FAIL_POST_STALL_PAYLOAD",
                                  "target": "FAIL_SECOND_HANDSHAKE_PAYLOAD",
                                  "preservation": "PASS_THREE_DIRECT_TRANSFERS"}}


def prepare() -> dict:
    if WORK.exists() or WORK.is_symlink() or WORK.parent != ROOT / "training":
        raise ValueError("v7 LibSV rollback work already exists or escaped TRAIN root")
    manifest = _manifest()
    WORK.mkdir(mode=0o700)
    WORK.chmod(0o700)
    (WORK / "preregistration.json").write_bytes(canonical(manifest))
    return {"prepared": True, "preregistration_sha256": sha(WORK / "preregistration.json")}


def _prereg() -> dict:
    path = WORK / "preregistration.json"
    if WORK.is_symlink() or not path.is_file():
        raise ValueError("v7 LibSV rollback preregistration missing")
    saved = json.loads(path.read_bytes())
    if saved != _manifest():
        raise ValueError("v7 LibSV rollback software/input/role drift")
    return saved


def run() -> dict:
    prereg = _prereg()
    if (WORK / "inputs").exists() or (WORK / "receipt.json").exists():
        raise ValueError("v7 LibSV rollback already run; no in-place retry")
    fault = (PRIVATE / "source/fault/skid_buffer.sv").read_bytes()
    source = fault.decode()
    payload = action_v7.payload_from_source_v7(source, binder.LIBSV_CONTEXT)
    candidate, action = action_v7.apply_skid_payload_action_v7(source, payload)
    if action["rewritten"] != 1 or hashlib.sha256(candidate.encode()).hexdigest() != CANDIDATE_SHA:
        raise ValueError("candidate regeneration drift")
    inputs = WORK / "inputs"
    (inputs / "source/fault").mkdir(parents=True)
    (inputs / "tb").mkdir()
    inputs.chmod(0o700)
    backup = WORK / "backup-fault.sv"
    before = WORK / "candidate-before-rollback.sv"
    staged = inputs / "source/fault/skid_buffer.sv"
    backup.write_bytes(fault)
    before.write_text(candidate)
    staged.write_text(candidate)
    if sha(staged) != CANDIDATE_SHA:
        raise ValueError("candidate not staged before rollback")
    shutil.copyfile(backup, staged)
    if sha(staged) != FAULT_SHA:
        raise ValueError("fault source not restored")
    for test in ("target", "preservation"):
        shutil.copy2(PRIVATE / f"tb/tb_{test}.sv", inputs / f"tb/tb_{test}.sv")
    oracle = load_qualify()
    oracle.PRIVATE = inputs
    oracle.NATIVE_ROOT = WORK / "native-builds"
    native = oracle.run_native("rollback-r1", "fault")
    target = oracle.run_private(WORK / "private-builds", "fault", "target")
    preservation = oracle.run_private(WORK / "private-builds", "fault", "preservation")
    (WORK / "runner-summary.json").write_bytes(canonical(
        {"native": native, "target": target, "preservation": preservation}))
    receipt = verify(compare_saved=False)
    (WORK / "receipt.json").write_bytes(canonical(receipt))
    return receipt


def verify(*, compare_saved: bool = True) -> dict:
    prereg = _prereg()
    backup = WORK / "backup-fault.sv"
    before = WORK / "candidate-before-rollback.sv"
    staged = WORK / "inputs/source/fault/skid_buffer.sv"
    if (sha(backup) != FAULT_SHA or sha(staged) != FAULT_SHA or
            backup.read_bytes() != staged.read_bytes() or sha(before) != CANDIDATE_SHA or
            staged.read_bytes() != (PRIVATE / "source/fault/skid_buffer.sv").read_bytes()):
        raise ValueError("v7 LibSV candidate-before or restored fault source drift")
    regenerated, action = action_v7.apply_skid_payload_action_v7(
        staged.read_text(), action_v7.payload_from_source_v7(
            staged.read_text(), binder.LIBSV_CONTEXT))
    if regenerated.encode() != before.read_bytes() or action.get("rewritten") != 1:
        raise ValueError("v7 LibSV rollback action regeneration drift")
    oracle = load_qualify()
    native_stage = WORK / "native-builds/rollback-r1/native-fault"
    target_stage = WORK / "private-builds/private-fault-target"
    preservation_stage = WORK / "private-builds/private-fault-preservation"
    for path in (native_stage / "libsv/fifos/skid_buffer.sv", target_stage / "dut.sv",
                 preservation_stage / "dut.sv"):
        if sha(path) != FAULT_SHA:
            raise ValueError("v7 LibSV rollback compiled wrong RTL")
    native_xml = oracle.xml_case(native_stage / "pytest-junit.xml")
    inner = list((native_stage / "build/fifos").glob("*results.xml"))
    if len(inner) != 1 or native_xml != {"testcases": 1, "failures": 1} or oracle.xml_case(
            inner[0]) != native_xml:
        raise ValueError("v7 LibSV rollback native XML missing or wrong")
    native_text = (native_stage / "stdout.log").read_text() + (native_stage / "stderr.log").read_text()
    target_text = (target_stage / "sim.stdout.log").read_text() + (target_stage / "sim.stderr.log").read_text()
    preservation_text = ((preservation_stage / "sim.stdout.log").read_text() +
                         (preservation_stage / "sim.stderr.log").read_text())
    if ('test_skid_buffer.py", line 56, in cocotb_test_skid_buffer' not in native_text or
            "LIBSV_POST_STALL_HANDSHAKE_PAYLOAD_MISMATCH index=1" not in target_text or
            "TARGET_SETUP_INCOMPLETE" in target_text or
            "R5_LIBSV_PRESERVATION_PASS accepted=3 received=3" not in preservation_text):
        raise ValueError("v7 LibSV rollback oracle markers incorrect")
    for test, stage in (("target", target_stage), ("preservation", preservation_stage)):
        if sha(stage / "tb.sv") != prereg["qualified_inputs"][f"tb/tb_{test}.sv"]:
            raise ValueError("v7 LibSV rollback private test drift")
        if not (stage / "obj_dir/Vtb").is_file():
            raise ValueError("v7 LibSV rollback simulator build missing")
    summary = json.loads((WORK / "runner-summary.json").read_bytes())
    if (set(summary) != {"native", "target", "preservation"} or
            not all(summary[name]["qualified"] is True for name in summary) or
            summary["native"]["rc"] != 1 or
            summary["target"]["sim_rc"] == 0 or
            summary["preservation"]["sim_rc"] != 0 or
            any(summary[name]["source_sha256"] != FAULT_SHA for name in summary)):
        raise ValueError("v7 LibSV rollback runner summary contradicts raw logs")
    result = {"schema": SCHEMA, "role": ROLE, "valid": True,
              "preregistration_sha256": sha(WORK / "preregistration.json"),
              "train_audit_sha256": prereg["train_audit_sha256"],
              "candidate_before_sha256": sha(before),
              "restored_fault_sha256": sha(staged),
              "compiled_source_sha256": FAULT_SHA,
              "native_testcases": 1, "native_failures": 1,
              "target": "FAIL_SECOND_HANDSHAKE_PAYLOAD",
              "preservation": "PASS_THREE_DIRECT_TRANSFERS",
              "rollback_source_verified": True, "fresh_oracle_execution": True,
              "asset_authority_recorded": False, "memory_mremove": False,
              "heldout_transfer": False}
    result["digest"] = hashlib.sha256(canonical(result)).hexdigest()
    if compare_saved and result != json.loads((WORK / "receipt.json").read_bytes()):
        raise ValueError("v7 LibSV rollback receipt does not cold replay")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "run", "verify"))
    args = parser.parse_args()
    result = globals()[args.command]()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
