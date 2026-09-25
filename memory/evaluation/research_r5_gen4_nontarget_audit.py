"""Independent cold audit of the gen4 healthy non-target three-view control."""
from __future__ import annotations

import hashlib
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path("/data1/zhangdy/RTL/RTL_testbench/_r5_pilot")
BASE = ROOT / "transfer/abarajithan-axis-fifo-nontarget-gen4-r1"
RUN = BASE / "run-r2"
QUAL = ROOT / "qualification/abarajithan-axis-fifo-nontarget-r1"
MEMORY = ROOT / "memory/r5-rtl-gen4-m0-v1"
EXPECTED = {
    "report": "ea1111752d12c5bb41708a9792b1588f1b07a629c36d8e860883d75b872cdc3f",
    "harness": "b994cd10840fecf6c8b7a2237d706e75b033acf728095ec926cf4de7858f9a29",
    "public_context": "91427764eb7224dd91401d4c6345ce4d725e96c3ed1820502d19949d8a683236",
    "seed_boot": "32f135e229d37a2a9588ea51ffead62469e008ba666d38d0f6c624bc9d472497",
    "qualification": "455d126dff446d23dccc67f20a6b40ce3eec5a9d8250be1f9644c8e8eb624c6d",
    "source": "af7021f42726ce6cde1bf1617069a2f2bb44ef93315f6e1554329d69b702affc",
    "test": "1d0f30c3ada0be60c67754569b32357e305bc3926e496e322b13c110628daa10",
    "helper": "2700276b1b3d5332c3cef5e5bf1b0a4b2fee665eec36beb911f1e80fcde4065a",
}
VIEWS = ("m-minus", "m-plus", "mremove")
CONFIGS = ("WIDTH=8,DEPTH=4", "WIDTH=8,DEPTH=2", "WIDTH=16,DEPTH=6")
BUNDLES = {
    "m-minus": "a31ef13da284f5cc3fb9569a94dba036c2bc355e8901b3cad5140ce5d86e815d",
    "m-plus": "34afb338cdc3ffe41d2bb6f56aad1336607a0a6116f410e780aeb8b36e158994",
    "mremove": "b0c9019e22ba1d4a2df0e61e6135ac794ad83955a242731d8349e53a2158f1da",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def exact(path: Path, expected: str) -> None:
    if path.is_symlink() or digest(path) != expected:
        raise ValueError("evidence identity drift: " + str(path))


def suite(path: Path, count: int) -> None:
    root = ET.parse(path).getroot()
    cases = root.findall(".//testcase")
    if len(cases) != count or any(
            case.find(tag) is not None for case in cases
            for tag in ("failure", "error", "skipped")):
        raise ValueError("native XML failure, skip, or test count drift: " + str(path))


def vcd_body(path: Path) -> str:
    data = path.read_bytes()
    if not data.startswith(b"$date\n") or b"$end\n" not in data:
        raise ValueError("VCD date header missing")
    body = data.split(b"$end\n", 1)[1]
    return hashlib.sha256(body).hexdigest()


def audit() -> dict:
    repo = Path(__file__).resolve().parents[2]
    exact(repo / "memory/evaluation/research_r5_gen4_nontarget_control.py",
          EXPECTED["harness"])
    exact(BASE / "public_context.json", EXPECTED["public_context"])
    exact(BASE / "seed/sitecustomize.py", EXPECTED["seed_boot"])
    exact(QUAL / "receipt.json", EXPECTED["qualification"])
    exact(QUAL / "stage/axis_fifo/hdl/axis_fifo.sv", EXPECTED["source"])
    exact(QUAL / "stage/axis_fifo/tb/test_axis_fifo.py", EXPECTED["test"])
    exact(QUAL / "stage/axis_fifo/tb/custom_axis.py", EXPECTED["helper"])
    exact(RUN / "report.json", EXPECTED["report"])
    report = json.loads((RUN / "report.json").read_bytes())
    if (report.get("schema") != "tehm-r5-gen4-healthy-nontarget-three-view-v1" or
            report.get("valid") is not True or
            report.get("role") != "HEALTHY_NON_TARGET_CONTROL_ONLY" or
            report.get("software_git_head") != "981849267118e3163904ec6b77c0d8f726f0888e" or
            report.get("memory_report_digest") != "sha256:4a1fe5c076c00136ffd2a2b1bba9ccd9ad5f387261bdbc4454ed10537d30a0a5" or
            report.get("qualification_receipt_sha256") != "sha256:" + EXPECTED["qualification"] or
            report.get("public_context_sha256") != "sha256:" + EXPECTED["public_context"] or
            report.get("seed_boot_sha256") != "sha256:" + EXPECTED["seed_boot"] or
            report.get("source_sha256") != "sha256:" + EXPECTED["source"] or
            report.get("pilot_transfer") is not False or
            report.get("repair_denominator") is not False or
            report.get("model_calls") != 0 or
            report.get("source_only_isolated") is not True or
            report.get("seed_plan") != {"cocotb_random_seed": 1729,
                                        "numpy_seed": 1729} or
            set(report.get("views", {})) != set(VIEWS)):
        raise ValueError("control role or report metadata drift")
    host_net = os.stat("/proc/self/ns/net").st_ino
    from tehm.sync import verify_bundle
    for view, expected in BUNDLES.items():
        checked = verify_bundle(MEMORY / "bundles" / view)
        if not checked["ok"] or checked["manifest"]["bundle_digest"] != expected:
            raise ValueError(view + ": cold bundle verification failed")

    waveforms = {}
    for view in VIEWS:
        entry = report["views"][view]
        action = entry["action"]
        native = entry["native"]
        decision = entry["route"]
        arm = RUN / "native" / view
        exact(arm / "stage/axis_fifo/hdl/axis_fifo.sv", EXPECTED["source"])
        exact(arm / "stage/axis_fifo/tb/test_axis_fifo.py", EXPECTED["test"])
        exact(arm / "stage/axis_fifo/tb/custom_axis.py", EXPECTED["helper"])
        if (decision.get("route") != "NO_SKILL" or
                decision.get("selection") != "NO_SKILL" or
                decision.get("selected_asset_ids") != [] or
                action.get("route") != "NO_SKILL" or
                action.get("selection") != "NO_SKILL" or
                action.get("action") != "NO_ACTION" or
                action.get("candidate_sha256") != "sha256:" + EXPECTED["source"] or
                action.get("evaluator_filesystem_visible") is not False or
                action.get("network_namespace_inode") == host_net or
                (RUN / "actions" / view / "candidate.sv").exists() or
                native.get("status") != "PASS" or native.get("tests") != 3 or
                native.get("compiled_source_sha256") != "sha256:" + EXPECTED["source"]):
            raise ValueError(view + ": route/action/native contradiction")
        if decision != json.loads((RUN / "authority" /
                                  (view + "-decision.json")).read_bytes()):
            raise ValueError(view + ": authority decision receipt drift")
        consumer = json.loads((RUN / "actions" / view / "consumer.json").read_bytes())
        if consumer != action:
            raise ValueError(view + ": isolated consumer receipt drift")
        pytest_xml = arm / "pytest-results.xml"
        exact(pytest_xml, native["pytest_xml_sha256"].split(":", 1)[1])
        suite(pytest_xml, 3)
        raw = native["raw_cocotb_reports"]
        if {item["configuration"] for item in raw} != set(CONFIGS):
            raise ValueError(view + ": native configuration denominator drift")
        for item in raw:
            path = arm / item["path"]
            if not path.resolve().is_relative_to(arm.resolve()):
                raise ValueError("native XML path escaped arm")
            exact(path, item["sha256"].split(":", 1)[1])
            suite(path, 1)
            props = ET.parse(path).getroot().findall(".//property[@name='random_seed']")
            if len(props) != 1 or props[0].attrib.get("value") != "1729":
                raise ValueError(view + ": cocotb seed drift")
            vcd = arm / "stage/axis_fifo/sim_build" / item["configuration"] / "axis_fifo.vcd"
            waveforms.setdefault(item["configuration"], {})[view] = vcd_body(vcd)
    if any(len(set(hashes.values())) != 1 for hashes in waveforms.values()):
        raise ValueError("same-seed VCD bodies differ between Memory views")
    if not (BASE / "run-r1/INFRASTRUCTURE_FAILURE.md").is_file():
        raise ValueError("first failed infrastructure attempt is missing")
    return {"valid": True, "role": "HEALTHY_NON_TARGET_CONTROL_ONLY",
            "report_sha256": EXPECTED["report"], "views": list(VIEWS),
            "native_case_executions": 9,
            "same_seed_vcd_body_sha256": {k: next(iter(v.values()))
                                           for k, v in waveforms.items()},
            "pilot_transfer": False, "positive_memory_attribution": False}


if __name__ == "__main__":
    print(json.dumps(audit(), indent=2, sort_keys=True))
