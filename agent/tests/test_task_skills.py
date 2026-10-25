"""Synthetic task selection regressions; no tool, model, EDA or production I/O."""
from copy import deepcopy
import hashlib
import json

import pytest

from r2g_agent import task_skills as skills


def fixture():
    snapshot = {"sha256": "a" * 64, "rtl_sha256": "b" * 64, "constraints_sha256": "c" * 64}
    job = {"id": "synthetic-job", "status": "failed", "spec": {"platform": "nangate45", "output": "graph"},
           "designs": [{"name": "counter8", "status": "failed", "stage": "synth", "detail": {
               "failed_stage": "synth", "top": "counter8", "input_bindings": {"synth": {
                   "before": snapshot, "after": dict(snapshot), "unchanged": True, "exit_code": 2}}}}]}
    events = [{"id": 1, "job_id": job["id"], "design": "counter8", "stage": "synth", "kind": "fail",
               "message": "counter8.v:15 syntax error, unexpected TOK_ELSE", "data": {}}]
    caps = {"inventory_id": "synthetic-inventory", "platform": {"name": "nangate45", "agent_supported": True},
            "tools": {name: {"status": "present", "usable": True} for name in ("yosys", "iverilog", "vvp", "openroad", "klayout")}}
    return job, events, caps


def candidate(result, name):
    return next(item for item in result["candidates"] if item["skill_id"] == name)


def delivery():
    job, events, caps = fixture()
    job.update(status="done", result={"package": "/synthetic/unchecked-archive.tar.gz"})
    record = job["designs"][0]
    record.update(status="passed", stage="done")
    record["detail"].update(verify={"passed": 297, "total": 297}, signoff={
        "drc": "clean", "lvs": "clean", "dual_clean": True, "drc_violations": 0, "lvs_mismatches": 0})
    events.append({"id": 2, "job_id": job["id"], "design": "counter8", "stage": "verify", "kind": "ok",
                   "message": "measured verifier observation", "data": {"checks": 297}})
    return job, events, caps


def test_manifest_is_small_and_only_one_registered_repair_contract():
    manifest = skills.list_skills()
    assert len(manifest["skills"]) == 6
    assert len(json.dumps(manifest)) < 5000
    assert all("required_evidence" not in item for item in manifest["skills"])
    for item in manifest["skills"]:
        contract = skills.load_skill(item["id"])
        assert contract["execution_authorized"] is False
        assert contract["required_inputs"] and contract["required_evidence"] and contract["stop_or_escalate"]
        if item["id"] == "compile_failure":
            assert contract["executable_action"]["id"] == "rtl_insert_semicolon"
            assert contract["executable_action"]["host_approval_required"]
        else:
            assert contract["execution_mode"] == "advisory_only"
            assert contract["executable_action"] is None and contract["allowed_modifications"] == []


@pytest.mark.parametrize("name", ["../prompts/system", "/etc/passwd", "manifest", "unknown", "compile_failure.json"])
def test_contract_load_is_allowlisted(name):
    with pytest.raises(ValueError, match="unknown"):
        skills.load_skill(name)


def test_loaded_contracts_and_inputs_are_not_mutated():
    contract = skills.load_skill("compile_failure")
    contract["executable_action"]["host_approval_required"] = False
    assert skills.load_skill("compile_failure")["executable_action"]["host_approval_required"] is True
    job, events, caps = fixture()
    before = deepcopy((job, events, caps))
    result = skills.select_tasks(job, events, capabilities=caps)
    assert (job, events, caps) == before
    chosen = candidate(result, "compile_failure")
    assert chosen["decision"] == "eligible_for_proposal"
    assert chosen["execution_authorized"] is False
    assert chosen["evidence_event_ids"] == [1]
    assert "explicit_approval_and_remaining_budget" in chosen["pending_host_checks"]
    assert all(item["tool"] != "execute_rtl_repair" for item in chosen["recommended_next_checks"])


@pytest.mark.parametrize("defect", ["different_top", "image_output", "missing_binding", "binding_changed",
    "unknown_constraint_binding", "zero_exit", "infrastructure", "missing_oracle", "present_not_usable",
    "unsupported_platform", "different_platform", "missing_capabilities", "boolean_exit"])
def test_narrow_candidate_refuses_unmet_or_unknown_preconditions(defect):
    job, events, caps = fixture()
    detail = job["designs"][0]["detail"]
    binding = detail["input_bindings"]["synth"]
    if defect == "different_top": detail["top"] = "uart"
    elif defect == "image_output": job["spec"]["output"] = "image"
    elif defect == "missing_binding": detail.pop("input_bindings")
    elif defect == "binding_changed": binding["after"]["sha256"] = "d" * 64
    elif defect == "unknown_constraint_binding":
        binding["before"].pop("constraints_sha256"); binding["after"].pop("constraints_sha256")
    elif defect == "zero_exit": binding["exit_code"] = 0
    elif defect == "boolean_exit": binding["exit_code"] = True
    elif defect == "infrastructure": events[0]["message"] += "; Permission denied"
    elif defect == "missing_oracle": caps["tools"].pop("iverilog")
    elif defect == "present_not_usable": caps["tools"]["vvp"]["usable"] = False
    elif defect == "unsupported_platform": caps["platform"]["agent_supported"] = False
    elif defect == "different_platform": caps["platform"]["name"] = "asap7"
    elif defect == "missing_capabilities": caps = None
    chosen = candidate(skills.select_tasks(job, events, capabilities=caps), "compile_failure")
    assert chosen["decision"] == "advisory"
    assert chosen["refusals"] and "proposed_action" not in chosen


@pytest.mark.parametrize("field,value", [("job_id", "other-job"), ("design", "another-design"), ("id", True)])
def test_unrelated_or_invalid_event_cannot_authorize_candidate(field, value):
    job, events, caps = fixture()
    events[0][field] = value
    result = skills.select_tasks(job, events, capabilities=caps)
    assert not any(c["decision"] == "eligible_for_proposal" for c in result["candidates"])
    assert candidate(result, "synthesis_diagnosis")["refusals"] == ["matching_failure_event_missing"]


def test_newer_infrastructure_failure_is_not_diagnosed_from_old_syntax_event():
    job, events, caps = fixture()
    events.append({**events[0], "id": 2, "message": "No space left on device"})
    result = skills.select_tasks(job, events, capabilities=caps)
    assert [c["skill_id"] for c in result["candidates"]] == ["synthesis_diagnosis"]
    assert result["candidates"][0]["evidence_event_ids"] == [2]


@pytest.mark.parametrize("status,decision", [("running", "wait"), ("finalizing", "wait"),
    ("cancelling", "abstain"), ("cancelled", "abstain"), ("recovery_required", "abstain")])
def test_nonterminal_or_uncertain_job_never_recommends_new_execution(status, decision):
    job, events, caps = fixture()
    job["status"] = status
    result = skills.select_tasks(job, events, capabilities=caps)
    assert all(c["decision"] == decision for c in result["candidates"])
    assert all(c["execution_authorized"] is False for c in result["candidates"])


@pytest.mark.parametrize("task,stage,message,detail", [
    ("simulation_mismatch", "simulate", "state mismatch at cycle 12", {}),
    ("timing_violations", "signoff", "setup violation on measured path", {"timing": {"wns": -0.4}}),
    ("lvs_mismatch", "signoff", "LVS mismatch", {"signoff": {"lvs": "mismatch", "lvs_mismatches": 2}}),
])
def test_general_diagnoses_are_advisory_and_have_no_modification_action(task, stage, message, detail):
    job, events, caps = fixture()
    record = job["designs"][0]
    record["stage"] = stage
    record["detail"].update(detail, failed_stage=stage)
    events[0].update(stage=stage, message=message)
    chosen = candidate(skills.select_tasks(job, events, capabilities=caps), task)
    assert chosen["decision"] == "advisory" and chosen["execution_mode"] == "advisory_only"
    assert "proposed_action" not in chosen


def test_delivery_uses_measured_297_count_and_does_not_claim_archive_verified():
    job, events, caps = delivery()
    chosen = candidate(skills.select_tasks(job, events, capabilities=caps), "dataset_delivery")
    assert chosen["decision"] == "verify_delivery"
    assert chosen["observed"]["stage"] == "done"  # Old failed_stage cannot override terminal state.
    assert chosen["observed"]["reported_dataset_checks"] == {
        "passed": 297, "total": 297, "source": "job.design.detail.verify"}
    assert "archive_members_and_hashes" in chosen["pending_host_checks"]
    assert chosen["execution_authorized"] is False
    assert "compile_failure" not in [c["skill_id"] for c in skills.select_tasks(job, events)["candidates"]]


@pytest.mark.parametrize("defect", ["empty", "missing", "failed", "boolean", "count_conflict", "later_failure",
    "missing_event", "unknown_lvs", "conflicting_lvs_count", "no_package"])
def test_delivery_missing_failed_or_conflicting_evidence_never_becomes_ready(defect):
    job, events, caps = delivery()
    detail = job["designs"][0]["detail"]
    if defect == "empty": detail["verify"] = {"passed": 0, "total": 0}
    elif defect == "missing": detail.pop("verify")
    elif defect == "failed": detail["verify"]["passed"] = 296
    elif defect == "boolean": detail["verify"] = {"passed": True, "total": True}
    elif defect == "count_conflict": events[-1]["data"]["checks"] = 296
    elif defect == "later_failure": events.append({**events[-1], "id": 3, "kind": "fail"})
    elif defect == "missing_event": events = []
    elif defect == "unknown_lvs": detail["signoff"]["lvs"] = "UNKNOWN"
    elif defect == "conflicting_lvs_count": detail["signoff"]["lvs_mismatches"] = 1
    elif defect == "no_package": job.pop("result")
    chosen = candidate(skills.select_tasks(job, events, capabilities=caps), "dataset_delivery")
    assert chosen["decision"] == "advisory" and chosen["refusals"]


def test_bounded_selection_and_on_demand_design_filter():
    job, events, caps = fixture()
    job["designs"] = [{**deepcopy(job["designs"][0]), "name": "d" + str(i)} for i in range(100)]
    events = [{**events[0], "id": i + 1, "design": "d" + str(i % 100)} for i in range(1000)]
    result = skills.select_tasks(job, events, capabilities=caps)
    assert result["omitted"]["designs"] == 92 and result["omitted"]["events"] == 800
    assert len(result["candidates"]) <= 16 and len(json.dumps(result, ensure_ascii=False)) <= skills.MAX_OUTPUT_CHARS
    one = skills.select_tasks(job, events, capabilities=caps, design="d99")
    assert {c["design"] for c in one["candidates"]} == {"d99"}
    assert skills.select_tasks(job, events, design="not-found")["refusals"] == ["design_not_found"]


def test_no_nan_or_unknown_metric_can_be_interpreted_as_measured_violation():
    job, events, caps = fixture()
    record = job["designs"][0]
    record.update(stage="signoff", detail={"failed_stage": "signoff", "timing": {"wns": float("nan")},
                                         "signoff": {"lvs_mismatches": "UNKNOWN"}})
    events = []
    result = skills.select_tasks(job, events, capabilities=caps)
    assert result["candidates"] == []
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("skill,check,tool", [("compile_failure", "compile", "iverilog"),
    ("synthesis_diagnosis", "synthesis_structure", "yosys")])
@pytest.mark.parametrize("stage", ["input", "prepare", "compile", "synth"])
def test_diagnostic_contract_allows_only_bound_read_only_check(skill, check, tool, stage):
    caps = {"tools": {tool: {"usable": True, "sha256": "c" * 64}}}
    before = deepcopy(caps)
    result = skills.validate_diagnostic(skill, check, stage=stage, capabilities=caps)
    assert result["allowed"] is True and result["mutation_authorized"] is False
    assert result["refusals"] == [] and result["validated_tool_hashes"] == {tool: "c" * 64}
    contract_bytes = json.dumps(skills.load_skill(skill), ensure_ascii=False, sort_keys=True,
                                separators=(",", ":"), allow_nan=False).encode()
    assert result["contract_sha256"] == hashlib.sha256(contract_bytes).hexdigest()
    assert caps == before


@pytest.mark.parametrize("skill,check", [
    ("unknown", "compile"), ("../compile_failure", "compile"),
    ("compile_failure", "synthesis_structure"), ("synthesis_diagnosis", "compile"),
    ("compile_failure", "simulate"), ("simulation_mismatch", "compile"),
    ("timing_violations", "compile"), ("lvs_mismatch", "compile"), ("dataset_delivery", "compile"),
])
def test_advisory_or_wrong_contract_cannot_enable_diagnostic(skill, check):
    _, _, caps = fixture()
    result = skills.validate_diagnostic(skill, check, capabilities=caps)
    assert result["allowed"] is False and result["mutation_authorized"] is False and result["refusals"]


@pytest.mark.parametrize("stage", ["signoff", "verify", "done", "simulate", "UNKNOWN", "", None, ["input"]])
def test_diagnostic_wrong_stage_is_denied_even_with_verified_tool(stage):
    result = skills.validate_diagnostic("compile_failure", "compile", stage=stage,
        capabilities={"tools": {"iverilog": {"usable": True, "sha256": "a" * 64}}})
    assert not result["allowed"]
    assert "diagnostic_not_allowed_at_this_stage" in result["refusals"]


@pytest.mark.parametrize("tool", [None, {}, {"status": "present"}, {"usable": False, "sha256": "a" * 64},
    {"usable": 1, "sha256": "a" * 64}, {"usable": True}, {"usable": True, "sha256": "bad"},
    {"usable": True, "sha256": True}])
def test_diagnostic_missing_usable_identity_cannot_run(tool):
    result = skills.validate_diagnostic("compile_failure", "compile", capabilities={"tools": {"iverilog": tool}})
    assert not result["allowed"] and result["refusals"] and not result["validated_tool_hashes"]


@pytest.mark.parametrize("field,value", [("diagnostic_stages", {}), ("diagnostic_stages", {"compile": []}),
    ("diagnostic_required_tools", {}), ("diagnostic_required_tools", {"compile": []}),
    ("diagnostic_required_tools", {"compile": [None]})])
def test_incomplete_contract_policy_fails_closed(monkeypatch, field, value):
    contract = skills.load_skill("compile_failure")
    contract[field] = value
    monkeypatch.setattr(skills, "load_skill", lambda _: contract)
    result = skills.validate_diagnostic("compile_failure", "compile", capabilities={})
    assert not result["allowed"] and result["refusals"] == ["diagnostic_policy_incomplete"]


def test_check_recommendation_uses_host_project_and_top_and_does_not_authorize():
    job, events, caps = fixture()
    job["designs"][0]["project"] = "/synthetic/workspace/bound-project"
    result = skills.select_tasks(job, events, capabilities=caps)
    for skill, check in (("compile_failure", "compile"), ("synthesis_diagnosis", "synthesis_structure")):
        item = candidate(result, skill)
        diagnostics = [c for c in item["recommended_next_checks"] if c["tool"] == "check_design"]
        assert len(diagnostics) == 1
        assert diagnostics[0]["arguments"] == {
            "path": "/synthetic/workspace/bound-project/rtl", "top": "counter8", "check": check}
        assert item["execution_authorized"] is False
    job["designs"][0]["project"] = "relative-or-model-text"
    assert not any(c["tool"] == "check_design" for item in skills.select_tasks(job, events)["candidates"]
                   for c in item["recommended_next_checks"])
