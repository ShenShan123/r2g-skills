from scripts.run_r3_orfs_interference_attribution import _child, _post_route
from scripts.run_r3_orfs_interference_shadow import _parent


def test_interference_child_is_structural_and_negative_applicability_bound():
    parent = _parent(("lineage-a", "lineage-b"))
    child = _child(parent)
    assert child.object_id != parent.object_id
    assert child.status == "shadow"
    assert child.negative_applicability[0]["core_utilization"] == "99"
    assert child.negative_applicability[0]["interference_signature"] == (
        "forced_memory_high_utilization"
    )


def test_p14_post_route_is_a_no_memory_veto():
    route = _post_route("case-a", "resolution-after")
    assert route.decision == "INAPPLICABLE"
    assert route.resolved_state_id == "resolution-after"
    assert route.selected_asset_ids == ()
    assert route.memory_budget == 0
    assert route.no_memory_budget == 1
