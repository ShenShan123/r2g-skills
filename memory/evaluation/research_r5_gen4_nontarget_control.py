"""Cold three-view R5 gen4 healthy non-target control; no repair denominator.

The trusted evaluator replays TRAIN authority. A no-network consumer receives
only the public clean RTL, public context, and a digest-checked decision. Each
view then gets a fresh, separately seeded native cocotb build.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import xml.etree.ElementTree as ET
from contextlib import nullcontext
from pathlib import Path


ROOT = Path("/data1/zhangdy/RTL/RTL_testbench/_r5_pilot")
BASE = ROOT / "transfer/abarajithan-axis-fifo-nontarget-gen4-r1"
QUAL = ROOT / "qualification/abarajithan-axis-fifo-nontarget-r1"
MEMORY = ROOT / "memory/r5-rtl-gen4-m0-v1"
APP = ROOT / "software/frozen-gen4-9818492/memory"
SOURCE = QUAL / "stage/axis_fifo/hdl/axis_fifo.sv"
TEST = QUAL / "stage/axis_fifo/tb/test_axis_fifo.py"
HELPER = QUAL / "stage/axis_fifo/tb/custom_axis.py"
CONTEXT = BASE / "public_context.json"
SEED_BOOT = BASE / "seed/sitecustomize.py"
PYDEPS = ROOT.parent / "_qualification/pydeps-py310"
HEAD = "981849267118e3163904ec6b77c0d8f726f0888e"
REPORT_DIGEST = "sha256:4a1fe5c076c00136ffd2a2b1bba9ccd9ad5f387261bdbc4454ed10537d30a0a5"
QUAL_RECEIPT_SHA = "sha256:455d126dff446d23dccc67f20a6b40ce3eec5a9d8250be1f9644c8e8eb624c6d"
SOURCE_SHA = "sha256:af7021f42726ce6cde1bf1617069a2f2bb44ef93315f6e1554329d69b702affc"
TEST_SHA = "sha256:1d0f30c3ada0be60c67754569b32357e305bc3926e496e322b13c110628daa10"
HELPER_SHA = "sha256:2700276b1b3d5332c3cef5e5bf1b0a4b2fee665eec36beb911f1e80fcde4065a"
BUNDLES = {
    "m-minus": "a31ef13da284f5cc3fb9569a94dba036c2bc355e8901b3cad5140ce5d86e815d",
    "m-plus": "34afb338cdc3ffe41d2bb6f56aad1336607a0a6116f410e780aeb8b36e158994",
    "mremove": "b0c9019e22ba1d4a2df0e61e6135ac794ad83955a242731d8349e53a2158f1da",
}
SCHEMA = "tehm-r5-gen4-healthy-nontarget-three-view-v1"
HANDOFF_KEYS = {"schema", "role", "view", "bundle_digest", "source_sha256",
                "public_context_sha256", "route", "selection", "selected_asset_ids"}


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def check_host_inputs() -> tuple[dict, dict]:
    if (sha(SOURCE.read_bytes()) != SOURCE_SHA or
            sha(TEST.read_bytes()) != TEST_SHA or
            sha(HELPER.read_bytes()) != HELPER_SHA or
            sha((QUAL / "receipt.json").read_bytes()) != QUAL_RECEIPT_SHA):
        raise ValueError("healthy control source/test/qualification identity drift")
    qualification = json.loads((QUAL / "receipt.json").read_bytes())
    if (qualification.get("valid_for_clean_control") is not True or
            qualification.get("valid_for_pilot_transfer") is not False or
            qualification.get("pytest_tests") != 3 or
            qualification.get("pytest_failures") != 0):
        raise ValueError("healthy non-target qualification gate not satisfied")
    context = json.loads(CONTEXT.read_bytes())
    if (context != {
            "design_id": "healthy_control_axis_fifo_001",
            "parameters": {"WIDTH": 8, "DEPTH": 4},
            "mechanism_family": "AXIS_FIFO_SHIFT_CHAIN",
            "compatibility_profile": "rtl.axis_fifo.shift_chain.control",
            "private_test_or_answer_fields": False}):
        raise ValueError("healthy non-target public context drift")
    report = json.loads((MEMORY / "research/m0-build-report.json").read_bytes())
    if (report.get("report_digest") != REPORT_DIGEST or
            report.get("software", {}).get("git_head") != HEAD):
        raise ValueError("gen4 M0 identity drift")
    worktree = APP.parent
    head = subprocess.run(["git", "-C", str(worktree), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True, timeout=20).stdout.strip()
    status = subprocess.run(["git", "-C", str(worktree), "status", "--porcelain=v1"],
                            check=True, capture_output=True, text=True, timeout=20).stdout
    if head != HEAD or status:
        raise ValueError("frozen gen4 software worktree drift")
    return context, report


def check_import() -> None:
    import tehm
    expected = Path("/app") if Path("/app/tehm").exists() else APP
    if not Path(tehm.__file__).resolve().is_relative_to(expected.resolve()):
        raise RuntimeError("TEHM import did not come from frozen gen4 software")


def load_ram(view: str) -> sqlite3.Connection:
    path = MEMORY / "bundles" / view / "closed_loop/tehm.sqlite"
    origin = sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        origin.backup(conn)
    finally:
        origin.close()
    return conn


def query_plan(context: dict) -> dict:
    return {"mechanism_family": context["mechanism_family"],
            "compatibility_profile": context["compatibility_profile"],
            "target_scope": context["compatibility_profile"],
            "transformation_family": "none",
            "design_id": context["design_id"]}


def authority(out: Path) -> dict:
    check_import()
    from contracts import MemoryQuery
    from tehm.assets import registry as _asset_registry  # initialize before selector
    from tehm.retrieval.asset_selector import select_knowledge_grounded_assets
    from tehm.retrieval.memory_router import route_memory
    from tehm.sync import verify_bundle
    from tehm.verified_execution import scoped_learning_replay

    context, _ = check_host_inputs()
    acquisitions = json.loads((MEMORY / "research/parent-acquisitions.json").read_bytes())
    source = SOURCE.read_text(encoding="utf-8")
    query = MemoryQuery(query_plan=query_plan(context))
    out.mkdir()
    views = {}
    for view, expected_digest in BUNDLES.items():
        bundle = verify_bundle(MEMORY / "bundles" / view)
        if not bundle["ok"] or bundle["manifest"]["bundle_digest"] != expected_digest:
            raise ValueError(view + ": frozen bundle digest drift")
        conn = load_ram(view)
        try:
            training = (scoped_learning_replay(
                conn, campaign_id=acquisitions["campaign_id"],
                acquisitions=acquisitions["acquisitions"],
                expected_digest=acquisitions["digest"])
                if view == "m-plus" else nullcontext())
            with training:
                route = route_memory(conn, query, no_memory_budget=1,
                                     memory_budget=1, persist_state=False, commit=False)
                selection = select_knowledge_grounded_assets(
                    conn, query, routing=route, rtl_source_text=source,
                    design_id=context["design_id"],
                    rtl_public_context=context["parameters"])
                decision = {"route": route.decision,
                            "route_reasons": list(route.abstain_reasons),
                            "selection": selection.receipt.decision,
                            "selection_reasons": list(selection.receipt.abstain_reasons),
                            "selected_asset_ids": list(selection.receipt.selected_asset_ids)}
                write_json(out / (view + "-decision.json"), decision)
                if (route.decision != "NO_SKILL" or
                        selection.receipt.decision != "NO_SKILL" or selection.assets):
                    raise ValueError(view + ": healthy non-target unexpectedly routed/selected")
                handoff = {"schema": SCHEMA, "role": "HEALTHY_NON_TARGET_CONTROL",
                           "view": view, "bundle_digest": expected_digest,
                           "source_sha256": SOURCE_SHA,
                           "public_context_sha256": sha(CONTEXT.read_bytes()),
                           "route": route.decision,
                           "selection": selection.receipt.decision,
                           "selected_asset_ids": []}
                write_json(out / (view + ".json"), handoff)
                views[view] = decision
        finally:
            conn.close()
    return views


def consumer() -> dict:
    check_import()
    if any(Path(path).exists() for path in ("/data1", "/snapshot", "/oracle", "/train")):
        raise RuntimeError("evaluator-private path visible to source-only consumer")
    allowed = sorted(str(path.relative_to("/inputs")) for path in
                     Path("/inputs").rglob("*") if path.is_file())
    if allowed != ["public_context.json", "rtl/axis_fifo.sv"]:
        raise RuntimeError("source-only input allowlist drift")
    source = Path("/inputs/rtl/axis_fifo.sv").read_bytes()
    public = Path("/inputs/public_context.json").read_bytes()
    handoff_bytes = Path("/handoff/handoff.json").read_bytes()
    handoff = json.loads(handoff_bytes)
    view = os.environ.get("TEHM_R5_CONTROL_VIEW")
    if (set(handoff) != HANDOFF_KEYS or view not in BUNDLES or
            handoff["schema"] != SCHEMA or
            handoff["role"] != "HEALTHY_NON_TARGET_CONTROL" or
            handoff["view"] != view or
            handoff["bundle_digest"] != BUNDLES[view] or
            handoff["source_sha256"] != SOURCE_SHA or sha(source) != SOURCE_SHA or
            handoff["public_context_sha256"] != sha(public) or
            handoff["route"] != "NO_SKILL" or
            handoff["selection"] != "NO_SKILL" or
            handoff["selected_asset_ids"] != []):
        raise RuntimeError("healthy non-target handoff/identity contradiction")
    result = {"schema": SCHEMA, "role": "HEALTHY_NON_TARGET_CONTROL",
              "view": view, "route": "NO_SKILL", "selection": "NO_SKILL",
              "action": "NO_ACTION", "source_sha256": SOURCE_SHA,
              "candidate_sha256": SOURCE_SHA, "handoff_sha256": sha(handoff_bytes),
              "network_namespace_inode": os.stat("/proc/self/ns/net").st_ino,
              "evaluator_filesystem_visible": False, "model_calls": 0}
    write_json(Path("/out/consumer.json"), result)
    return result


def actions(authority_dir: Path, out: Path) -> dict:
    out.mkdir()
    host_net = os.stat("/proc/self/ns/net").st_ino
    views = {}
    for view in BUNDLES:
        arm = out / view
        arm.mkdir()
        command = [
            "bwrap", "--unshare-all", "--die-with-parent", "--new-session",
            "--clearenv", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
            "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib",
            "--ro-bind", "/lib64", "/lib64", "--ro-bind", str(APP), "/app",
            "--dir", "/inputs", "--dir", "/inputs/rtl",
            "--ro-bind", str(SOURCE), "/inputs/rtl/axis_fifo.sv",
            "--ro-bind", str(CONTEXT), "/inputs/public_context.json",
            "--dir", "/handoff",
            "--ro-bind", str(authority_dir / (view + ".json")), "/handoff/handoff.json",
            "--ro-bind", str(Path(__file__).resolve()), "/pilot.py",
            "--bind", str(arm), "/out",
            "--setenv", "PYTHONPATH", "/app",
            "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
            "--setenv", "TEHM_R5_CONTROL_VIEW", view,
            "--chdir", "/", "/usr/bin/python3", "/pilot.py", "--consumer",
        ]
        process = subprocess.run(command, capture_output=True, text=True, timeout=40)
        (arm / "launcher.stdout.log").write_text(process.stdout)
        (arm / "launcher.stderr.log").write_text(process.stderr)
        if process.returncode != 0:
            raise RuntimeError(view + ": isolated consumer failed: " + process.stderr[-500:])
        result = json.loads((arm / "consumer.json").read_bytes())
        if (result.get("network_namespace_inode") == host_net or
                result.get("handoff_sha256") != sha(
                    (authority_dir / (view + ".json")).read_bytes()) or
                result.get("action") != "NO_ACTION" or
                (arm / "candidate.sv").exists()):
            raise RuntimeError(view + ": no-action isolation receipt drift")
        views[view] = result
    return views


def native_view(view: str, arm: Path) -> dict:
    stage = arm / "stage"
    hdl = stage / "axis_fifo/hdl"
    tb = stage / "axis_fifo/tb"
    hdl.mkdir(parents=True)
    tb.mkdir(parents=True)
    shutil.copy2(SOURCE, hdl / "axis_fifo.sv")
    shutil.copy2(TEST, tb / "test_axis_fifo.py")
    shutil.copy2(HELPER, tb / "custom_axis.py")
    if sha((hdl / "axis_fifo.sv").read_bytes()) != SOURCE_SHA:
        raise RuntimeError(view + ": compiled source is not the view output")
    env = dict(os.environ)
    env.update({"PYTHONPATH": str(SEED_BOOT.parent) + ":" + str(PYDEPS),
                "PYTHONDONTWRITEBYTECODE": "1", "RANDOM_SEED": "1729",
                "TEHM_R5_NUMPY_SEED": "1729"})
    command = ["/usr/bin/python3", "-m", "pytest", "-q",
               "axis_fifo/tb/test_axis_fifo.py", "-o",
               "cache_dir=" + str(arm / "pytest-cache"),
               "--junitxml=" + str(arm / "pytest-results.xml")]
    process = subprocess.run(command, cwd=stage, env=env, capture_output=True,
                             text=True, timeout=240)
    (arm / "pytest.stdout.log").write_text(process.stdout)
    (arm / "pytest.stderr.log").write_text(process.stderr)
    if process.returncode != 0:
        raise RuntimeError(view + ": native test failed: " + process.stdout[-500:])
    suite = ET.parse(arm / "pytest-results.xml").getroot().find("testsuite")
    if (suite is None or any(int(suite.attrib[k]) != expected for k, expected in
                             (("tests", 3), ("failures", 0), ("errors", 0), ("skipped", 0)))):
        raise RuntimeError(view + ": pytest XML is not 3/3 clean PASS")
    raw = sorted(stage.glob("axis_fifo/sim_build/*/*_results.xml"))
    if len(raw) != 3:
        raise RuntimeError(view + ": expected three raw cocotb XML reports")
    reports = []
    for path in raw:
        tree = ET.parse(path).getroot()
        cases = tree.findall(".//testcase")
        seeds = tree.findall(".//property[@name='random_seed']")
        if (len(cases) != 1 or cases[0].find("failure") is not None or
                cases[0].find("error") is not None or
                cases[0].find("skipped") is not None or
                len(seeds) != 1 or seeds[0].attrib.get("value") != "1729"):
            raise RuntimeError(view + ": raw cocotb verdict/seed drift")
        reports.append({"path": str(path.relative_to(arm)), "sha256": sha(path.read_bytes()),
                        "configuration": path.parent.name, "tests": 1,
                        "failures": 0, "errors": 0, "seed": 1729})
    if sha((hdl / "axis_fifo.sv").read_bytes()) != SOURCE_SHA:
        raise RuntimeError(view + ": simulator source changed during native run")
    return {"status": "PASS", "tests": 3,
            "pytest_xml_sha256": sha((arm / "pytest-results.xml").read_bytes()),
            "compiled_source_sha256": SOURCE_SHA, "raw_cocotb_reports": reports}


def run(output: Path) -> dict:
    check_import()
    check_host_inputs()
    target = output.resolve(strict=False)
    if target.parent != BASE or target.exists() or target.is_symlink():
        raise ValueError("control output must be a new direct child of its transfer root")
    target.mkdir()
    authority_dir = target / "authority"
    action_dir = target / "actions"
    oracle_dir = target / "native"
    oracle_dir.mkdir()
    routed = authority(authority_dir)
    acted = actions(authority_dir, action_dir)
    native = {}
    for view in BUNDLES:
        arm = oracle_dir / view
        arm.mkdir()
        native[view] = native_view(view, arm)
    report = {"schema": SCHEMA, "role": "HEALTHY_NON_TARGET_CONTROL_ONLY",
              "valid": True, "software_git_head": HEAD,
              "memory_report_digest": REPORT_DIGEST,
              "qualification_receipt_sha256": QUAL_RECEIPT_SHA,
              "public_context_sha256": sha(CONTEXT.read_bytes()),
              "seed_boot_sha256": sha(SEED_BOOT.read_bytes()),
              "seed_plan": {"cocotb_random_seed": 1729, "numpy_seed": 1729},
              "source_sha256": SOURCE_SHA,
              "source_only_isolated": True, "model_calls": 0,
              "pilot_transfer": False, "repair_denominator": False,
              "views": {view: {"route": routed[view],
                               "action": acted[view], "native": native[view]}
                        for view in BUNDLES}}
    write_json(target / "report.json", report)
    return {"valid": True, "output": str(target),
            "report_sha256": sha((target / "report.json").read_bytes()),
            "routes": {v: routed[v]["route"] for v in BUNDLES},
            "native": {v: native[v]["status"] for v in BUNDLES}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--consumer", action="store_true")
    args = parser.parse_args()
    if not args.consumer and args.output is None:
        parser.error("--output is required outside the isolated consumer")
    result = consumer() if args.consumer else run(args.output)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
