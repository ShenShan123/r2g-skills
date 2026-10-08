import copy

import pytest

import diagnose_signoff_fix as dsf


def plan():
    return {
        "lifecycle_gate_ok": True,
        "routing_context": {"symptom_id": "04d38c5a585fd332", "platform": "sky130hd"},
        "strategies": [
            {"id": "density_relief", "auto_apply": True},
            {"id": "pin_side_rebalance", "auto_apply": True,
             "requires_ab_promotion": True, "lifecycle_status": "promoted",
             "config_edits": {"PLACE_PINS_ARGS": "-exclude right:*"},
             "geometry_evidence": {"side": "right", "rule": "m3.2",
                                   "edge_count": 5, "edge_fraction": 1.0}},
        ],
    }


@pytest.mark.parametrize("side", ["left", "right", "top", "bottom"])
def test_promoted_geometric_action_is_prioritized(side):
    p = plan()
    pin = p["strategies"][1]
    pin["geometry_evidence"]["side"] = side
    pin["config_edits"]["PLACE_PINS_ARGS"] = f"-exclude {side}:*"
    dsf._prioritize_geometric_pin_strategy(p)
    assert dsf._live_auto_strategy(p) is pin
    assert dsf._live_auto_strategy(p, rank_first="density_relief")["id"] == "density_relief"


@pytest.mark.parametrize("status", [None, "candidate", "shadow", "parked"])
def test_no_priority_for_unpromoted_even_with_candidate_authorization(status):
    p = plan()
    p["strategies"][1].update(lifecycle_status=status, candidate_attempt_authorized=True)
    dsf._prioritize_geometric_pin_strategy(p)
    assert p["strategies"][0]["id"] == "density_relief"


@pytest.mark.parametrize("field,value", [
    ("edge_fraction", 0.79), ("edge_fraction", float("nan")),
    ("edge_fraction", 1.1), ("edge_count", 1), ("edge_count", True),
    ("rule", "m2.1"), ("side", "top"),
])
def test_bad_geometry_or_side_mismatch_keeps_existing_order(field, value):
    p = plan()
    p["strategies"][1]["geometry_evidence"][field] = value
    dsf._prioritize_geometric_pin_strategy(p)
    assert p["strategies"][0]["id"] == "density_relief"


@pytest.mark.parametrize("gate", ["lifecycle", "policy", "dead", "platform", "symptom"])
def test_priority_respects_existing_guards(gate):
    p = plan()
    if gate == "lifecycle":
        p["lifecycle_gate_ok"] = False
    elif gate == "policy":
        p["action_policy_gate_ok"] = False
    elif gate == "dead":
        p["strategies"][1]["dead_here"] = 2
    elif gate == "platform":
        p["routing_context"]["platform"] = "asap7"
    else:
        p["routing_context"]["symptom_id"] = "unrelated"
    before = copy.deepcopy(p["strategies"])
    dsf._prioritize_geometric_pin_strategy(p)
    assert p["strategies"] == before
