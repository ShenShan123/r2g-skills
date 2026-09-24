"""Candidate-derived S1 treatment planning must fail closed."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from tehm.evaluation import research_s1_action as action


TASK = {"memory_arm": {"permitted_action_family": "flow.CONFIG_DELTA",
                       "permitted_config_keys": ["CORE_UTILIZATION"]}}


def _row(edits: dict[str, str]) -> dict:
    return {"target_failure": True, "routing": {"decision": "CONSIDER"},
            "selection": {"decision": "SELECT"},
            "candidate": {"evaluation_only": True,
                          "candidate_digest": "sha256:" + "a" * 64,
                          "concrete_action": {"domain": "flow.CONFIG_DELTA",
                                              "payload": {"config_edits": edits}}}}


def test_selected_action_comes_from_candidate() -> None:
    assert action._selected(_row({"CORE_UTILIZATION": "40"}), TASK) == (
        {"CORE_UTILIZATION": "40"}, "sha256:" + "a" * 64)


def test_selected_rejects_unregistered_config_key() -> None:
    with pytest.raises(action.ResearchS1ActionError, match="key contract"):
        action._selected(_row({"PLACE_DENSITY": "0.4"}), TASK)


def test_selected_requires_real_route_and_selector() -> None:
    row = _row({"CORE_UTILIZATION": "40"})
    row["routing"]["decision"] = "NO_SKILL"
    with pytest.raises(action.ResearchS1ActionError, match="route"):
        action._selected(row, TASK)


def test_expected_adapter_changes_only_selected_config_and_metadata() -> None:
    original = {"adapter_id": "control", "designs": [
        {"design_id": "a", "flow_binding": {
            "overrides": {"CORE_UTILIZATION": "95"}, "authority_note": "control"},
         "ordered_filelist": ["rtl/a.v"]}]}
    frozen = copy.deepcopy(original)
    result = action._expected_adapter(original, [{
        "design_id": "a", "candidate_digest": "sha256:" + "a" * 64,
        "config_edits": {"CORE_UTILIZATION": "40"}}])
    assert result["designs"][0]["flow_binding"]["overrides"] == {
        "CORE_UTILIZATION": "40"}
    assert result["designs"][0]["ordered_filelist"] == ["rtl/a.v"]
    assert original == frozen


def test_prepare_refuses_existing_output_before_replay(tmp_path: Path) -> None:
    with pytest.raises(action.ResearchS1ActionError, match="overwrite"):
        action.prepare_s1_treatments(preflight=tmp_path, epoch=tmp_path,
                                     output=tmp_path)
