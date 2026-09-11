"""Paired ORFS physical-harm attribution tests."""
from __future__ import annotations

import copy

import pytest

from tehm.evaluation.candidate_executor import P12_ARMS, execute_paired_candidates
from tehm.evaluation.counterfactual_oracle import (
    build_counterfactual_oracle_receipt,
)
from tehm.evaluation.orfs_candidate_oracle import _physical_observation
from tehm.evaluation.orfs_paired_utility import (
    OrfsPairedUtilityError,
    apply_orfs_paired_utility_contract,
    replay_orfs_paired_utility_receipt,
)
from tehm.evolution.reason_derivation import (
    EvolutionReasonDerivationError, derive_memory_interference_reason,
)
from tehm.physical.utility_contracts import (
    p12_density_relief_interference_nonregression_v1,
)
from tehm.retrieval.structured_candidate import StructuredRepairCandidate


def _candidate() -> StructuredRepairCandidate:
    return StructuredRepairCandidate(
        candidate_id="source-bound-core40",
        resolved_state_id="state-density",
        knowledge_object_id="mk-density@1",
        causal_path_ids=("path-density",),
        asset_id="asset-density",
        action_family="DENSITY_RELIEF",
        concrete_action={
            "domain": "flow.CONFIG_DELTA",
            "transformation_family": "DENSITY_RELIEF",
            "payload": {
                "config_edits": {"CORE_UTILIZATION": "40"},
                "measurement_contract_digest": "sha256:flow-feasibility",
                "recheck": "flow_feasibility",
            },
        },
        applicability_receipt_id="app-density",
        binding_receipt_id="binding-density",
        obligations=("ORFS_FLOW_PASS",),
        evidence_level="L3_REPLICATED_EFFECT",
        authority={"eligible": True}, risk={},
        provenance={"evaluation_only": True, "source": "typed-test"},
    )


def _ppa(*, wns: float, area: float, power: float) -> dict:
    return {
        "summary": {
            "timing": {"setup_wns": wns, "setup_tns": 0.0},
            "area": {},
            "power": {"total_power_w": power},
            "drc": {},
        },
        "geometry": {"die_area_um2": area},
        "orfs_status": "complete",
        "orfs_last_stage": "finish",
    }


class _Oracle:
    def execute_policy_arm(self, arm, candidate, _case, _budget):
        ppa = (_ppa(wns=0.10, area=100.0, power=1.0)
               if candidate is None else
               _ppa(wns=0.12, area=110.0, power=0.9))
        checks = {name: "PASS" for name in (
            "route", "drc", "lvs", "rcx", "timing", "manifest_binding")}
        return {
            "compile_result": "PASS", "functional_result": "PASS",
            "signoff_result": "UNKNOWN", "outcome": "PASS",
            "created_regressions": [], "obligations": {},
            "toolchain_digest": "sha256:tool",
            "oracle_digest": "sha256:oracle",
            "metadata": {
                "policy_arm": arm,
                "physical_observation": _physical_observation({"ppa": ppa}),
                "counterfactual_oracle": build_counterfactual_oracle_receipt(
                    checks, evidence_digest="sha256:evidence-" + arm.lower()),
            },
        }


def _bundle(candidate=None):
    candidate = candidate or _candidate()
    arms = {"NO_MEMORY": None, **{arm: candidate for arm in P12_ARMS[1:]}}
    bundle = execute_paired_candidates(
        {"case_id": "heldout-density", "toolchain_digest": "sha256:tool",
         "oracle_digest": "sha256:oracle"},
        arms, oracle=_Oracle(), budget=3, lineage_id="heldout-lineage",
        routing_decision="CONSIDER")
    return bundle, arms


def test_paired_utility_derives_physical_regression_and_interference():
    bundle, arms = _bundle()
    checked = apply_orfs_paired_utility_contract(
        bundle, arms,
        contract=p12_density_relief_interference_nonregression_v1())
    forced = checked.arm_receipts["ALWAYS_MEMORY"]
    assert forced.outcome == "PASS"
    assert any(value.endswith(":area_budget_exceeded")
               for value in forced.created_regressions)
    utility = forced.metadata["paired_utility"]
    assert utility["observation"]["status"] == "FAIL"
    assert utility["strict_signoff_claim"] is False
    assert utility["canonical_memory_mutation"] == "none"
    assert replay_orfs_paired_utility_receipt(
        checked.arm_receipts["NO_MEMORY"], forced) == utility
    reason = derive_memory_interference_reason(
        checked, campaign_id="r3-8-source-bound")
    assert reason is not None
    assert reason.reason == "MEMORY_INTERFERENCE"


def test_paired_utility_rejects_tampered_physical_observation():
    bundle, arms = _bundle()
    forced = bundle.arm_receipts["ALWAYS_MEMORY"]
    metadata = copy.deepcopy(forced.metadata)
    metadata["oracle_metadata"]["physical_observation"]["ppa"][
        "geometry"]["die_area_um2"] = 99.0
    from dataclasses import replace
    receipts = dict(bundle.arm_receipts)
    receipts["ALWAYS_MEMORY"] = replace(forced, metadata=metadata)
    tampered = replace(bundle, arm_receipts=receipts)
    with pytest.raises(OrfsPairedUtilityError, match="report digest mismatch"):
        apply_orfs_paired_utility_contract(
            tampered, arms,
            contract=p12_density_relief_interference_nonregression_v1())


def test_paired_utility_rejects_incomplete_ppa():
    bundle, arms = _bundle()
    forced = bundle.arm_receipts["ALWAYS_MEMORY"]
    metadata = copy.deepcopy(forced.metadata)
    incomplete = _ppa(wns=0.12, area=110.0, power=0.9)
    incomplete["summary"]["power"] = {}
    metadata["oracle_metadata"]["physical_observation"] = (
        _physical_observation({"ppa": incomplete}))
    from dataclasses import replace
    receipts = dict(bundle.arm_receipts)
    receipts["ALWAYS_MEMORY"] = replace(forced, metadata=metadata)
    incomplete_bundle = replace(bundle, arm_receipts=receipts)
    with pytest.raises(OrfsPairedUtilityError,
                       match="paired utility observation is incomplete"):
        apply_orfs_paired_utility_contract(
            incomplete_bundle, arms,
            contract=p12_density_relief_interference_nonregression_v1())


def test_paired_utility_rejects_action_contract_mismatch():
    wrong = _candidate()
    payload = wrong.to_dict()
    payload.pop("candidate_digest", None)
    payload.pop("receipt_id", None)
    wrong = StructuredRepairCandidate.from_dict({
        **payload,
        "concrete_action": {
            **wrong.concrete_action,
            "payload": {"config_edits": {"CORE_UTILIZATION": "45"}},
        },
    })
    bundle, arms = _bundle(wrong)
    with pytest.raises(OrfsPairedUtilityError, match="action does not match contract"):
        apply_orfs_paired_utility_contract(
            bundle, arms,
            contract=p12_density_relief_interference_nonregression_v1())


def test_interference_detector_replays_paired_utility_projection():
    bundle, arms = _bundle()
    checked = apply_orfs_paired_utility_contract(
        bundle, arms,
        contract=p12_density_relief_interference_nonregression_v1())
    forced = checked.arm_receipts["ALWAYS_MEMORY"]
    from dataclasses import replace
    receipts = dict(checked.arm_receipts)
    receipts["ALWAYS_MEMORY"] = replace(
        forced, created_regressions=("utility_contract:forged",))
    forged = replace(checked, arm_receipts=receipts)
    with pytest.raises(EvolutionReasonDerivationError,
                       match="regression projection mismatch"):
        derive_memory_interference_reason(
            forged, campaign_id="r3-8-source-bound")
