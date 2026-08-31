"""Typed utility contract and context-conditioned proposal boundary tests."""
from __future__ import annotations

import pytest

from tehm.physical.utility_contracts import (
    UtilityContractError,
    contract_action,
    density_relief_nonregression_32,
    evaluate_observed_contract,
    select_contract_proposal,
    timing_relief_budgeted_v1,
    timing_relief_budgeted_v2_50_to_45,
    validate_utility_contract,
)


def _ppa(*, wns=0.0, tns=0.0, area=100.0, power=1.0):
    return {"summary": {
        "timing": {"setup_wns": wns, "setup_tns": tns},
        "area": {"design_area_um2": area},
        "power": {"total_power_w": power},
        "drc": {"drc_violations": 0},
    }}


def _checks():
    return {"equivalence": "PASS", "drc": "PASS", "lvs": "PASS",
            "timing": "PASS"}


def _graph():
    return {"platform": "sky130hs", "dataset_tier": "research",
            "digest": "context-for-test"}


def _prediction(*, wns_lower=0.015, area_upper=1.5, power_upper=0.02):
    return {
        "abstained": False,
        "abstain_reasons": [],
        "nearest_distance": 0.4,
        "support": 8,
        "unique_graph_contexts": 8,
        "query_graph_context_digest": "ctx",
        "mean_deltas": {"wns_ns": 0.02, "tns_ns": 0.0,
                         "area_um2": area_upper, "power_w": power_upper},
        "uncertainty_95": {
            "wns_ns": {"lower_95": wns_lower, "upper_95": 0.03},
            "tns_ns": {"lower_95": 0.0, "upper_95": 0.0},
            "area_um2": {"lower_95": 0.5, "upper_95": area_upper},
            "power_w": {"lower_95": -0.01, "upper_95": power_upper},
        },
    }


class _Memory:
    def __init__(self, prediction):
        self.prediction = prediction
        self.calls = []

    def predict(self, **kwargs):
        self.calls.append(kwargs)
        return self.prediction


def test_contract_is_frozen_and_action_bound():
    contract = timing_relief_budgeted_v1()
    validate_utility_contract(contract)
    assert contract["contract_id"] == "TIMING_RELIEF_BUDGETED_V1"
    assert contract["authority"]["raw_pareto_gate_unchanged"] is True
    action = contract_action(contract)
    assert action["payload"]["utility_contract_id"] == contract["contract_id"]
    with pytest.raises(UtilityContractError):
        validate_utility_contract({**contract, "runtime_policy": {
            **contract["runtime_policy"], "ood_action": "EXECUTE"}})


def test_50_to_45_is_an_independent_signature_and_not_v1():
    v1 = timing_relief_budgeted_v1()
    v2 = timing_relief_budgeted_v2_50_to_45()
    validate_utility_contract(v2)
    assert v2["contract_id"] != v1["contract_id"]
    assert v2["action_signature"]["config_edits"] == {"CORE_UTILIZATION": "45"}
    assert v2["action_signature"]["operation_point"] == "50->45"
    assert contract_action(v2)["payload"]["utility_contract_id"] == v2["contract_id"]
    assert contract_action(v1) != contract_action(v2)


def test_action32_contract_is_pre_registered_and_rejects_timing_regression():
    contract = density_relief_nonregression_32()
    validate_utility_contract(contract)
    assert contract["status"] == "PRE_REGISTERED_FOR_NEXT_SOURCE_DISJOINT_COHORT"
    assert contract["action_signature"]["config_edits"] == {"CORE_UTILIZATION": "32"}
    result = evaluate_observed_contract(
        contract=contract, action=contract_action(contract),
        before_ppa=_ppa(), after_ppa=_ppa(wns=-0.01, tns=0.0, area=99.0, power=0.99),
        checks=_checks())
    assert result["status"] == "FAIL"
    assert "wns_delta_below_objective" in result["failures"]
    assert result["contract_eligible"] is False
    assert result["canonical_memory_mutation"] == "none"


def test_observed_contract_accepts_orfs_geometry_area_baseline():
    contract = density_relief_nonregression_32()
    before = {
        "geometry": {"die_area_um2": 100.0},
        "summary": {"timing": {"setup_wns": 0.0, "setup_tns": 0.0},
                    "power": {"total_power_w": 1.0}},
    }
    after = {
        "geometry": {"die_area_um2": 99.0},
        "summary": {"timing": {"setup_wns": 0.01, "setup_tns": 0.0},
                    "power": {"total_power_w": 1.0}},
    }
    result = evaluate_observed_contract(
        contract=contract, action=contract_action(contract),
        before_ppa=before, after_ppa=after, checks=_checks())
    assert result["status"] == "PASS"
    assert result["observed_relative"]["area_delta_percent"] == -1.0


def test_observed_contract_can_pass_while_raw_pareto_remains_harmful():
    contract = timing_relief_budgeted_v1()
    result = evaluate_observed_contract(
        contract=contract, action=contract_action(contract),
        before_ppa=_ppa(), after_ppa=_ppa(wns=0.02, area=101.5, power=1.02),
        checks=_checks())
    assert result["status"] == "PASS"
    assert result["contract_eligible"] is True
    assert result["raw_pareto"]["verdict"] == "HARMFUL"
    assert result["raw_pareto"]["harmful_metrics"] == ["area_um2", "power_w"]
    assert result["canonical_memory_mutation"] == "none"


def test_observed_contract_fails_budget_and_does_not_rewrite_raw_verdict():
    contract = timing_relief_budgeted_v1()
    result = evaluate_observed_contract(
        contract=contract, action=contract_action(contract),
        before_ppa=_ppa(), after_ppa=_ppa(wns=0.02, area=102.1, power=1.02),
        checks=_checks())
    assert result["status"] == "FAIL"
    assert "area_budget_exceeded" in result["failures"]
    assert result["raw_pareto"]["verdict"] == "HARMFUL"


def test_observed_contract_abstains_when_hard_evidence_is_missing():
    contract = timing_relief_budgeted_v1()
    result = evaluate_observed_contract(
        contract=contract, action=contract_action(contract),
        before_ppa=_ppa(), after_ppa=_ppa(wns=0.02, area=101.0, power=1.01),
        checks={"equivalence": "PASS"})
    assert result["status"] == "ABSTAINED"
    assert "missing_hard_oracle:drc" in result["abstain_reasons"]


def test_selector_proposes_only_when_all_prediction_intervals_fit():
    contract = timing_relief_budgeted_v1()
    memory = _Memory(_prediction())
    result = select_contract_proposal(
        memory, contract=contract, action=contract_action(contract),
        graph_context=_graph(), baseline_ppa=_ppa(),
        calibration_policy={"status": "ready"}, hard_checks=_checks(),
        obligation_coverage=1.0)
    assert result["status"] == "PROPOSED"
    assert result["abstained"] is False
    assert result["proposal_only"] is True
    assert result["promotion_eligible"] is False
    assert result["canonical_memory_mutation"] == "none"
    assert result["rule_proposal"]["status"] == "candidate"
    assert result["rule_proposal"]["context_predicates"]
    assert result["rule_proposal"]["runtime_eligible"] is False
    assert len(memory.calls) == 1


def test_selector_abstains_when_wns_interval_crosses_contract_boundary():
    contract = timing_relief_budgeted_v1()
    memory = _Memory(_prediction(wns_lower=0.005))
    result = select_contract_proposal(
        memory, contract=contract, action=contract_action(contract),
        graph_context=_graph(), baseline_ppa=_ppa(),
        calibration_policy={"status": "ready"}, hard_checks=_checks(),
        obligation_coverage=1.0)
    assert result["status"] == "ABSTAINED"
    assert "wns_interval_below_objective" in result["abstain_reasons"]


def test_selector_abstains_for_unbound_action_and_never_falls_back():
    contract = timing_relief_budgeted_v1()
    memory = _Memory(_prediction())
    action = {"domain": "flow.CONFIG_DELTA",
              "transformation_family": "DENSITY_RELIEF",
              "payload": {"config_edits": {"CORE_UTILIZATION": "40"}}}
    result = select_contract_proposal(
        memory, contract=contract, action=action, graph_context=_graph(),
        baseline_ppa=_ppa(), calibration_policy={"status": "ready"},
        hard_checks=_checks(), obligation_coverage=1.0)
    assert result["status"] == "ABSTAINED"
    assert result["abstain_reasons"] == ["utility_contract_binding_mismatch"]
    assert memory.calls == []
