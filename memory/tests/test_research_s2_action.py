"""S2 staging keeps all policies and isolated config edits."""
from __future__ import annotations

import copy
import pytest
from tehm.evaluation import research_s2_action as subject


def _base():
    return {"adapter_id": "control", "designs": [
        {"design_id": "d1", "flow_binding": {
            "overrides": {"CORE_UTILIZATION": "95"}, "platform": "sky130hs"}},
        {"design_id": "d2", "flow_binding": {
            "overrides": {"CORE_UTILIZATION": "95"}, "platform": "sky130hs"}}]}


def _candidate(source, value):
    return {"source": source, "candidate_digest": "sha256:" + "a" * 64,
            "config_edits": {"CORE_UTILIZATION": value}}


def test_adapter_preserves_base_and_selects_one_design():
    base = _base()
    before = copy.deepcopy(base)
    result = subject._expected_adapter(
        base, design="d1", policy="tehm", index=1,
        candidate=_candidate("tehm", "40"))
    assert base == before
    assert [row["design_id"] for row in result["designs"]] == ["d1"]
    assert result["designs"][0]["flow_binding"]["overrides"] == {
        "CORE_UTILIZATION": "40"}


@pytest.mark.parametrize("edits", [
    {"VERILOG_FILES": "bad", "CORE_UTILIZATION": "40"},
    {"CORE_UTILIZATION": 40},
    {"PLACE_DENSITY_LB_ADDON": "0.2"},
])
def test_stage_rejects_unsupported_edits(edits):
    candidate = _candidate("tehm", "40")
    candidate["config_edits"] = edits
    with pytest.raises(subject.ResearchS2ActionError):
        subject._expected_adapter(
            _base(), design="d1", policy="tehm", index=1, candidate=candidate)


def test_stage_rejects_unknown_design_and_changed_control():
    with pytest.raises(subject.ResearchS2ActionError):
        subject._expected_adapter(
            _base(), design="missing", policy="tehm", index=1,
            candidate=_candidate("tehm", "40"))
    base = _base()
    base["designs"][0]["flow_binding"]["overrides"] = {
        "CORE_UTILIZATION": "40"}
    with pytest.raises(subject.ResearchS2ActionError):
        subject._expected_adapter(
            base, design="d1", policy="tehm", index=1,
            candidate=_candidate("tehm", "40"))


def test_candidate_records_keep_all_six_policy_tasks():
    def row(design):
        cold = _candidate("cold_start", "25")
        memory = _candidate("tehm", "40")
        return {"design_id": design, "source_group": "owner:" + design,
                "control_project": "/control/" + design,
                "control_audit_digest": "sha256:" + "b" * 64,
                "pools": {
                    "no_persistent_memory": {
                        "candidate_limit": 3, "ordered_candidates": [cold]},
                    "legacy_memory": {
                        "candidate_limit": 3, "ordered_candidates": [cold]},
                    "tehm": {
                        "candidate_limit": 3,
                        "ordered_candidates": [memory, cold]}}}
    result = subject._candidate_records({"rows": [row("d1"), row("d2")]})
    assert len(result) == 8
    assert len({(item["design_id"], item["policy"]) for item in result}) == 6


def test_pool_without_cold_start_fails_stage():
    row = {"design_id": "d1", "source_group": "owner:d1",
           "control_project": "/control/d1",
           "control_audit_digest": "sha256:" + "b" * 64,
           "pools": {policy: {"candidate_limit": 3,
                             "ordered_candidates": [_candidate("tehm", "40")]}
                     for policy in subject.POLICIES}}
    with pytest.raises(subject.ResearchS2ActionError, match="budget drifted"):
        subject._candidate_records({"rows": [row]})
