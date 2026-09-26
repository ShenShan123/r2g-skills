"""Cold replay of the preregistered, researcher-assisted LibSV TRAIN arms.

This establishes one bounded TRAIN oracle instance, not unseen transfer or
an independent causal-origin claim. Saved PASS flags are never an oracle.
"""
from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from tehm.evaluation import research_r5_skid_binding_v7 as binder
from tehm.rtl.skid_payload_action_v7 import (
    apply_skid_payload_action_v7, payload_from_source_v7,
)

PILOT = Path("/data1/zhangdy/RTL/RTL_testbench/_r5_pilot")
TRAIN = PILOT / "training/skid-v7-r1"
RUN = TRAIN / "fresh-r1"
QUAL = PILOT / "qualification/libsv-skid-oracle-v1/evaluator-private"
REPOSITORY = "bensampson5/libsv"
SOURCE_SHA = {
    "clean": "a5e2e2d3f753a43d6d643c3d030809e00198a6478dcfaf9225a47fa096df472a",
    "fault": "1536de8a2e007048e9538f218b3eb83960c5328ac1daac828b360393a3939d0f",
}
TB_SHA = {
    "target": "d20fc09f3f43dd3d989eb7012310d0aeba1aa79d9458e19588058b31961df680",
    "preservation": "6e33a0a5cd62960382c9c734c4185c5fbfc2dbda5aaa5e8bbf04f43cd8486663",
}
NATIVE_TEST_SHA = "b4ec3a4c5ca24676d8ab5c2f3d579603ff8146a62c6795ede629b2782287547e"
NATIVE_INPUTS = {
    "tests/fifos/test_skid_buffer.py": NATIVE_TEST_SHA,
    "tests/utils.py": "8136fb9c6f5516100bfc5eadeac6f327b3e927bbfd2457976e226f87befe05e6",
    "tests/conftest.py": "9fcbd28c0a8f331cd1394b30d8cfa2480ed5eb35c6819340f7cb3d618d7800b8",
    "pyproject.toml": "568715e5b43636c2a93aa905cc0bac608ae2d3f9f7df02f1c1e5db81970c2dba",
}
QUALIFY_SHA = "11286569ab57ae91464b799c732b0149f45fd5e20aa89a91ccfa14785fb3310a"
TRAIN_RUNNER_SHA = "22ef4e9d0ac59a84af9e0ea6d3ec604697f0262d39709152d81ce2e0c99d4afd"
VERILATOR_SHA = "aa644932964f713353b9293ea0d390af282009ab6a882c31de9cf6627e29df9d"
ROLE = "RESEARCHER_ASSISTED_TRAIN_REUSED_DEV"


def _sha(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("TRAIN input missing or symlink: " + str(path))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _xml(path: Path) -> dict:
    cases = ET.parse(path).getroot().findall(".//testcase")
    return {"testcases": len(cases),
            "failures": sum(bool(item.findall("failure") or item.findall("error"))
                            for item in cases)}


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise ValueError("LibSV TRAIN raw replay: " + reason)


def verify() -> dict:
    prereg_sha = _sha(TRAIN / "preregistration.md")
    action = json.loads((RUN / "action.json").read_bytes())
    saved = json.loads((RUN / "receipt.json").read_bytes())
    fault_path = RUN / "evaluator-inputs/source/fault/skid_buffer.sv"
    clean_path = RUN / "evaluator-inputs/source/clean/skid_buffer.sv"
    fault = fault_path.read_text(encoding="utf-8")
    candidate = clean_path.read_text(encoding="utf-8")
    payload = payload_from_source_v7(fault, binder.LIBSV_CONTEXT)
    replayed, edit = apply_skid_payload_action_v7(fault, payload)
    bound = binder.bind_skid_payload_v7(
        {"binding_template": binder.TEMPLATE}, fault, binder.LIBSV_CONTEXT)
    source_edit = {key: value for key, value in edit.items()
                   if key not in {"domain", "compatibility_profile"}}
    _require(replayed == candidate and source_edit == action["action"] and
             bound == action["binding"] and edit.get("rewritten") == 1,
             "candidate/action/binding mismatch")
    _require(_sha(fault_path) == _sha(QUAL / "source/fault/skid_buffer.sv") == SOURCE_SHA["fault"] and
             _sha(clean_path) == _sha(QUAL / "source/clean/skid_buffer.sv") == SOURCE_SHA["clean"] and
             action["fault_sha256"] == SOURCE_SHA["fault"] and
             action["candidate_sha256"] == SOURCE_SHA["clean"], "source identity")
    for test, expected in TB_SHA.items():
        _require(_sha(RUN / f"evaluator-inputs/tb/tb_{test}.sv") ==
                 _sha(QUAL / f"tb/tb_{test}.sv") == expected, test + " TB identity")
    _require(_sha(QUAL.parent / "qualify.py") == QUALIFY_SHA and
             _sha(TRAIN / "run_train.py") == TRAIN_RUNNER_SHA and
             _sha(PILOT.parent / REPOSITORY / "tests/fifos/test_skid_buffer.py") ==
             NATIVE_TEST_SHA and
             _sha(QUAL / "make_fault.py") ==
             _sha(RUN / "evaluator-inputs/make_fault.py") ==
             "f4cbb7b4f4be52191c8cfaedf1efeb341dc75fcaf0c26d128a71ce5dcd4504ae" and
             _sha(Path("/opt/pdk_klayout_openroad/oss-cad-suite/bin/verilator")) ==
             VERILATOR_SHA, "TRAIN evaluator/software/tool identity")
    _require(action["preregistration_sha256"] == saved["preregistration_sha256"] == prereg_sha and
             action["role"] == saved["role"] == ROLE and action["model_calls"] == 0 and
             action["binder_code_sha256"] == saved["binder_code_sha256"] ==
             _sha(Path(binder.__file__)) and saved["candidate_sha256"] == SOURCE_SHA["clean"] and
             saved["qualified_clean_sha256"] == SOURCE_SHA["clean"] and
             saved["candidate_equals_qualified_clean"] is True and
             saved["fresh_six_arm_valid"] is True and
             all(saved[field] is False for field in (
                 "knowledge_admitted", "asset_authority_recorded",
                 "memory_constructed", "heldout_transfer")), "receipt identity/role")
    observed = {}
    for source in ("clean", "fault"):
        stage = RUN / f"native-builds/fresh-r1/native-{source}"
        _require(_sha(stage / "libsv/fifos/skid_buffer.sv") == SOURCE_SHA[source] and
                 _sha(stage / "tests/fifos/test_skid_buffer.py") == NATIVE_TEST_SHA,
                 source + " native source/test")
        for relative, expected in NATIVE_INPUTS.items():
            _require(_sha(stage / relative) ==
                     _sha(PILOT.parent / REPOSITORY / relative) == expected,
                     source + " native dependency: " + relative)
        xmls = list((stage / "build/fifos").glob("*results.xml"))
        _require(len(xmls) == 1, source + " native cocotb XML cardinality")
        outer, inner = _xml(stage / "pytest-junit.xml"), _xml(xmls[0])
        counts = {"testcases": 1, "failures": 0 if source == "clean" else 1}
        stdout = (stage / "stdout.log").read_text(encoding="utf-8")
        _require(outer == inner == counts and
                 ("1 passed" in stdout if source == "clean" else
                  'test_skid_buffer.py", line 56, in cocotb_test_skid_buffer' in stdout),
                 source + " native raw verdict")
        observed[f"native-{source}"] = {
            "arm": f"native-{source}", "command": [
                "/usr/bin/python3", "-m", "pytest", "-q",
                "tests/fifos/test_skid_buffer.py", "--junitxml=pytest-junit.xml"],
            "source_sha256": SOURCE_SHA[source], "native_test_sha256": NATIVE_TEST_SHA,
            "rc": 0 if source == "clean" else 1,
            "pytest": outer, "cocotb": inner, "qualified": True,
        }
    for source in ("clean", "fault"):
        for test in ("target", "preservation"):
            arm = f"private-{source}-{test}"
            stage = RUN / "private-builds" / arm
            _require(_sha(stage / "dut.sv") == SOURCE_SHA[source] and
                     _sha(stage / "tb.sv") == TB_SHA[test], arm + " compiled inputs")
            build = ((stage / "build.stdout.log").read_text(encoding="utf-8") +
                     (stage / "build.stderr.log").read_text(encoding="utf-8"))
            sim = ((stage / "sim.stdout.log").read_text(encoding="utf-8") +
                   (stage / "sim.stderr.log").read_text(encoding="utf-8"))
            fault_target = source == "fault" and test == "target"
            marker = ("LIBSV_POST_STALL_HANDSHAKE_PAYLOAD_MISMATCH index=1" if fault_target
                      else "R5_LIBSV_TARGET_PASS accepted=2 received=2" if test == "target"
                      else "R5_LIBSV_PRESERVATION_PASS accepted=3 received=3")
            _require("error:" not in build.lower() and marker in sim and
                     "TARGET_SETUP_INCOMPLETE" not in sim, arm + " raw simulation")
            command = ["/opt/pdk_klayout_openroad/oss-cad-suite/bin/verilator",
                       "--binary", "--timing", "-Wno-fatal", "-j", "1",
                       "--top-module", "tb", "--Mdir", str(stage / "obj_dir"),
                       str(stage / "dut.sv"), str(stage / "tb.sv")]
            observed[arm] = {"arm": arm, "command": command,
                             "source_sha256": SOURCE_SHA[source],
                             "tb_sha256": TB_SHA[test], "build_rc": 0,
                             "sim_rc": -6 if fault_target else 0,
                             "qualified": True}
    _require(len(saved["evaluations"]) == 6 and
             {entry["arm"]: entry for entry in saved["evaluations"]} == observed,
             "six-arm receipt differs from raw outcome")
    return {"schema": "tehm-r5-libsv-train-v7-cold-raw-replay-r1",
            "role": ROLE, "repository": REPOSITORY,
            "preregistration_sha256": prereg_sha,
            "action_sha256": _sha(RUN / "action.json"),
            "receipt_sha256": _sha(RUN / "receipt.json"),
            "source_sha256": dict(SOURCE_SHA), "tb_sha256": dict(TB_SHA),
            "observed": {name: {"source_sha256": row["source_sha256"],
                                "verdict": "FAIL" if name in (
                                    "native-fault", "private-fault-target") else "PASS"}
                         for name, row in observed.items()},
            "raw_six_arm_valid": True, "heldout_transfer": False}


__all__ = ["TRAIN", "RUN", "REPOSITORY", "SOURCE_SHA", "ROLE", "verify"]
