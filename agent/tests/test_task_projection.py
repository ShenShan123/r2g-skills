"""Model projections must fit the existing budget without losing authority.

The regression reflects the real-model R1 failure: repeated task conditions
and refusals consumed the same 64k envelope as already persisted tool evidence.
No model, external source or physical tool is used by these tests.
"""
from copy import deepcopy
import json

import pytest

from r2g_agent import task_runtime
from r2g_agent.config import ModelConfig
from r2g_agent.context_state import ContextState
from r2g_agent.model_context import ContextBudgetError, prepare_context


def serialized(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def snapshot():
    conditions = [{"id": "design:top:" + name, "description": "Independent evidence required for " + name,
                   "design": "top", "verdict": "UNKNOWN", "source": "host:validation.review",
                   "refs": [{"path": "/isolated/project/" + "retained-original-evidence/" * 7 + str(i),
                             "sha256": str(i) * 64} for i in range(4)],
                   "reasons": ["missing_current_evidence:" + name]}
                  for name in ("final_result", "quantitative", "technical", "dataset", "delivery")]
    conditions.insert(0, {"id": "job_execution", "description": "All designs completed", "verdict": "FAIL",
                          "source": "jobs.sqlite", "refs": [{"job_id": "job-fixture"}],
                          "reasons": ["job_not_fully_successful"]})
    allowed = {"id": "top:review", "design": "top", "tool": "review_evidence",
               "arguments": {"job_id": "job-fixture", "design": "top"}, "allowed": True,
               "reasons": ["verify_real_final_and_quantity_and_technical_evidence"],
               "refusals": [], "preconditions": [], "checks": ["three_layers"],
               "requires_host_revalidation": False}
    refused = [{"id": "top:refused-" + str(i), "design": "top", "tool": "execute_rtl_repair",
                "arguments": {"proposal_id": "not-approved"}, "allowed": False,
                "refusals": ["host_approval_required", "remaining_evidence_unknown"],
                "preconditions": ["original_source_version_and_scope_required:" + "detail" * 40],
                "checks": ["independent_functional_and_full_flow_validation_required"],
                "reasons": ["cannot_modify_rtl_or_waive_acceptance"]} for i in range(12)]
    return {"task": {"schema_version": 1, "id": "task-fixture", "job_id": "job-fixture",
                     "goal": "Deliver the graph dataset without changing RTL or constraints.",
                     "constraints": ["Preserve RTL", "UNKNOWN is not PASS"], "profile": "dataset_delivery",
                     "phase": "signoff", "status": "blocked", "conditions": conditions,
                     "pending_evidence": deepcopy(conditions), "omitted_conditions": 0,
                     "omitted_pending_evidence": 0, "read_with": {"tool": "task_status", "arguments": {}}},
            "stage_actions": {"schema_version": 1, "job_id": "job-fixture", "job_status": "failed",
                              "designs": [{"design": "top", "stage": "signoff", "failure_kind": "physical_evidence_unresolved"}],
                              "candidates": [allowed, *refused], "refusals": [],
                              "omitted": {"designs": 0, "candidates": 0}},
            "budget": {"remaining_model_calls": 1, "remaining_actions": 6, "remaining_recovery_attempts": 0}}


def test_model_projection_is_bounded_and_cannot_mutate_full_task_ledger():
    full = snapshot()
    original = deepcopy(full)
    view = task_runtime.model_view(full, max_chars=5000)
    assert len(serialized(view)) <= 5000
    assert full == original
    text = serialized(view)
    assert 'UNKNOWN' in text and 'FAIL' in text
    assert 'PASS' not in text or 'UNKNOWN is not PASS' in text
    assert 'host_approval_required' in text
    assert view.get('budget') == full['budget']
    # A legal action retains executable, scoped arguments. Compacting a view
    # cannot silently convert the refused RTL action into an allowed action.
    assert 'review_evidence' in text and 'job-fixture' in text
    assert 'task_status' in text
    if 'stage_actions' in view and isinstance(view['stage_actions'], dict):
        candidates = view['stage_actions'].get('candidates', [])
        assert not any(candidate.get('tool') == 'execute_rtl_repair' and candidate.get('allowed') is True
                       for candidate in candidates)


def test_same_64k_context_accepts_compact_task_view_and_preserves_full_tool_pairs():
    full = snapshot()
    current = [{"role": "user", "content": "Continue the existing task using current evidence."},
               {"role": "assistant", "content": "Read the actual report.", "tool_calls": [{
                   "id": "report-call", "name": "read_log", "arguments": {"job_id": "job-fixture"}}]},
               {"role": "tool", "tool_call_id": "report-call", "name": "read_log",
                "content": serialized({"raw_observation": "E" * 47000})}]
    archived = deepcopy(current)
    policy = "Unchanged host policy.\n" * 300
    state = ContextState(goal="Deliver graph dataset; preserve the original RTL and constraints.",
                         constraints=("No test oracle or PDK waivers.",),
                         pending_actions=({"id": "uncertain-action", "status": "recovery_required", "tool_name": "start_job"},),
                         approvals=({"status": "not_granted", "scope": "RTL editing"},),
                         source={"store": "test-controller", "turn_id": "turn-fixture"})
    mc = ModelConfig(context_max_chars=64000)
    with pytest.raises(ContextBudgetError):
        prepare_context(current, None, mc, [], policy + serialized(full), state=state)
    compact = task_runtime.model_view(full, max_chars=5000)
    messages, context = prepare_context(current, None, mc, [], policy + serialized(compact), state=state)
    assert context['context_chars'] <= 64000
    assert current == archived
    assert messages[-1]['content'] == current[-1]['content']
    assert messages[-2]['tool_calls'][0]['id'] == messages[-1]['tool_call_id'] == 'report-call'
    protected = context['controller_state']
    assert protected['goal'] == state.goal and protected['constraints'] == list(state.constraints)
    assert protected['pending_actions'] == list(state.pending_actions)
    assert protected['approvals'] == list(state.approvals)


def test_projection_counts_details_already_omitted_by_the_stage_policy():
    full = snapshot()
    full['stage_actions']['omitted'] = {'designs': 9, 'candidates': 17}
    compact = task_runtime.model_view(full)
    assert compact['omitted']['designs'] == 9
    assert compact['omitted']['candidates'] >= 17
    assert full['stage_actions']['omitted'] == {'designs': 9, 'candidates': 17}
