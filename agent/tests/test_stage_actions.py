"""Host-side EDA stage gates; these tests execute no EDA tool or model."""
from copy import deepcopy

import pytest

from r2g_agent.stage_actions import action_view, validate_action


def fixture(message="Unknown module type: child"):
    before = {"sha256": "a" * 64, "rtl_sha256": "b" * 64, "constraints_sha256": "c" * 64}
    job = {"id": "test-job", "status": "failed", "spec": {"platform": "nangate45", "output": "graph"},
        "designs": [{"name": "top", "status": "failed", "stage": "synth", "project": "/jobs/test/top",
            "detail": {"top": "top", "failed_stage": "synth", "input_bindings": {"synth": {
                "before": before, "after": dict(before), "unchanged": True, "exit_code": 1}}}}]}
    events = [{"id": 10, "job_id": job["id"], "design": "top", "stage": "synth", "kind": "fail",
               "message": message, "data": {}}]
    caps = {"inventory_id": "inventory-a", "platform": {"name": "nangate45", "agent_supported": True},
        "tools": {name: {"usable": True, "sha256": "d" * 64} for name in ("iverilog", "yosys", "vvp")}}
    budget = {"remaining_model_calls": 4, "remaining_actions": 8, "remaining_recovery_attempts": 1}
    return job, events, {"capabilities": caps, "budget": budget, "debug_authorized": True}


def candidate(view, tool):
    return next(c for c in view["candidates"] if c["tool"] == tool)


def test_missing_dependency_has_bounded_checks_and_one_host_authorized_recovery():
    job, events, host = fixture()
    view = action_view(job, events, **host)
    assert view["designs"][0]["failure_kind"] == "missing_dependency"
    action = candidate(view, "execute_dependency_debug")
    assert action["allowed"]
    assert action["arguments"] == {"job_id": "test-job", "design": "top", "evidence_event_id": 10}
    assert "immutable_source_and_constraints" in action["preconditions"]
    assert "three_layer_review_before_delivery" in action["checks"]
    assert validate_action(action["tool"], action["arguments"], job, events, **host)["allowed"]


@pytest.mark.parametrize("defect,reason", [
    ("not_authorized", "job_scoped_dependency_debug_authorization_required"),
    ("no_budget", "explicit_dependency_debug_budget_required"),
    ("attempts_zero", "budget_exhausted_or_invalid:remaining_recovery_attempts"),
    ("attempts_bool", "budget_exhausted_or_invalid:remaining_recovery_attempts"),
    ("actions_zero", "budget_exhausted_or_invalid:remaining_actions"),
    ("binding_changed", "failure_source_binding_missing_changed_or_inconsistent"),
    ("binding_missing", "failure_source_binding_missing_changed_or_inconsistent"),
    ("exit_bool", "failure_source_binding_missing_changed_or_inconsistent"),
    ("tool_missing", "current_diagnostic_tool_identity_required:iverilog"),
    ("tool_unbound", "current_diagnostic_tool_identity_required:yosys"),
    ("platform_mismatch", "current_supported_platform_identity_required"),
    ("source_missing", "job_project_and_top_required"),
])
def test_dependency_recovery_fails_closed_on_unknown_authorization_budget_and_input(defect, reason):
    job, events, host = fixture()
    detail = job["designs"][0]["detail"]
    if defect == "not_authorized": host["debug_authorized"] = False
    elif defect == "no_budget": host.pop("budget")
    elif defect == "attempts_zero": host["budget"]["remaining_recovery_attempts"] = 0
    elif defect == "attempts_bool": host["budget"]["remaining_recovery_attempts"] = True
    elif defect == "actions_zero": host["budget"]["remaining_actions"] = 0
    elif defect == "binding_changed": detail["input_bindings"]["synth"]["after"]["sha256"] = "e" * 64
    elif defect == "binding_missing": detail.pop("input_bindings")
    elif defect == "exit_bool": detail["input_bindings"]["synth"]["exit_code"] = True
    elif defect == "tool_missing": host["capabilities"]["tools"]["iverilog"]["usable"] = False
    elif defect == "tool_unbound": host["capabilities"]["tools"]["yosys"].pop("sha256")
    elif defect == "platform_mismatch": host["capabilities"]["platform"]["name"] = "sky130hd"
    elif defect == "source_missing": job["designs"][0].pop("project")
    action = candidate(action_view(job, events, **host), "execute_dependency_debug")
    assert not action["allowed"]
    assert reason in action["refusals"]
    assert not validate_action(action["tool"], action["arguments"], job, events, **host)["allowed"]


def test_current_phase_and_own_latest_failure_determine_diagnosis():
    job, events, host = fixture()
    # Neither another job's event nor a newer old-stage failure determines this task.
    events += [dict(events[0], id=100, job_id="other", message="syntax error"),
               dict(events[0], id=101, stage="flow", message="Permission denied")]
    view = action_view(job, events, **host)
    assert view["designs"][0]["failure_kind"] == "missing_dependency"
    events.append(dict(events[0], id=11, message="syntax error"))
    view = action_view(job, list(reversed(events)), **host)
    assert view["designs"][0]["failure_kind"] == "syntax"
    assert not any(c["tool"] == "execute_dependency_debug" for c in view["candidates"])


@pytest.mark.parametrize("status", ["running", "pending", "finalizing", "recovery_required", "cancelling", "cancelled"])
def test_live_cancelled_and_uncertain_execution_has_no_new_diagnostic_or_mutation(status):
    job, events, host = fixture()
    job["status"] = status
    view = action_view(job, events, **host)
    assert {c["tool"] for c in view["candidates"]} <= {"job_status", "design_detail", "read_log"}
    assert not validate_action("check_design", {"path": "/jobs/test/top/rtl", "top": "top", "check": "compile"},
                               job, events, **host)["allowed"]


def test_environment_takes_precedence_over_syntax_or_missing_module_messages():
    job, events, host = fixture("Unknown module child; syntax error; Permission denied")
    view = action_view(job, events, **host)
    assert view["designs"][0]["failure_kind"] == "environment"
    assert candidate(view, "check_environment")["allowed"]
    assert not any(c["tool"] in {"check_design", "propose_rtl_repair", "execute_dependency_debug"}
                   for c in view["candidates"])


@pytest.mark.parametrize("verdict", ["UNKNOWN", "STALE"])
def test_physical_unresolved_evidence_is_not_an_input_repair_or_a_pass(verdict):
    job, events, host = fixture()
    record = job["designs"][0]
    record["stage"] = record["detail"]["failed_stage"] = "signoff"
    record["detail"]["validation"] = {"verdict": verdict}
    events[0].update(stage="signoff", message="LVS final result MATCH, must-connect unresolved")
    view = action_view(job, events, **host)
    assert view["designs"][0]["failure_kind"] == "physical_evidence_unresolved"
    assert candidate(view, "review_evidence")["allowed"]
    assert candidate(view, "diagnose")["allowed"]
    for tool in ("check_design", "start_job", "execute_dependency_debug", "execute_rtl_repair"):
        assert not validate_action(tool, {}, job, events, **host)["allowed"]


def test_functional_counterexample_has_no_unregistered_repair():
    job, events, host = fixture("output mismatch: expected 3 observed 2")
    record = job["designs"][0]
    record["stage"] = record["detail"]["failed_stage"] = "functional_check"
    events[0]["stage"] = "functional_check"
    view = action_view(job, events, **host)
    assert view["designs"][0]["failure_kind"] == "functional"
    assert not any(c["tool"] in {"check_design", "propose_rtl_repair", "execute_rtl_repair", "execute_dependency_debug"}
                   for c in view["candidates"])


def test_diagnostic_arguments_are_bound_to_host_project_top_stage_and_tool_identity():
    job, events, host = fixture()
    exact = {"path": "/jobs/test/top/rtl", "top": "top", "check": "compile"}
    assert validate_action("check_design", exact, job, events, **host)["allowed"]
    for arguments in ({**exact, "path": "/tmp/other"}, {**exact, "top": "other"},
                       {**exact, "flags": "-i"}, {**exact, "check": "simulate"}):
        assert not validate_action("check_design", arguments, job, events, **host)["allowed"]
    host["capabilities"]["tools"]["iverilog"].pop("sha256")
    assert not validate_action("check_design", exact, job, events, **host)["allowed"]


def test_normal_read_tools_survive_stage_gating_but_job_and_design_scopes_hold():
    job, events, host = fixture()
    host["budget"]["remaining_actions"] = 0
    assert validate_action("read_log", {"job_id": "test-job", "design": "top", "stage": "synth"}, job, events, **host)["allowed"]
    assert not validate_action("read_log", {"job_id": "other", "design": "top"}, job, events, **host)["allowed"]
    assert not validate_action("read_log", {"job_id": "test-job", "design": "unknown"}, job, events, **host)["allowed"]
    assert not validate_action("run_shell", {"command": "true"}, job, events, **host)["allowed"]
    assert not validate_action("start_job", {}, job, events, **host)["allowed"]


def test_counter8_registered_semicolon_keeps_grant_boundary():
    job, events, host = fixture("syntax error, unexpected TOK_ELSE")
    job["designs"][0]["detail"]["top"] = "counter8"
    view = action_view(job, events, **host)
    assert candidate(view, "propose_rtl_repair")["allowed"]
    assert not candidate(view, "execute_rtl_repair")["allowed"]
    host["grant_id"] = "host-validated-grant"
    view = action_view(job, events, **host)
    assert candidate(view, "execute_rtl_repair")["allowed"]
    assert validate_action("execute_rtl_repair", {"proposal_id": "host-owned-proposal"}, job, events, **host)["allowed"]
    # A grant never turns arbitrary syntax repair into a registered action.
    job["designs"][0]["detail"]["top"] = "uart"
    assert not candidate(action_view(job, events, **host), "execute_rtl_repair")["allowed"]


def test_view_is_bounded_stable_and_does_not_mutate_host_records():
    job, events, host = fixture()
    before = deepcopy((job, events, host))
    view = action_view(job, events, **host)
    assert (job, events, host) == before
    assert action_view(job, events, **host) == view
    host["budget"]["remaining_recovery_attempts"] = 0
    assert action_view(job, events, **host)["observation_id"] != view["observation_id"]
    host["budget"]["remaining_recovery_attempts"] = 1
    binding = job["designs"][0]["detail"]["input_bindings"]["synth"]
    binding["before"]["sha256"] = binding["after"]["sha256"] = "e" * 64
    assert action_view(job, events, **host)["observation_id"] != view["observation_id"]
