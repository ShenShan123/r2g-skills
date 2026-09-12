"""Execution-audit boundaries; arithmetic fixtures are NOT empirical receipts."""
import copy
from dataclasses import replace

import pytest

from scripts.audit_p13_interference_policy_execution import (
    InterferencePolicyExecutionAuditError, _case_gate, _distinct_executions,
    _metrics, _retained_arm, _zero_regression, audit_policy_execution,
)
from tehm.evaluation.candidate_executor import execute_paired_candidates
from tehm.evaluation.orfs_candidate_oracle import _physical_observation
from tehm.evaluation.orfs_paired_utility import apply_orfs_paired_utility_contract
from tehm.physical.utility_contracts import p12_density_relief_interference_nonregression_v1
from test_orfs_paired_utility import _candidate, _Oracle, _ppa


def _pairs():
    candidate = _candidate()
    pairs = {}
    for view in ("Mt", "Mt_plus_delta", "Mt_plus_delta_minus_delta"):
        selected = view != "Mt_plus_delta"
        arms = {"NO_MEMORY": None, "ALWAYS_MEMORY": candidate,
                "APPLICABILITY_GATED": candidate if selected else None,
                "CAUSAL_NO_SKILL": candidate if selected else None}
        pair = execute_paired_candidates(
            {"case_id": "audit-fixture", "toolchain_digest": "sha256:tool", "oracle_digest": "sha256:oracle"},
            arms, oracle=_Oracle(), budget=3, lineage_id="fixture-lineage",
            routing_decision="CONSIDER" if selected else "INAPPLICABLE", routing_receipt_id="fixture-" + view)
        pairs[view] = apply_orfs_paired_utility_contract(
            pair, arms, contract=p12_density_relief_interference_nonregression_v1())
    return pairs


def test_zero_tolerance_does_not_round_away_small_power_harm():
    before = _ppa(wns=1, area=100, power=0.000286324)
    after = _ppa(wns=1, area=100, power=0.000286324001)
    audit = _zero_regression(before, after)
    assert audit["non_regressing"] is False
    assert audit["failures"] == ["power_w"]
    assert audit["deltas_after_minus_before"]["power_w"] == "1E-12"


@pytest.mark.parametrize("metric,value", [("setup_wns", 0.9), ("setup_tns", -0.1)])
def test_worse_setup_or_tns_is_not_non_regressing(metric, value):
    before = _ppa(wns=1, area=100, power=1)
    after = copy.deepcopy(before)
    after["summary"]["timing"][metric] = value
    assert _zero_regression(before, after)["non_regressing"] is False


def test_missing_or_nonfinite_power_cannot_be_declared_safe():
    for value in (None, float("nan"), float("inf")):
        ppa = _ppa(wns=1, area=100, power=value)
        with pytest.raises(InterferencePolicyExecutionAuditError, match="incomplete"):
            _metrics(ppa)


def test_distinct_path_spellings_cannot_hide_aliased_remove_delta(tmp_path):
    workspace = tmp_path / "real"
    workspace.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(workspace, target_is_directory=True)
    with pytest.raises(InterferencePolicyExecutionAuditError, match="alias"):
        _distinct_executions([workspace, alias])
    _distinct_executions([workspace, tmp_path / "separate"])


@pytest.mark.parametrize("purpose", ["target_replay", "heldout"])
def test_arithmetic_gate_requires_harm_avoided_and_real_policy_restoration(purpose):
    gate = _case_gate(_pairs(), purpose=purpose)
    assert gate["passed"]
    for row in gate["arms"].values():
        assert row["Mt_harm"]["harm"] and row["remove_delta_harm"]["harm"]
        assert row["post_fallback_attributed_to_memory_action"] is False


def test_old_pre_no_memory_baseline_is_not_remove_delta_memory_execution():
    pairs = _pairs()
    removed = pairs["Mt_plus_delta_minus_delta"]
    arms = dict(removed.arm_receipts)
    arms["APPLICABILITY_GATED"] = removed.arm_receipts["NO_MEMORY"]
    pairs["Mt_plus_delta_minus_delta"] = replace(removed, arm_receipts=arms)
    with pytest.raises(InterferencePolicyExecutionAuditError, match="executed memory"):
        _case_gate(pairs, purpose="heldout")


def test_fallback_ppa_regression_blocks_gain_even_when_fixed_oracle_passes():
    pairs = _pairs()
    after = pairs["Mt_plus_delta"]
    arms = dict(after.arm_receipts)
    fallback = arms["APPLICABILITY_GATED"]
    metadata = copy.deepcopy(fallback.metadata)
    ppa = metadata["oracle_metadata"]["physical_observation"]["ppa"]
    ppa["summary"]["power"]["total_power_w"] += 1e-9
    metadata["oracle_metadata"]["physical_observation"] = _physical_observation({"ppa": ppa})
    arms["APPLICABILITY_GATED"] = replace(fallback, metadata=metadata)
    pairs["Mt_plus_delta"] = replace(after, arm_receipts=arms)
    gate = _case_gate(pairs, purpose="heldout")
    assert gate["passed"] is False
    assert "power_w" in gate["arms"]["APPLICABILITY_GATED"]["post_fallback_vs_executed_baseline"]["failures"]


def test_non_target_preservation_does_not_erase_existing_absolute_utility_harm():
    pairs = _pairs()
    pairs["Mt_plus_delta"] = pairs["Mt"]
    gate = _case_gate(pairs, purpose="non_target")
    assert gate["passed"]
    for row in gate["arms"].values():
        assert row["existing_mt_regressions_retained"]
        assert row["absolute_utility_safety_claimed"] is False


def test_non_target_candidate_effect_cannot_change_under_same_utility_label():
    pairs = _pairs()
    after = pairs["Mt"]
    arms = dict(after.arm_receipts)
    arms["APPLICABILITY_GATED"] = replace(arms["APPLICABILITY_GATED"], action_digest="sha256:foreign")
    pairs["Mt_plus_delta"] = replace(after, arm_receipts=arms)
    assert _case_gate(pairs, purpose="non_target")["passed"] is False


def test_missing_retained_workspace_cannot_pass_with_only_json_boolean(tmp_path):
    receipt = _pairs()["Mt"].arm_receipts["ALWAYS_MEMORY"]
    metadata = copy.deepcopy(receipt.metadata)
    metadata["oracle_metadata"].update(execution_artifacts_retained=True,
                                        execution_project_dir=str(tmp_path / "missing"))
    receipt = replace(receipt, metadata=metadata)
    case = {"execution_artifacts_root": str(tmp_path), "source_digest": "sha256:fixture"}
    with pytest.raises(InterferencePolicyExecutionAuditError, match="retained execution"):
        _retained_arm(receipt, _candidate(), case, "ALWAYS_MEMORY")


def test_existing_audit_never_overwritten(tmp_path):
    output = tmp_path / "existing.json"
    output.write_text("original")
    with pytest.raises(InterferencePolicyExecutionAuditError, match="must be new"):
        audit_policy_execution("missing", "missing", purpose="heldout", output=output)
    assert output.read_text() == "original"
