#!/usr/bin/env python3
"""Freeze a prospective non-P12 gap cohort and its ACTUAL pre-outcome routes.

This is an input compiler, not a gap detector, asset registration, mutation or
production grant. A subsequent executor must establish actual baseline source
failures before capturing verified training repairs. Heldout/calibration never
becomes learner evidence. Source hashes alone are NOT design generalization.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts import MemoryQuery
from scripts.build_p13_interference_policy_views import _runtime_code_binding, _self_digest
from scripts.build_p13_interference_source_bound_plan import _load_json, _sha256
from scripts.build_r3_orfs_interference_source_bound_inputs import _digest, _write
from tehm import db
from tehm.retrieval.memory_router import route_memory
from tehm.rtl.verilog_parse import parse_verilog


class CapabilityGapInputError(ValueError):
    """A prospective source/partition/oracle boundary is not established."""


def _pin(path):
    return {"path": str(path), "sha256": _sha256(path)}


def _contained_file(project, name):
    path = (project / name).resolve()
    if not path.is_file() or not path.is_relative_to(project):
        raise CapabilityGapInputError("project input must be an existing contained file")
    return path


def _case(row):
    if type(row.get("case_id")) is not str or not row["case_id"].strip():
        raise CapabilityGapInputError("case identity must be a non-empty string")
    project = Path(row["project"]).resolve()
    manifest_path = _contained_file(project, "manifest.json")
    manifest = _load_json(manifest_path, "RTL project manifest")
    role = row.get("role")
    if (role not in {"training", "held_out", "validation"} or row.get("dataset_split") != role or
            row.get("learner_eligible") is not (role == "training") or
            not isinstance(manifest.get("design"), str) or not manifest["design"].strip() or
            row.get("lineage_id") != manifest["design"]):
        raise CapabilityGapInputError("prospective role/lineage does not match actual project identity")
    if role != "training" and "fix" in manifest:
        raise CapabilityGapInputError("non-learner manifest must not expose a repair answer")
    if role == "training" and (not isinstance(manifest.get("fix"), dict) or
            not str(manifest["fix"].get("domain", "")).startswith("rtl.")):
        raise CapabilityGapInputError("training repair proposal is missing; it is not verified evidence yet")
    query_plan = row.get("query_plan")
    if (not isinstance(query_plan, dict) or
            set(query_plan) != {"mechanism_family", "compatibility_profile", "target_scope"} or
            any(type(value) is not str or not value.strip() for value in query_plan.values()) or
            query_plan["mechanism_family"] != manifest.get("mechanism_family")):
        raise CapabilityGapInputError("query must contain only declared static mechanism/profile/scope facts")
    paths = sorted((project / "rtl").glob("*.v"))
    if not paths:
        raise CapabilityGapInputError("project has no actual Verilog source")
    rtl, parsed = [], []
    for source in paths:
        source = _contained_file(project, str(source.relative_to(project)))
        modules = parse_verilog(source.read_text())
        if not modules:
            raise CapabilityGapInputError("RTL source is not supported by the structural parser")
        rtl.append(_pin(source))
        parsed.extend(module.to_dict() for module in modules)
    fsm_profile = query_plan["compatibility_profile"] in {
        "rtl.fsm.single_guard.v1", "rtl.fsm.guard_conjunction.v1"}
    if fsm_profile:
        fsms = [fsm for module in parsed for block in module["always_blocks"] for fsm in block["fsms"]]
        if len(parsed) != 1 or len(fsms) != 1 or not fsms[0]["items"]:
            raise CapabilityGapInputError("declared FSM profile requires one actual parser-supported FSM")
    guard_locator = None
    if query_plan["compatibility_profile"] == "rtl.fsm.guard_conjunction.v1":
        from tehm.assets.guard_binding import locate_guard_conjunction
        if len(rtl) != 1:
            raise CapabilityGapInputError("guard locator requires one actual source file")
        try:
            guard_locator = locate_guard_conjunction(Path(rtl[0]["path"]).read_text())
        except ValueError as exc:
            raise CapabilityGapInputError("declared guard profile does not satisfy source-only locator") from exc
        if role == "training":
            fix = {key: value for key, value in manifest["fix"].items()
                   if key != "transformation_family"}
            if fix != guard_locator["payload"]:
                raise CapabilityGapInputError("training proposal must match the source-only locator")
    verification = manifest["verification"]
    tests = {key: _pin(_contained_file(project, verification[key])) for key in (
        "target_test", "frozen_regression")}
    if (tests["target_test"]["path"] == tests["frozen_regression"]["path"] or
            tests["target_test"]["sha256"] == tests["frozen_regression"]["sha256"]):
        raise CapabilityGapInputError("target and frozen regression must not alias one testbench")
    return {"case_id": row["case_id"], "project": str(project), "lineage_id": row["lineage_id"],
        "dataset_split": role, "role": role, "learner_eligible": role == "training",
        "project_manifest": _pin(manifest_path), "rtl_inputs": rtl, "verification_inputs": tests,
        "parsed_structural_digest": _digest(parsed), "query": MemoryQuery(query_plan=query_plan).to_dict(),
        "fsm_syntax_profile_checked": fsm_profile,
        "source_guard_locator_checked": guard_locator is not None,
        "source_guard_locator_digest": _digest(guard_locator) if guard_locator is not None else None,
        "query_plan": query_plan, "repair_proposal_is_not_evidence": True}


def _partitions(cases):
    if not cases or len({case["case_id"] for case in cases}) != len(cases):
        raise CapabilityGapInputError("cohort membership is empty or duplicated")
    groups = {role: [case for case in cases if case["role"] == role]
              for role in ("training", "held_out", "validation")}
    for role, minimum in (("training", 2), ("held_out", 2), ("validation", 1)):
        if len(groups[role]) < minimum or len({c["lineage_id"] for c in groups[role]}) < minimum:
            raise CapabilityGapInputError("insufficient distinct prospective " + role + " lineages")
    seen_lineages, seen_rtl = set(), set()
    for case in cases:
        hashes = {ref["sha256"] for ref in case["rtl_inputs"]}
        if case["lineage_id"] in seen_lineages or not hashes or hashes & seen_rtl:
            raise CapabilityGapInputError("cases overlap actual lineage or RTL content")
        seen_lineages.add(case["lineage_id"])
        seen_rtl.update(hashes)
    return {role: [case["case_id"] for case in rows] for role, rows in groups.items()}


def _source(p14_path):
    p14 = _load_json(p14_path, "current completed formal P14")
    _self_digest(p14, "report_digest")
    if (p14.get("version") != "p14-interference-formal-attribution-report-v1" or
            p14.get("bounded_attribution_complete") is not True or
            p14.get("canonical_memory_mutation") != "none" or
            p14.get("production_runtime_imported") is not False or p14.get("promotion_attempted") is not False):
        raise CapabilityGapInputError("current P14 source or authority boundary drift")
    pin = p14["runner_source_binding"]
    if _sha256(Path(pin["path"])) != pin["sha256"]:
        raise CapabilityGapInputError("formal P14 producer source drift")
    ref = p14["source_database"]
    path = Path(ref["path"]).resolve()
    if (_sha256(path) != ref["sha256"] or p14.get("source_database_sha256_after") != ref["sha256"] or
            any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm"))):
        raise CapabilityGapInputError("current source must be an unchanged sidecar-free snapshot")
    return path, p14


def _actual_routes(source, cases):
    """No synthetic gap-state route, no learner capture and no file-backed writes."""
    frozen = db.connect_read_only(source)
    ram = sqlite3.connect(":memory:")
    ram.row_factory = sqlite3.Row
    ram.execute("PRAGMA foreign_keys=ON")
    try:
        frozen.backup(ram)
        before = _digest("\n".join(ram.iterdump()))
        ram.execute("SAVEPOINT gap_input_routing")
        # CREATE IF NOT EXISTS may bootstrap empty derived schema. Permit that
        # in RAM, but deny ALL data-row writes and destructive schema changes.
        # Roll back even those additive DDL statements before comparing source.
        forbidden = {sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_DROP_INDEX,
            sqlite3.SQLITE_DROP_TRIGGER, sqlite3.SQLITE_DROP_VIEW, sqlite3.SQLITE_ALTER_TABLE}
        def authorize(operation, table, column, database, trigger):
            if operation in forbidden or (operation in {
                    sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}
                    and table not in {"sqlite_master", "sqlite_temp_master"}):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        ram.set_authorizer(authorize)
        routes = {}
        for case in cases:
            query = MemoryQuery(**case["query"])
            try:
                route = route_memory(ram, query, mode="shadow", no_memory_budget=1,
                    memory_budget=2, persist_state=False, commit=False)
            except sqlite3.Error as exc:
                raise CapabilityGapInputError("router attempted forbidden data writes or schema change") from exc
            if (case["role"] == "training" and
                    (route.decision != "NO_SKILL" or route.no_skill_reason != "NO_MATCH")):
                raise CapabilityGapInputError("actual current training route is not NO_SKILL/NO_MATCH")
            routes[case["case_id"]] = {**route.to_dict(), "decision_digest": route.decision_digest,
                "routing_receipt_id": route.routing_receipt_id}
        ram.set_authorizer(None)
        ram.execute("ROLLBACK TO gap_input_routing")
        ram.execute("RELEASE gap_input_routing")
        if _digest("\n".join(ram.iterdump())) != before:
            raise CapabilityGapInputError("input routing unexpectedly mutated its disposable source")
        return routes, before
    finally:
        frozen.close()
        ram.close()


def prepare_gap_inputs(preregistration, p14_attribution, *, output):
    prereg, p14_path, output = (Path(path).resolve() for path in (preregistration, p14_attribution, output))
    if output.exists():
        raise CapabilityGapInputError("input freeze output must be new")
    spec = _load_json(prereg, "prospective gap preregistration")
    if spec.get("lane") != "CAPABILITY_GAP" or not spec.get("campaign_id"):
        raise CapabilityGapInputError("prospective CAPABILITY_GAP lane is required")
    execution_root = Path(spec["execution_artifacts_root"]).resolve()
    if execution_root.exists() and (not execution_root.is_dir() or any(execution_root.iterdir())):
        raise CapabilityGapInputError("gap inputs must freeze before any baseline or repair execution")
    cases = [_case(row) for row in spec["cases"]]
    partition = _partitions(cases)
    # Require explicitly selected personal tools; never PATH-discover tools.
    tool_bindings = {}
    for key in ("iverilog", "vvp"):
        path = Path(spec["oracle_tools"][key]).resolve()
        if not path.is_file():
            raise CapabilityGapInputError("explicit oracle tool is missing: " + key)
        tool_bindings[key] = _pin(path)
    source, p14 = _source(p14_path)
    routes, logical = _actual_routes(source, cases)
    if logical != p14["source_database"]["logical_digest"] or _sha256(source) != p14["source_database"]["sha256"]:
        raise CapabilityGapInputError("actual source logical/file binding does not replay")
    for case in cases:
        for ref in (case["project_manifest"], *case["rtl_inputs"], *case["verification_inputs"].values()):
            if _sha256(Path(ref["path"])) != ref["sha256"]:
                raise CapabilityGapInputError("prospective source or oracle input drift during compilation")
    report = {"version": "r3-source-bound-capability-gap-input-freeze-v3",
        "campaign_id": spec["campaign_id"], "lane": "CAPABILITY_GAP", "preregistration": _pin(prereg),
        "p14_attribution": {**_pin(p14_path), "report_digest": p14["report_digest"]},
        "source_database": p14["source_database"], "cases": cases, "partition": partition,
        "execution_artifacts_root": str(execution_root), "actual_preexecution_routing": routes,
        "runtime_generation_binding": _runtime_code_binding(), "oracle_tool_bindings": tool_bindings,
        "compiler_source_binding": _pin(Path(__file__).resolve()), "frozen_before_execution": True,
        "actual_router_used": True, "synthetic_gap_route_used": False, "eda_executed": False,
        "source_database_unchanged": True, "canonical_memory_mutation": "none",
        "training_capture_performed": False, "learner_support_imported": False,
        "gap_derivation_created": False, "asset_registered": False, "knowledge_registered": False,
        "production_runtime_imported": False, "promotion_attempted": False, "provider_calls": 0,
        "memory_docs_submitted": False, "design_generalization_established": False,
        "required_next_evidence": ["actual baseline target FAIL and compiled regression PASS",
            "verified training repair PASS", "repeated failure gap and non-P12 admission",
            "source-only frozen asset binding", "new Asset and Knowledge in disposable staging",
            "independent heldout/remove-delta/non-target executions and rollback"]}
    report["report_digest"] = _digest(report)
    _write(output, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--p14-attribution", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = prepare_gap_inputs(args.preregistration, args.p14_attribution, output=args.output)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"report_digest": report["report_digest"], "status": "INPUTS_FROZEN_NOT_GAP_EVIDENCE"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
