"""Build per-host integration receipts from real offline test artifacts."""
import hashlib
import json
import time
import xml.etree.ElementTree as ET

from bridge import ROOT


def read_suite(name, required):
    path = ROOT / "reports" / name
    node = ET.parse(path).getroot()
    cases = list(node.iter("testcase"))
    if any(list(case.iter("failure")) or list(case.iter("error")) for case in cases):
        raise ValueError("test suite has failures: " + name)
    passed = [case.get("name", "") for case in cases if case.find("skipped") is None]
    for expected in required:
        if expected not in passed:
            raise ValueError("required native branch was not tested: " + expected)
    return {"path": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "passed": len(passed), "skipped": len(cases)-len(passed)}


def prepare():
    path = ROOT / "reports/continuation-qualification.json"
    if path.exists():
        raise ValueError("qualification already recorded; do not overwrite")
    modern = read_suite("modern-continuation-tests.xml", [
        "test_native_local_execution_and_verified_rollback_without_llm[True]",
        "test_native_local_execution_and_verified_rollback_without_llm[False]",
        "test_skip_authorization_is_exact_and_never_turns_usage_into_known",
        "test_never_started_rule_rejects_any_existing_revision",
        "test_apply_cannot_execute_a_different_retrieved_rule_and_consider_is_advice_only"])
    fixer = read_suite("fixer-continuation-tests.xml", [
        "test_real_native_rag_agent_compiler_and_simulator_with_offline_transport",
        "test_local_source_reuse_and_entry_restoration_use_native_evaluator[True]",
        "test_local_source_reuse_and_entry_restoration_use_native_evaluator[False]"])
    data = {"schema": "table4-continuation-host-qualification-v1", "time": time.time(),
        "paper_data": False, "core_algorithm_changed": False,
        "tests": {"modern": modern, "rtlfixer": fixer},
        "qualification_scope": "native adapter branch integration; direct routing uses explicit unit fixtures, not live promoted-rule evidence",
        "hosts": {
            "rtlscout": {"launch_allowed": True, "reason": "native tools, local edit/verify/rollback and online hooks pass"},
            "rtlfixer": {"launch_allowed": True, "reason": "native RAG and native Icarus compile/simulation, local source reuse and entry restoration pass"},
            "mini-swe-agent": {"launch_allowed": False,
                "reason": "official SWE-bench Docker runtime/socket unavailable; instance environments unqualified; full verified software reuse not established"}}}
    path.write_text(json.dumps(data, indent=2, sort_keys=True)+"\n")
    return data


if __name__ == "__main__":
    print(json.dumps(prepare()))
