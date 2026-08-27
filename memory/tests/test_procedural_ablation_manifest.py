"""Procedural ablation manifest is explicit, disjoint, and fail-closed."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from prepare_procedural_ablation_manifest import validate  # noqa: E402
from tehm.parametric.shadow_campaign import ShadowCampaignError  # noqa: E402


def _manifest():
    return json.loads((ROOT / "evaluation" /
                       "procedural_ablation_task_manifest_v1.json").read_text())


def test_procedural_manifest_freezes_four_component_tasks():
    result = validate(_manifest(), repo_root=ROOT.parent)
    assert result["validation"]["component_count"] == 4
    assert result["validation"]["future_lineage_count"] == 4
    assert result["validation"]["fixtures_materialized"] is True
    assert result["validation"]["executed_evidence"] is True
    assert result["firewall"]["disjoint"] is True


def test_procedural_manifest_rejects_firewall_overlap_and_wrong_arm():
    bad = _manifest()
    bad["tasks"][0]["lineage_id"] = bad["firewall"]["training_lineages"][0]
    with pytest.raises(ShadowCampaignError, match="overlaps protected firewall"):
        validate(bad)

    bad = _manifest()
    bad["tasks"][0]["ablated_arm"] = "M6"
    with pytest.raises(ShadowCampaignError, match="does not match component"):
        validate(bad)


def test_procedural_manifest_requires_real_source_before_executed_claim():
    bad = _manifest()
    bad["tasks"][0]["source_status"] = "PENDING_MATERIALIZATION"
    bad["tasks"][0]["fixture"] = 17
    with pytest.raises(ShadowCampaignError, match="pending fixture"):
        validate(bad)


def test_growth_manifest_allows_domain_specific_cohort_and_rule_selection():
    manifest = json.loads((ROOT / "evaluation" /
                           "procedural_growth_ablation_task_manifest_v2.json").read_text())
    result = validate(manifest, repo_root=ROOT.parent)
    assert result["validation"]["growth_manifest"] is True
    assert result["rule_selection"]["transformation_family"] == "RESET_RESTORE"
    assert {task["transformation_family"] for task in result["tasks"]} == {"RESET_RESTORE"}


def test_mechanism_manifest_allows_repeated_positive_path_tasks():
    manifest = json.loads((ROOT / "evaluation" /
                           "procedural_mechanism_ablation_task_manifest_v2.json").read_text())
    result = validate(manifest, repo_root=ROOT.parent)
    assert result["validation"]["mechanism_manifest"] is True
    assert result["validation"]["task_count"] == 6
    assert result["validation"]["future_lineage_count"] == 6
    assert result["acceptance"]["min_rule_coverage"] == 0.5
    assert sum(task["component"] == "full_mechanism_path"
               for task in result["tasks"]) == 2
