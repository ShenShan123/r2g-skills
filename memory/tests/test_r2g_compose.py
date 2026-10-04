"""Phase G2 composition selection (memory/tehm/retrieval/compose.py)."""
from __future__ import annotations

from tehm.retrieval.compose import compositions

SIT = {"v": "sit-v2", "check": "orfs_stage", "violation_class": "place", "error_code": "FLW-0024",
       "timeout": False, "platform": "sky130hd", "die_mode": "auto", "util_band": "gt60",
       "count_band": None, "perimeter_band": None}


def _trial(tid, design, edits, before, after, sit=SIT):
    return {"transition_id": tid, "design": design, "tier": "component_trial", "situation": sit,
            "knob_edits": edits, "severity_before": before, "severity_after": after,
            "strategy": "component:x", "verdict": "PASS" if after <= 0 else "FAIL"}


def test_prefers_tested_clearing_composition_and_translates_deltas():
    util = {"CORE_UTILIZATION": {"before": "90", "after": "60"}}
    both = {**util, "PLACE_DENSITY_LB_ADDON": {"before": "0.2", "after": "0.1"}}
    pool = [_trial("t1", "a", util, 0.25, 0.05),         # helps, does not clear alone
            _trial("t2", "a", both, 0.25, -0.12)]        # the combination clears
    out = compositions(pool, {**SIT, "util_band": "gt60"}, {"CORE_UTILIZATION": "87"}, 0.20)
    assert out and out[0]["tested"] and out[0]["predicted_severity"] == round(0.20 - 0.37, 4)
    assert out[0]["edits"] == {"CORE_UTILIZATION": "57", "PLACE_DENSITY_LB_ADDON": "0.1"}  # delta -30


def test_one_untested_union_of_single_dimension_components():
    util = {"CORE_UTILIZATION": {"before": "90", "after": "70"}}
    lb = {"PLACE_DENSITY_LB_ADDON": {"before": "0.2", "after": "0.1"}}
    pool = [_trial("t1", "a", util, 0.25, 0.08), _trial("t2", "b", lb, 0.25, 0.15)]
    out = compositions(pool, SIT, {"CORE_UTILIZATION": "87"}, 0.20)
    untested = [c for c in out if not c["tested"]]
    assert len(untested) == 1 and untested[0]["dims"] == ["CORE_UTILIZATION", "PLACE_DENSITY_LB_ADDON"]
    assert untested[0]["predicted_severity"] <= 0               # 0.20 - 0.17 - 0.10


def test_core_matching_and_nothing_usable():
    pool = [_trial("t1", "a", {"CORE_UTILIZATION": {"before": "90", "after": "60"}}, 0.25, -0.1)]
    other = {**SIT, "error_code": "PPL-0024"}
    assert compositions(pool, other, {"CORE_UTILIZATION": "87"}, 0.2) == []
    banded = {**SIT, "util_band": "31-60"}                     # bands are not part of the core
    assert compositions(pool, banded, {"CORE_UTILIZATION": "50"}, 0.2)
    assert compositions(pool, SIT, {"CORE_UTILIZATION": "87"}, None) == []
