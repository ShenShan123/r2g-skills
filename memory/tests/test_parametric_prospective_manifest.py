"""Prospective campaign firewall and candidate-action gates."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prepare_parametric_prospective_manifest import validate  # noqa: E402
from tehm.parametric.shadow_campaign import ShadowCampaignError  # noqa: E402
from tehm.physical.utility_contracts import (  # noqa: E402
    timing_relief_budgeted_v2_50_to_45,
    utility_contract_digest,
)


def _manifest():
    return {
        "version": "parametric-prospective-manifest-v1",
        "status": "PLANNED",
        "source_freeze": {"bundle_digest": "bundle", "manifest_digest": "manifest"},
        "firewall": {
            "training_lineages": ["train:a"],
            "calibration_lineages": ["cal:a"],
            "heldout_lineages": ["heldout:a"],
            "ab_lineages": ["ab:a"],
        },
        "cases": [
            {"case_id": "obs:0", "target_id": "target:0", "lineage_id": "future:a",
             "platform": "sky130hs", "family": "DENSITY_RELIEF", "phase": "observation",
             "graph_context_digest": "ctx:a", "candidate_actions": [{"knob": "a"}]},
            {"case_id": "dec:0:a", "target_id": "target:1", "lineage_id": "future:b",
             "platform": "sky130hs", "family": "DENSITY_RELIEF", "phase": "decision",
             "graph_context_digest": "ctx:b", "candidate_actions": [{"knob": "a"}, {"knob": "b"}]},
        ],
        "pre_registered_metrics": {
            "hard_ood_ceiling": 3.0,
            "min_interval_coverage": 0.8,
            "max_harmful_rate": 0.0,
            "min_obligation_coverage": 1.0,
        },
        "decision_gate": {
            "min_observation_proposal_coverage": 0.8,
            "min_observation_outcome_coverage": 1.0,
            "min_observation_obligation_coverage": 0.95,
            "required_physical_metrics": ["area_um2"],
            "min_metric_evaluations": 2,
        },
    }


def test_prospective_manifest_requires_disjoint_future_lineages():
    result = validate(_manifest())
    assert result["firewall"]["disjoint"] is True
    assert result["validation"]["future_lineage_count"] == 2

    bad = _manifest()
    bad["cases"][0]["lineage_id"] = "cal:a"
    with pytest.raises(ShadowCampaignError, match="overlaps protected firewall"):
        validate(bad)


def test_decision_manifest_requires_multiple_actions_and_hard_ood_ceiling():
    bad = _manifest()
    bad["cases"][1]["candidate_actions"] = [{"knob": "a"}]
    with pytest.raises(ShadowCampaignError, match=">=2 distinct"):
        validate(bad)

    bad = _manifest()
    bad["pre_registered_metrics"]["hard_ood_ceiling"] = 3.1
    with pytest.raises(ShadowCampaignError, match="cannot be widened"):
        validate(bad)

    bad = _manifest()
    del bad["decision_gate"]
    with pytest.raises(ShadowCampaignError, match="decision_gate"):
        validate(bad)


def test_v2_50_to_45_contract_binding_is_independent():
    manifest = _manifest()
    contract = timing_relief_budgeted_v2_50_to_45()
    manifest.update({
        "contract_id": contract["contract_id"],
        "utility_contract_digest": utility_contract_digest(contract),
        "action_signature": {
            "domain": "flow.CONFIG_DELTA",
            "family": "DENSITY_RELIEF",
            "config_edits": {"CORE_UTILIZATION": "45"},
            "operation_point": "50->45",
        },
    })
    result = validate(manifest)
    assert result["contract_id"] == contract["contract_id"]
    assert result["action_signature"]["config_edits"] == {"CORE_UTILIZATION": "45"}

    bad = dict(manifest)
    bad["utility_contract_digest"] = utility_contract_digest(
        timing_relief_budgeted_v2_50_to_45())[:-1] + "0"
    with pytest.raises(ShadowCampaignError, match="digest"):
        validate(bad)
