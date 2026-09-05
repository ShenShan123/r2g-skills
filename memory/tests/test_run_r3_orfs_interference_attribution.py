import pytest

from contracts import MemoryRoutingDecision
from scripts.run_r3_orfs_interference_attribution import (
    _child, _routing_query, audit_router_outputs, OrfsInterferenceAttributionError,
)
from scripts.run_r3_orfs_interference_challenge import _candidate
from scripts.run_r3_orfs_interference_shadow import _parent
from tehm.knowledge.registry import register_knowledge
from tehm.knowledge.revision import revise_knowledge
from tehm.retrieval.memory_router import route_memory


def test_interference_child_is_structural_and_negative_applicability_bound():
    parent = _parent(("lineage-a", "lineage-b"))
    child = _child(parent)
    assert child.object_id != parent.object_id
    assert child.status == "shadow"
    assert child.negative_applicability[0]["core_utilization"] == "99"
    assert child.negative_applicability[0]["interference_signature"] == (
        "forced_memory_high_utilization"
    )


def _inputs():
    return {"case_id": "case-a", "platform": "sky130hs", "target_check": "route",
            "source_digest": "sha256:" + "a" * 64}, _candidate("gcd", core_utilization="99")


def test_p14_route_is_derived_from_state_not_an_expected_veto(tmp_tehm):
    conn, _, _ = tmp_tehm
    parent = _parent(("lineage-a", "lineage-b"))
    register_knowledge(conn, parent, evidence_refs=[])
    case, candidate = _inputs()
    expected_veto = MemoryRoutingDecision(
        decision="INAPPLICABLE", resolved_state_id="invented-state",
        selected_rule_ids=(), selected_path_ids=(), selected_asset_ids=(),
        applicability={}, causal_support={}, risk={}, abstain_reasons=(),
        no_memory_budget=1, memory_budget=0)
    audit = audit_router_outputs(conn, [case], {"case-a": candidate}, parent,
                                 {"case-a": expected_veto})
    assert audit["eligible"] is False
    assert audit["cases"]["case-a"]["actual"]["decision"] == "NO_SKILL"
    assert audit["cases"]["case-a"]["actual"]["no_skill_reason"] == "NO_MATCH"
    before = audit["cases"]["case-a"]["actual"]["resolved_state_id"]
    revise_knowledge(conn, parent_object_id=parent.object_id, replacement=_child(parent),
                     operation="SPECIALIZE", target_scope="global", evidence_refs=[])
    after = audit_router_outputs(conn, [case], {"case-a": candidate}, parent,
                                 {"case-a": expected_veto})
    assert after["eligible"] is False
    assert after["cases"]["case-a"]["actual"]["decision"] == "NO_SKILL"
    assert after["cases"]["case-a"]["actual"]["resolved_state_id"] != before


def test_actual_router_receipt_replays_without_database_writes(tmp_tehm):
    conn, _, _ = tmp_tehm
    parent = _parent(("lineage-a", "lineage-b"))
    case, candidate = _inputs()
    query = _routing_query(case, candidate, parent)
    actual = route_memory(conn, query, no_memory_budget=1, memory_budget=1)
    before = conn.total_changes
    audit = audit_router_outputs(conn, [case], {"case-a": candidate}, parent, {"case-a": actual})
    assert audit["eligible"] is True
    assert conn.total_changes == before
    assert not conn.in_transaction


def test_query_does_not_inject_the_expected_interference_predicate():
    case, candidate = _inputs()
    case.update({"routing_decision": "INAPPLICABLE", "memory_interference": True})
    query = _routing_query(case, candidate, _parent(("a", "b")))
    assert "memory_interference" not in query.query_plan
    assert "interference_signature" not in query.query_plan
    assert "core_utilization" not in query.query_plan
    assert query.query_plan["proposed_action"]["payload"]["config_edits"] == {"CORE_UTILIZATION": "99"}


def test_router_audit_does_not_accept_empty_coverage(tmp_tehm):
    conn, _, _ = tmp_tehm
    with pytest.raises(OrfsInterferenceAttributionError, match="non-empty case coverage"):
        audit_router_outputs(conn, [], {}, _parent(("a", "b")), {})
