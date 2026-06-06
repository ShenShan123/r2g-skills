"""Unit tests for the autonomous fix-log manager (pure helpers)."""
from __future__ import annotations
import json
import fix_log_manager as flm


def test_canonical_key_merges_within_tolerance():
    e1 = {"check_type": "drc", "violation_class": "M2_ANTENNA",
          "strategy": "antenna_density_relief",
          "cumulative_config_json": json.dumps({"CORE_UTILIZATION": "15"})}
    e2 = dict(e1, cumulative_config_json=json.dumps({"CORE_UTILIZATION": "14"}))  # within 15%
    e3 = dict(e1, cumulative_config_json=json.dumps({"CORE_UTILIZATION": "5"}))   # far
    assert flm.canonical_action_key(e1) == flm.canonical_action_key(e2)
    assert flm.canonical_action_key(e1) != flm.canonical_action_key(e3)


def test_canonical_key_keeps_violation_class_distinct():
    base = {"check_type": "drc", "strategy": "antenna_diode_repair",
            "cumulative_config_json": "{}"}
    assert (flm.canonical_action_key(dict(base, violation_class="M2_ANTENNA"))
            != flm.canonical_action_key(dict(base, violation_class="M3_ANTENNA")))


def test_dedup_collapses_repeats_keeps_last():
    evs = [{"iter": 1, "check_type": "drc", "violation_class": "M2_ANTENNA",
            "strategy": "antenna_diode_repair", "cumulative_config_json": "{}", "after_count": 9},
           {"iter": 2, "check_type": "drc", "violation_class": "M2_ANTENNA",
            "strategy": "antenna_diode_repair", "cumulative_config_json": "{}", "after_count": 3}]
    out = flm.dedup_events_by_action(evs)
    assert len(out) == 1 and out[0]["after_count"] == 3   # freshest wins


def test_bound_rule_details_caps_samples():
    b = flm.bound_rule_details({"samples": list(range(100))}, top_n=20)
    assert b["total"] == 100 and len(b["samples"]) == 20 and b["truncated"] is True
