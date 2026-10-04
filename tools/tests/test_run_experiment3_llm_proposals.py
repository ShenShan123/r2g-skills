import importlib.util
import json
import os
from pathlib import Path
import socket
from types import SimpleNamespace


REPO = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO / "tools/run_experiment3_llm_proposals.py"
SPEC = importlib.util.spec_from_file_location("run_experiment3_llm_proposals", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_conservative_request_tokens_uses_token_scale_not_raw_bytes():
    system = "s" * 2_000
    user = "u" * 18_000
    assert MODULE.conservative_request_tokens(system, user, 2_048) == 12_048


def test_extract_json_accepts_fenced_response():
    assert MODULE.extract_json('```json\n{"proposals": []}\n```') == {"proposals": []}


def test_endpoint_override_appends_chat_path(monkeypatch):
    monkeypatch.setenv("TEST_CODESUC_BASE", "https://gateway.example/v1")
    route = {
        "endpoint_env": "TEST_CODESUC_BASE",
        "endpoint": "https://fallback.invalid/v1/chat/completions",
        "api_style": "openai_chat",
    }
    assert MODULE.endpoint_for(route) == "https://gateway.example/v1/chat/completions"


def test_complete_endpoint_override_is_preserved(monkeypatch):
    endpoint = "https://gateway.example/v1/chat/completions"
    monkeypatch.setenv("TEST_CODESUC_BASE", endpoint)
    route = {
        "endpoint_env": "TEST_CODESUC_BASE",
        "endpoint": "https://fallback.invalid/v1/chat/completions",
        "api_style": "openai_chat",
    }
    assert MODULE.endpoint_for(route) == endpoint


def _proposal(model, fingerprint):
    return {
        "candidate_id": f"candidate-{model}-{fingerprint}",
        "candidate_version": 1,
        "source_model": model,
        "failure_domain": "setup_timing",
        "strategy": "test",
        "rationale": "test",
        "applicability": {"platform": "sky130hd", "required_failure_signatures": ["SETUP_TIMING"]},
        "config_edits": {"ABC_AREA": "0"},
        "action_policy_sha256": "a" * 64,
        "candidate_hash": fingerprint,
    }


def test_round_plan_gives_each_model_first_opportunity():
    proposals = [
        _proposal("gpt", "g1"),
        _proposal("gpt", "g2"),
        _proposal("claude", "c1"),
        _proposal("claude", "c2"),
        _proposal("qwen", "q1"),
        _proposal("qwen", "q2"),
    ]
    plan = MODULE.select_round_plan(proposals, set(), execution_limit=4, active_limit=8)
    selected_models = {item["candidate"]["source_model"] for item in plan["selected"]}
    assert selected_models == {"gpt", "claude", "qwen"}
    assert plan["selected_count"] == 4


def test_round_plan_respects_active_bank_limit():
    plan = MODULE.select_round_plan(
        [_proposal("gpt", "new1"), _proposal("claude", "new2")],
        {f"old-{index}" for index in range(7)},
        execution_limit=4,
        active_limit=8,
    )
    assert plan["selected_count"] == 1


def test_round_plan_does_not_repeat_rejected_attempt_but_frees_active_slot():
    proposals = [_proposal("gpt", "rejected"), _proposal("claude", "new")]
    plan = MODULE.select_round_plan(
        proposals,
        {f"active-{index}" for index in range(7)},
        attempted_hashes={"rejected"},
        execution_limit=4,
        active_limit=8,
    )
    assert [item["candidate"]["candidate_hash"] for item in plan["selected"]] == ["new"]
    assert plan["already_attempted_count"] == 1


def test_pure_llm_normalization_keeps_only_one_attempt():
    proposal = {
        "candidate_id": "timing-map",
        "strategy": "abc_timing_mapping",
        "rationale": "test",
        "applicability": {"platform": "sky130hd", "required_failure_signatures": ["SETUP_TIMING"]},
        "config_edits": {"ABC_AREA": "0"},
    }
    accepted, rejected = MODULE.normalize_proposals(
        {"proposals": [proposal, proposal | {"candidate_id": "second"}]},
        source_model="gpt",
        failure_domain="setup_timing",
        candidate_version=1,
        max_proposals=1,
    )
    assert len(accepted) == 1
    assert rejected == []


def test_normalization_accepts_nonsemantic_failure_pattern_alias():
    proposal = {
        "candidate_id": "antenna-drt",
        "strategy": "antenna_drt",
        "rationale": "test",
        "applicability": {
            "platform": "nangate45",
            "failure_patterns": ["DRC:*_ANTENNA"],
        },
        "config_edits": {"MAX_REPAIR_ANTENNAS_ITER_DRT": 10},
    }
    policy = REPO / "docs/experiments/experiment3/nangate45/action_policy.json"
    accepted, rejected = MODULE.normalize_proposals(
        {"proposals": [proposal]},
        source_model="gpt",
        failure_domain="antenna_drc",
        candidate_version=1,
        policy_path=policy,
    )
    assert rejected == []
    assert accepted[0]["applicability"]["required_failure_signatures"] == [
        "DRC:*_ANTENNA"
    ]
    assert accepted[0]["config_edits"]["MAX_REPAIR_ANTENNAS_ITER_DRT"] == "10"


def test_usage_token_count_normalizes_provider_shapes():
    assert MODULE.usage_token_count({"total_tokens": 41}) == 41
    assert MODULE.usage_token_count({"prompt_tokens": 11, "completion_tokens": 7}) == 18
    assert MODULE.usage_token_count({"input_tokens": 13, "output_tokens": 5}) == 18
    assert MODULE.usage_token_count({}) is None


def test_budget_ledger_accounts_completed_call(tmp_path):
    ledger = tmp_path / "gpt.json"
    assert MODULE.reserve_call(ledger, "call-1", 1000, 400) == "reserved"
    record = MODULE.complete_call(
        ledger,
        "call-1",
        1000,
        {"prompt_tokens": 40, "completion_tokens": 20},
        output=tmp_path / "proposal.json",
    )
    payload = json.loads(ledger.read_text())
    assert record["charged_tokens"] == 60
    assert payload["consumed_tokens"] == 60
    assert payload["reservations"] == {}
    assert MODULE.reserve_call(ledger, "call-1", 1000, 400) == "completed"


def test_budget_ledger_refuses_unsafe_reservation(tmp_path):
    ledger = tmp_path / "qwen.json"
    MODULE.reserve_call(ledger, "call-1", 500, 400)
    try:
        MODULE.reserve_call(ledger, "call-2", 500, 101)
    except MODULE.BudgetExhausted as exc:
        assert "would be exceeded" in str(exc)
    else:
        raise AssertionError("unsafe reservation was accepted")


def test_budget_ledger_reclaims_orphaned_reservation(tmp_path):
    ledger = tmp_path / "claude.json"
    MODULE.write_json(
        ledger,
        {
            "schema_version": "experiment3-token-budget-ledger-1.0",
            "created_at": MODULE.now(),
            "updated_at": MODULE.now(),
            "total_token_budget": 1000,
            "consumed_tokens": 0,
            "reservations": {
                "call-1": {
                    "reserved_at": MODULE.now(),
                    "reserved_tokens": 300,
                    "pid": 999999999,
                    "hostname": socket.gethostname(),
                }
            },
            "calls": {},
            "failures": [],
        },
    )
    assert MODULE.reserve_call(ledger, "call-1", 1000, 350) == "reserved"
    payload = json.loads(ledger.read_text())
    assert payload["reservations"]["call-1"]["reserved_tokens"] == 350
    assert payload["failures"][-1]["error_type"] == "orphaned_reservation_reclaimed"


def test_pure_llm_prompt_labels_feedback_as_task_local():
    context = {
        "split": "b_heldout",
        "task_isolation": "single_task_only",
        "repair_outcomes_included": False,
        "tasks": [{"task_id": "task-a", "failure_domain": "setup_timing"}],
    }
    prompt = json.loads(
        MODULE.proposal_user_prompt(context, [{"verdict": "loss"}], 1, "setup_timing", "pure_llm")
    )
    assert prompt["phase"] == "Pure LLM held-out repair"
    assert prompt["prior_same_model_same_task_feedback"] == [{"verdict": "loss"}]
    assert "prior_A_propose_feedback" not in prompt


def test_pure_llm_context_must_be_one_heldout_task():
    context = {
        "split": "b_heldout",
        "task_isolation": "single_task_only",
        "repair_outcomes_included": False,
        "tasks": [
            {"task_id": "task-a", "failure_domain": "setup_timing"},
            {"task_id": "task-b", "failure_domain": "setup_timing"},
        ],
    }
    try:
        MODULE.validate_model_context(context, "pure_llm", "setup_timing")
    except ValueError as exc:
        assert "exactly one" in str(exc)
    else:
        raise AssertionError("Pure LLM accepted cross-task context")


def test_formal_a_propose_limits_are_explicit_and_complete():
    payload = {
        "status": "frozen",
        "a_propose": {
            "rounds": 3,
            "api_calls_per_model_per_domain": 3,
            "model_turns_per_model_total": 6,
            "raw_proposals_per_call": 2,
            "max_output_tokens_per_call": 2048,
            "total_tokens_per_model": 60000,
        },
    }
    visible = MODULE.model_visible_resource_limits(
        payload,
        phase="a_propose",
        round_index=1,
        max_output_tokens=2048,
        require_frozen=True,
    )
    assert visible == {
        "mode": "formal",
        "fixed_rounds": 3,
        "current_round_zero_based": 1,
        "api_calls_per_model_per_domain": 3,
        "api_calls_per_model_total": 6,
        "model_turns_per_model_total": 6,
        "total_token_budget_per_model_across_both_domains": 60000,
        "raw_proposals_per_call": 2,
        "current_call_max_output_tokens": 2048,
    }


def test_formal_call_rejects_pending_resource_file():
    try:
        MODULE.model_visible_resource_limits(
            {"status": "pending_user_freeze", "a_propose": {}},
            phase="a_propose",
            round_index=0,
            max_output_tokens=2048,
            require_frozen=True,
        )
    except ValueError as exc:
        assert "status=frozen" in str(exc)
    else:
        raise AssertionError("formal call accepted pending limits")


def test_resolved_route_manifest_contains_no_credentials(tmp_path, monkeypatch):
    routes = tmp_path / "routes.json"
    output = tmp_path / "resolved.json"
    routes.write_text(
        json.dumps(
            {
                "routes": [
                    {
                        "model_key": "gpt",
                        "display_model": "GPT",
                        "model_id": "gpt-test",
                        "provider": "test",
                        "api_style": "openai_chat",
                        "endpoint": "https://fallback.invalid/v1/chat/completions",
                        "endpoint_env": "TEST_ENDPOINT",
                        "api_key_env": "TEST_SECRET",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_ENDPOINT", "https://gateway.example/v1")
    monkeypatch.setenv("TEST_SECRET", "do-not-persist")
    MODULE.command_resolve_routes(
        SimpleNamespace(routes=routes, output=output, require_credentials=True)
    )
    payload = json.loads(output.read_text())
    encoded = output.read_text()
    assert payload["resolved_routes"][0]["resolved_endpoint"] == "https://gateway.example/v1/chat/completions"
    assert payload["resolved_routes"][0]["credential_present"] is True
    assert "do-not-persist" not in encoded


def test_formal_single_domain_budget_is_not_doubled():
    payload = {
        "status": "frozen",
        "failure_domains": ["antenna_drc"],
        "a_propose": {
            "rounds": 3,
            "api_calls_per_model_per_domain": 3,
            "model_turns_per_model_total": 3,
            "raw_proposals_per_call": 2,
            "max_output_tokens_per_call": 2048,
            "total_tokens_per_model": 30000,
        },
    }
    visible = MODULE.model_visible_resource_limits(
        payload,
        phase="a_propose",
        round_index=0,
        max_output_tokens=2048,
        require_frozen=True,
    )
    assert visible["api_calls_per_model_total"] == 3
