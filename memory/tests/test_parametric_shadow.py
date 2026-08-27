"""Parametric shadow contract: evidence gates, provenance, and no mutation."""
from __future__ import annotations

import copy

import pytest

from tehm.parametric.shadow import (
    SHADOW_ABSTAINED,
    SHADOW_PROPOSED,
    ParametricShadowError,
    build_shadow_proposal,
    proposal_digest,
)
from tehm.parametric.shadow_campaign import (
    AppendOnlyShadowLog,
    ShadowCampaignError,
    build_outcome,
    build_receipt,
    join_receipts_and_outcomes,
    summarise,
    validate_observation_gate,
)
from tehm.views.parametric_stub import PARAMETRIC_VIEW_STATUS


def _context():
    body = {
        "platform": "sky130hd",
        "dataset_tier": "strict_clean",
        "graph_features": {"num_cells": 12.0, "avg_fanout": 1.2},
        "topology_rows": {"nets": 8},
        "extractor_version": "graph-v0.1",
    }
    from hashlib import sha256
    from tehm.ids import stable_dumps
    body["digest"] = sha256(stable_dumps(body).encode()).hexdigest()
    return body


def _readiness(**overrides):
    criteria = {
        "all_retrieval_policies_ready": True,
        "distance_gate_satisfied": True,
        "coverage_gate_satisfied": True,
        "uncertainty_gate_satisfied": True,
        "lineage_diversity_satisfied": True,
        "minimum_independent_heldout_lineages": 2,
        "observed_independent_heldout_lineages": 2,
    }
    criteria.update(overrides)
    return {
        "status": "READY_FOR_IMPLEMENTATION",
        "parametric_view_status": PARAMETRIC_VIEW_STATUS,
        "criteria": criteria,
    }


def _policy():
    return {
        "family": "DENSITY_RELIEF",
        "scope": {"platform": "sky130hd", "family": "DENSITY_RELIEF",
                  "dataset_tier": "strict_clean"},
        "status": "ready",
        "version": "cal-v0.1",
        "firewall": {"heldout_lineages": ["heldout:a", "heldout:b"],
                      "disjoint": True, "overlap": []},
        "thresholds": {"max_distance": 3.0, "required_coverage": 0.8},
        "calibration": {"empirical_coverage": 1.0, "required_metrics": ["area_um2"]},
    }


def _replay():
    return {
        "ok": True,
        "roundtrip_byte_stable": True,
        "bundle_digest": "bundle-a",
        "manifest_digest": "manifest-a",
    }


class _Memory:
    def __init__(self, result=None):
        self.result = result or {
            "family": "DENSITY_RELIEF",
            "effect_key": None,
            "retrieval_mode": "similar_graph_knn",
            "memory_version": "physical-effect-memory-v0.3",
            "gradient_claimed": False,
            "query_graph_context_digest": _context()["digest"],
            "platform": "sky130hd",
            "dataset_tier": "strict_clean",
            "abstained": False,
            "abstain_reasons": [],
            "support": 3,
            "unique_graph_contexts": 3,
            "neighbour_contexts": 3,
            "nearest_distance": 0.5,
            "max_distance": 3.0,
            "calibration": {"status": "ready"},
            "mean_deltas": {"area_um2": 1.0},
            "uncertainty_95": {"area_um2": {"lower_95": 0.0, "upper_95": 2.0}},
            "harmful_metrics": [], "neighbours": [], "note": "no gradient",
        }
        self.calls = 0
        self.last_kwargs = {}

    def predict(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        return copy.deepcopy(self.result)


def test_shadow_proposal_is_read_only_and_provenance_bound():
    memory = _Memory()
    result = build_shadow_proposal(
        memory, family="DENSITY_RELIEF", graph_context=_context(),
        calibration_policy=_policy(), readiness=_readiness(),
        replay_evidence=_replay())
    assert result["shadow_status"] == SHADOW_PROPOSED
    assert result["parametric_view_status"] == "NOT_IMPLEMENTED"
    assert result["parametric_shadow_status"] == "SHADOW_ONLY"
    assert result["promotion_eligible"] is False
    assert result["canonical_memory_mutation"] == "none"
    assert result["abstained"] is False
    assert result["provenance"]["bundle_digest"] == "bundle-a"
    assert result["provenance"]["heldout_lineages"] == ["heldout:a", "heldout:b"]
    assert memory.calls == 1
    assert proposal_digest(result) == proposal_digest(copy.deepcopy(result))


def test_failed_readiness_abstains_before_prediction():
    memory = _Memory()
    result = build_shadow_proposal(
        memory, family="DENSITY_RELIEF", graph_context=_context(),
        calibration_policy=_policy(), readiness=_readiness(coverage_gate_satisfied=False),
        replay_evidence=_replay())
    assert result["shadow_status"] == SHADOW_ABSTAINED
    assert result["abstained"] is True
    assert "readiness_gate_failed:coverage_gate_satisfied" in result["abstain_reasons"]
    assert memory.calls == 0


def test_shadow_binds_action_digest_and_forwards_action_conditioning():
    memory = _Memory()
    action = {
        "domain": "flow.CONFIG_DELTA",
        "payload": {"config_edits": {"CORE_UTILIZATION": "22"}},
        "transformation_family": "DENSITY_RELIEF",
    }
    result = build_shadow_proposal(
        memory, family="DENSITY_RELIEF", graph_context=_context(),
        action=action, calibration_policy=_policy(), readiness=_readiness(),
        replay_evidence=_replay())
    assert result["action_digest"]
    assert memory.last_kwargs["action"] == action


def test_unverified_replay_abstains_even_when_policy_ready():
    memory = _Memory()
    replay = _replay()
    replay["roundtrip_byte_stable"] = False
    result = build_shadow_proposal(
        memory, family="DENSITY_RELIEF", graph_context=_context(),
        calibration_policy=_policy(), readiness=_readiness(),
        replay_evidence=replay)
    assert result["shadow_status"] == SHADOW_ABSTAINED
    assert "replay_roundtrip_not_byte_stable" in result["abstain_reasons"]
    assert memory.calls == 0


def test_prediction_ood_remains_abstained():
    memory = _Memory({"abstained": True, "abstain_reasons": ["out_of_distribution"]})
    result = build_shadow_proposal(
        memory, family="DENSITY_RELIEF", graph_context=_context(),
        calibration_policy=_policy(), readiness=_readiness(),
        replay_evidence=_replay())
    assert result["shadow_status"] == SHADOW_ABSTAINED
    assert result["abstain_reasons"] == ["out_of_distribution"]
    assert result["promotion_eligible"] is False


def test_invalid_graph_digest_is_rejected():
    context = _context()
    context["digest"] = "wrong"
    with pytest.raises(ParametricShadowError):
        build_shadow_proposal(
            _Memory(), family="DENSITY_RELIEF", graph_context=context,
            calibration_policy=_policy(), readiness=_readiness(),
            replay_evidence=_replay())


def _receipt():
    proposal = build_shadow_proposal(
        _Memory(), family="DENSITY_RELIEF", graph_context=_context(),
        calibration_policy=_policy(), readiness=_readiness(), replay_evidence=_replay())
    counts = {key: 10 for key in (
        "tehm_states", "tehm_transitions", "tehm_episodes", "tehm_views",
        "tehm_rules", "tehm_physical_effects")}
    return build_receipt(
        case_id="future:lineage:0",
        proposal=proposal,
        target_graph_context_digest=_context()["digest"],
        action={"domain": "flow.CONFIG_DELTA", "payload": {"CORE_UTILIZATION": "30"}},
        policy_digest=proposal["provenance"]["policy_digest"],
        bundle_digest="bundle-a", manifest_digest="manifest-a",
        canonical_counts_before=counts, candidate_rank=1), counts


def _ppa(area):
    return {"summary": {"timing": {"setup_wns": 1.0, "setup_tns": 0.0},
                         "area": {"design_area_um2": area},
                         "power": {"total_power_w": 1.0}}}


def test_shadow_log_is_idempotent_chained_and_recovers_partial_tail(tmp_path):
    receipt, counts = _receipt()
    log = AppendOnlyShadowLog(tmp_path / "shadow.jsonl")
    first = log.append(receipt)
    duplicate = log.append(receipt)
    assert first["duplicate"] is False
    assert duplicate["duplicate"] is True
    outcome = build_outcome(receipt=receipt, before_ppa=_ppa(10), after_ppa=_ppa(11),
                            oracle={"obligation_coverage": 1.0})
    log.append(outcome)
    assert len(log.read()) == 2
    with (tmp_path / "shadow.jsonl").open("ab") as stream:
        stream.write(b'{"partial":')
    with pytest.raises(ShadowCampaignError):
        log.read(recover_partial_tail=False)
    recovered = log.read(recover_partial_tail=True)
    assert len(recovered) == 2
    assert recovered[-1]["event"]["record_type"] == "shadow_outcome"


def test_shadow_log_rejects_tampered_complete_record(tmp_path):
    receipt, _ = _receipt()
    path = tmp_path / "shadow.jsonl"
    log = AppendOnlyShadowLog(path)
    log.append(receipt)
    text = path.read_text().replace("future:lineage:0", "future:lineage:tampered")
    path.write_text(text)
    with pytest.raises(ShadowCampaignError):
        log.read()


def test_shadow_outcome_join_and_metrics_keep_missing_metrics_explicit():
    receipt, _ = _receipt()
    outcome = build_outcome(receipt=receipt, before_ppa=_ppa(10), after_ppa=_ppa(11),
                            oracle={"obligation_coverage": 1.0})
    joined, report = join_receipts_and_outcomes([
        {"event": receipt}, {"event": outcome}])
    assert report["joined_count"] == 1
    assert joined[0]["observed_deltas"]["area_um2"] == 1.0
    assert joined[0]["observed_deltas"]["congestion"] is None
    metrics = summarise(joined, total_receipts=1)
    assert metrics["proposal_coverage"] == 1.0
    assert metrics["physical_metrics"]["area_um2"]["evaluated"] == 1
    assert metrics["physical_metrics"]["area_um2"]["interval_coverage"] == 1.0


def test_shadow_metrics_report_abstain_reasons_and_ood_distance():
    proposal = build_shadow_proposal(
        _Memory({"abstained": True, "abstain_reasons": ["out_of_distribution"],
                 "nearest_distance": 2.5}),
        family="DENSITY_RELIEF", graph_context=_context(),
        calibration_policy=_policy(), readiness=_readiness(), replay_evidence=_replay())
    receipt = build_receipt(
        case_id="future:lineage:abstain", proposal=proposal,
        target_graph_context_digest=_context()["digest"],
        action={"domain": "flow.CONFIG_DELTA", "payload": {"CORE_UTILIZATION": "20"}},
        policy_digest=proposal["provenance"]["policy_digest"],
        bundle_digest="bundle-a", manifest_digest="manifest-a",
        canonical_counts_before={key: 10 for key in (
            "tehm_states", "tehm_transitions", "tehm_episodes", "tehm_views",
            "tehm_rules", "tehm_physical_effects")})
    outcome = build_outcome(receipt=receipt, before_ppa=_ppa(10), after_ppa=_ppa(11))
    joined, report = join_receipts_and_outcomes([{"event": receipt}, {"event": outcome}])
    metrics = summarise(joined, total_receipts=1)
    assert report["joined_count"] == 1
    assert metrics["abstain_reason_distribution"] == {"out_of_distribution": 1}
    assert metrics["ood_distance"] == {"evaluated": 1, "min": 2.5, "max": 2.5, "mean": 2.5}
    assert metrics["proposal_coverage"] == 0.0


def test_shadow_outcome_marks_canonical_mutation_and_is_not_joinable():
    receipt, counts = _receipt()
    changed = dict(counts)
    changed["tehm_physical_effects"] += 1
    outcome = build_outcome(receipt=receipt, before_ppa=_ppa(10), after_ppa=_ppa(11),
                            canonical_counts_after=changed)
    assert outcome["status"] == "INVALID_MEMORY_MUTATION"
    joined, report = join_receipts_and_outcomes([
        {"event": receipt}, {"event": outcome}])
    assert joined == []
    assert report["invalid_outcomes"] == [receipt["receipt_id"]]


def test_decision_gate_rejects_abstained_observation_metrics():
    manifest = {
        "pre_registered_metrics": {
            "hard_ood_ceiling": 3.0,
            "min_interval_coverage": 0.8,
            "max_harmful_rate": 0.1,
        },
        "decision_gate": {
            "min_observation_proposal_coverage": 0.8,
            "min_observation_outcome_coverage": 1.0,
            "min_observation_obligation_coverage": 0.95,
            "required_physical_metrics": ["area_um2"],
            "min_metric_evaluations": 2,
        },
    }
    report = {"metrics": {
        "proposal_coverage": 0.0,
        "outcome_coverage": 1.0,
        "obligation_coverage_min": 0.333,
        "harmful_outcome_rate": None,
        "ood_distance": {"max": 0.95},
        "physical_metrics": {"area_um2": {"evaluated": 0,
                                             "interval_coverage": None}},
    }}
    gate = validate_observation_gate(report, manifest)
    assert gate["passed"] is False
    assert {item["metric"] for item in gate["failures"]} >= {
        "proposal_coverage", "obligation_coverage_min",
        "harmful_outcome_rate", "physical_metrics.area_um2"}


def test_decision_gate_accepts_complete_observation_metrics():
    manifest = {
        "pre_registered_metrics": {
            "hard_ood_ceiling": 3.0,
            "min_interval_coverage": 0.8,
            "max_harmful_rate": 0.1,
        },
        "decision_gate": {
            "min_observation_proposal_coverage": 0.8,
            "min_observation_outcome_coverage": 1.0,
            "min_observation_obligation_coverage": 0.95,
            "required_physical_metrics": ["area_um2"],
            "min_metric_evaluations": 2,
        },
    }
    report = {"metrics": {
        "proposal_coverage": 1.0,
        "outcome_coverage": 1.0,
        "obligation_coverage_min": 1.0,
        "harmful_outcome_rate": 0.0,
        "ood_distance": {"max": 1.0},
        "physical_metrics": {"area_um2": {"evaluated": 2,
                                             "interval_coverage": 1.0}},
    }}
    assert validate_observation_gate(report, manifest)["passed"] is True
