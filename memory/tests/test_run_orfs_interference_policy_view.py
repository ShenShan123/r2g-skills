"""Policy runner authority validation; fixtures are not empirical EDA results."""
import json
from types import SimpleNamespace

import pytest

from scripts.run_orfs_interference_policy_view import (
    InterferencePolicyRunError, _verify_candidate_freeze, _verify_unchanged_case, run_policy_view,
)


def _freeze(selected=False):
    forced = {"path": "/forced.json", "sha256": "sha256:f", "candidate_id": "f", "candidate_digest": "sha256:d"}
    frozen = {"audit": {"NO_MEMORY": None, "ALWAYS_MEMORY": forced,
                        "APPLICABILITY_GATED": forced if selected else None,
                        "CAUSAL_NO_SKILL": forced if selected else None}}
    refs = {"audit": {arm: ref for arm, ref in frozen["audit"].items() if ref is not None}}
    routing = {"audit": SimpleNamespace(decision="CONSIDER" if selected else "INAPPLICABLE")}
    return frozen, refs, routing


@pytest.mark.parametrize("selected", [True, False])
def test_exact_actual_per_arm_refs_accept_memory_and_true_fallback(selected):
    frozen, refs, routing = _freeze(selected)
    _verify_candidate_freeze({"audit"}, frozen, refs, routing)


@pytest.mark.parametrize("mutation", ["manual_null", "invented_candidate", "foreign_case", "forced_null", "arm_missing"])
def test_no_manual_policy_arm_rewrite_can_bypass_actual_freeze(mutation):
    frozen, refs, routing = _freeze(mutation == "manual_null")
    if mutation == "manual_null":
        refs["audit"].pop("APPLICABILITY_GATED")
    elif mutation == "invented_candidate":
        refs["audit"]["CAUSAL_NO_SKILL"] = refs["audit"]["ALWAYS_MEMORY"]
    elif mutation == "foreign_case":
        frozen["other"] = frozen.pop("audit")
    elif mutation == "forced_null":
        frozen["audit"]["ALWAYS_MEMORY"] = None
        refs["audit"].clear()
    else:
        frozen["audit"].pop("NO_MEMORY")
    with pytest.raises(InterferencePolicyRunError):
        _verify_candidate_freeze({"audit"}, frozen, refs, routing)


def test_self_consistent_null_still_rejected_when_actual_route_selects_memory():
    frozen, refs, routing = _freeze()
    routing["audit"].decision = "CONSIDER"
    with pytest.raises(InterferencePolicyRunError, match="contradicts actual route"):
        _verify_candidate_freeze({"audit"}, frozen, refs, routing)


def test_policy_may_change_action_and_artifact_root_not_environment_or_role():
    baseline = {"source_digest": "sha256:s", "dataset_split": "held_out", "role": "held_out",
                "learner_eligible": False, "environment": {"NUM_CORES": "2"}, "candidate_paths": {}}
    runtime = {**baseline, "candidate_paths": {"CAUSAL_NO_SKILL": None}, "execution_artifacts_root": "/new"}
    _verify_unchanged_case(runtime, baseline)
    for key, value in (("role", "training"), ("learner_eligible", True),
                       ("source_digest", "sha256:other"), ("environment", {"NUM_CORES": "8"})):
        with pytest.raises(InterferencePolicyRunError, match="source, environment, or audit role"):
            _verify_unchanged_case({**runtime, key: value}, baseline)


def test_terminal_or_preexecution_files_are_not_overwritten(tmp_path):
    output = tmp_path / "execution.json"
    request = tmp_path / "execution.json.preexecution.json"
    request.write_text("original request")
    with pytest.raises(InterferencePolicyRunError, match="must be new"):
        run_policy_view("missing", output=output)
    assert request.read_text() == "original request" and not output.exists()


def test_post_execution_drift_preserves_completed_real_receipts_even_when_gate_fails(tmp_path, monkeypatch):
    import scripts.run_orfs_interference_policy_view as runner
    from scripts.build_p13_interference_source_bound_plan import _digest
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    payload = {"campaign_id": "fixture-policy", "policy_view": "Mt", "platform_digest": "sha256:p",
               "pdk_digest": "sha256:d", "toolchain_digest": "sha256:t", "oracle_digest": "sha256:o",
               "utility_contract_id": "fixture-contract", "utility_contract_digest": "sha256:u"}
    inputs = {"manifest": payload, "cases": [], "budget": 3, "min_lineages": 1,
              "authority_ref": {}, "candidates": {}, "candidate_refs": {}, "routing": {}, "routing_ref": {}}
    calls = iter((inputs, InterferencePolicyRunError("fixture source drift")))
    def validate(_):
        result = next(calls)
        if isinstance(result, Exception):
            raise result
        return result
    cohort = SimpleNamespace(to_dict=lambda: {"fixture_only": True}, receipt_digest="sha256:actual-retained")
    monkeypatch.setattr(runner, "validate_policy_inputs", validate)
    monkeypatch.setattr(runner, "known_utility_contracts", lambda: {"fixture-contract": lambda: {}})
    monkeypatch.setattr(runner, "execute_orfs_paired_cohort", lambda *args, **kwargs: cohort)
    output = tmp_path / "result.json"
    with pytest.raises(InterferencePolicyRunError, match="fixture source drift"):
        runner.run_policy_view(manifest, output=output)
    terminal = json.loads(output.read_text())
    assert terminal["status"] == "EXECUTION_BINDING_FAILED"
    assert terminal["post_execution_binding_passed"] is False
    assert terminal["cohort_receipt_available"] is True
    assert terminal["cohort_receipt_digest"] == cohort.receipt_digest
    assert terminal["cohort_receipt"]["fixture_only"] is True
    supplied = terminal.pop("terminal_report_digest")
    assert supplied == _digest(terminal)
    assert output.with_name("result.json.preexecution.json").is_file()
