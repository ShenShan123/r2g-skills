"""Evidence/context boundaries with synthetic history and durable job fixtures."""
import copy
import json

import pytest

from r2g_agent.config import ModelConfig
from r2g_agent.context_state import ContextState
from r2g_agent.jobs import JobStore
from r2g_agent.model_context import ContextBudgetError, prepare_context


def exchange(number, job_id=None):
    name = "job_status" if job_id else "list_examples"
    return [{"role": "user", "content": f"request {number}"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": f"call-{number}", "name": name,
                 "arguments": {"job_id": job_id} if job_id else {}}]},
            {"role": "tool", "name": name, "tool_call_id": f"call-{number}",
             "content": json.dumps({"job_id": job_id} if job_id else {"examples": ["counter"]})},
            {"role": "assistant", "content": "observed"}]


def runtime(view):
    return next(json.loads(m["content"].split("\n")[-1]) for m in view
                if m.get("content", "").startswith("【运行时上下文"))


def test_middle_user_constraint_survives_history_compaction_exactly():
    history = [m for i in range(18) for m in exchange(i)]
    constraint = "DO_NOT_CHANGE_INTERFACE_OR_TESTBENCH"
    history[0]["content"] = "synthetic prefix " * 90 + constraint + " synthetic suffix" * 90
    # A later constraint must also survive even if its turn is not indexed.
    history[4]["content"] = "Do not rerun EDA until I approve the candidate diff."
    history.append({"role": "user", "content": "What is the current result?"})
    before = copy.deepcopy(history)
    view, manifest = prepare_context(history, None, ModelConfig(context_max_chars=32000, context_recent_turns=1), [], "policy")
    assert constraint in json.dumps(view)
    assert history[4]["content"] in json.dumps(view)
    assert history == before
    assert manifest["context_chars"] <= 32000


def test_active_job_is_not_evicted_by_eight_later_terminal_failures():
    store = JobStore()
    active = store.create_job("active synthetic task", {}, [{"name": "new_top"}])
    store.set_job(active, "running")
    history = exchange(0, active)
    for i in range(1, 11):
        jid = store.create_job(f"old failure {i}", {}, [{"name": "old_top"}])
        store.set_job(jid, "failed")
        history.extend(exchange(i, jid))
    history.append({"role": "user", "content": "Continue the active task."})
    view, _ = prepare_context(history, store, ModelConfig(context_max_chars=32000), [], "policy")
    data = runtime(view)
    assert data["related_jobs"][0]["job_id"] == active
    assert data["primary_job_id"] == active
    assert len(data["related_jobs"]) <= 8
    assert data["omitted_job_count"] == 3


def test_old_failure_evidence_is_not_current_job_evidence():
    store = JobStore()
    old = store.create_job("old synthetic task", {}, [{"name": "top"}])
    store.set_design(old, "top", status="failed", detail={"reason": "OLD_JOB_ONLY_FAILURE"})
    store.set_job(old, "failed")
    current = store.create_job("new synthetic task", {}, [{"name": "top"}])
    store.set_job(current, "running")
    history = [*exchange(0, old), *exchange(1, current), {"role": "user", "content": "current progress?"}]
    view, _ = prepare_context(history, store, None, [], "policy")
    data = runtime(view)
    assert data["primary_job_id"] == current
    previous = next(j for j in data["related_jobs"] if j["job_id"] == old)
    assert previous["scope"] == "historical_reference"
    assert "OLD_JOB_ONLY_FAILURE" not in json.dumps(previous)
    assert "read_with" in previous


def test_latest_complete_tool_group_survives_recent_turn_limit():
    history = [*exchange(0), {"role": "user", "content": "Continue using the evidence just read."}]
    view, manifest = prepare_context(history, None, ModelConfig(context_recent_turns=1), [], "policy")
    assert any(m.get("tool_call_id") == "call-0" for m in view)
    assert manifest["retained_tool_group_ranges"] == [[1, 3]]


@pytest.mark.parametrize("tail", [[], [{"role": "tool", "tool_call_id": "different", "name": "list_examples", "content": "{}"}]])
def test_unresolved_tool_exchange_cannot_be_sent_to_provider(tail):
    history = exchange(0)[:2] + tail
    with pytest.raises(ContextBudgetError, match="工具"):
        prepare_context(history, None, None, [], "policy")


def test_controller_goal_constraints_pending_actions_and_approvals_are_protected():
    history = [m for i in range(15) for m in exchange(i)] + [{"role": "user", "content": "continue"}]
    state = ContextState(
        goal="Deliver the current synthetic design with the public interface unchanged.",
        constraints=("Do not alter timing constraints or test expectations.",),
        pending_actions=({"id": "action-pending", "state": "accepted", "snapshot_id": "snapshot-new",
                          "tool_name": "apply_candidate", "public_reason": "Awaiting durable execution"},),
        approvals=({"id": "approval-pending", "status": "pending", "action_id": "action-pending",
                    "scope": "rtl/counter.sv"},),
        source={"store": "sessions.sqlite", "turn_id": "turn-current", "version": 3})
    original = copy.deepcopy(state)
    view, manifest = prepare_context(history, None, ModelConfig(context_recent_turns=1), [], "policy", state=state)
    data = runtime(view)
    assert data["current_goal"]["text"] == state.goal
    assert data["controller_state"]["constraints"] == list(state.constraints)
    assert data["controller_state"]["pending_actions"] == list(state.pending_actions)
    assert data["controller_state"]["approvals"] == list(state.approvals)
    assert manifest["controller_state"] == data["controller_state"]
    assert state == original
    assert "not model tokens" in manifest["budget_unit"]


def test_pending_action_that_cannot_fit_stops_without_silent_truncation():
    state = ContextState(pending_actions=({"id": "pending", "arguments": {"patch": "x" * 12000}},),
                         source={"store": "sessions.sqlite", "turn_id": "synthetic-turn"})
    with pytest.raises(ContextBudgetError, match="待执行动作"):
        prepare_context([{"role": "user", "content": "continue"}], None,
                        ModelConfig(context_max_chars=8192), [], "policy", state=state)


def test_oversized_older_user_constraints_cannot_disappear_into_an_index():
    history = [{"role": "user", "content": "exact older constraint " * 800},
               {"role": "assistant", "content": "understood"},
               {"role": "user", "content": "next"}]
    with pytest.raises(ContextBudgetError, match="用户要求"):
        prepare_context(history, None, ModelConfig(context_max_chars=8192, context_recent_turns=1), [], "policy")


def test_durable_context_can_bind_current_job_without_transcript_inference():
    store = JobStore()
    jid = store.create_job("current synthetic job", {}, [{"name": "top"}])
    private = store.create_job("unrelated synthetic job", {}, [{"name": "private"}])
    state = ContextState(active_job_id=jid, source={"store": "sessions.sqlite", "turn_id": "turn-job"})
    view, _ = prepare_context([{"role": "user", "content": "continue"}], store, None, [], "policy", state=state)
    data = runtime(view)
    assert data["primary_job_id"] == jid
    assert [j["job_id"] for j in data["related_jobs"]] == [jid]
    assert private not in json.dumps(data)
    assert data["related_jobs"][0]["source_context"] == state.source


def test_unproven_controller_state_is_not_accepted_as_authority():
    with pytest.raises(ContextBudgetError, match="来源"):
        prepare_context([{"role": "user", "content": "continue"}], None, None, [], "policy",
                        state=ContextState(approvals=({"status": "approved"},)))


def test_parallel_tool_results_are_preserved_as_one_complete_group():
    history = [{"role": "user", "content": "read both synthetic sources"},
               {"role": "assistant", "content": "", "tool_calls": [
                   {"id": "a", "name": "read_rtl", "arguments": {}},
                   {"id": "b", "name": "read_log", "arguments": {}}]},
               {"role": "tool", "name": "read_log", "tool_call_id": "b", "content": "{}"},
               {"role": "tool", "name": "read_rtl", "tool_call_id": "a", "content": "{}"},
               {"role": "user", "content": "continue"}]
    view, manifest = prepare_context(history, None, ModelConfig(context_recent_turns=1), [], "policy")
    assert [m.get("tool_call_id") for m in view if m["role"] == "tool"] == ["b", "a"]
    assert manifest["retained_tool_group_ranges"] == [[1, 4]]


def test_internal_review_prompt_is_not_pinned_as_a_user_requirement():
    history = [{"role": "user", "content": "Keep the interface unchanged.", "turn_kind": "user"},
               {"role": "assistant", "content": "understood"},
               {"role": "user", "content": "INTERNAL_REVIEW_PROMPT", "turn_kind": "job_review"},
               {"role": "assistant", "content": "review finished"},
               {"role": "user", "content": "status?", "turn_kind": "user"}]
    view, _ = prepare_context(history, None, ModelConfig(context_recent_turns=1), [], "policy")
    records = runtime(view)["protected_user_inputs"]["records"]
    assert [r["text"] for r in records] == ["Keep the interface unchanged."]


def test_bound_completed_job_review_is_not_hijacked_by_an_older_active_job():
    store = JobStore()
    older = store.create_job("older still-running task", {}, [{"name": "old"}])
    store.set_job(older, "running")
    current = store.create_job("finished task under review", {}, [{"name": "new"}])
    store.set_design(current, "new", stage="done", status="passed", detail={"verify": {"passed": 3, "total": 3}})
    store.set_job(current, "done")
    state = ContextState(active_job_id=current, source={"store": "sessions.sqlite", "turn_id": "bound-review",
                                                       "turn_kind": "job_review"})
    history = [*exchange(0, older), {"role": "user", "content": "Review the completed task.", "turn_kind": "job_review"}]
    view, _ = prepare_context(history, store, None, [], "policy", state=state)
    data = runtime(view)
    assert data["primary_job_id"] == current
    assert data["related_jobs"][0]["job_id"] == current
    assert data["related_jobs"][0]["designs"][0]["verify"] == {"passed": 3, "total": 3}
    assert data["related_jobs"][1]["scope"] == "active_related_job"
