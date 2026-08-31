"""P5 shadow memory routing and NO_SKILL firewall tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from contracts import MemoryQuery, MemoryRoutingDecision, RepairContext
from tehm.knowledge import MechanismKnowledge, register_knowledge
from tehm.state import StateResolutionError, record_relation
from tehm.retrieval.memory_router import route_memory
from tehm_backend import TehmMemoryBackend


def _query(**extra) -> MemoryQuery:
    plan = {
        "mechanism_family": "HANDSHAKE_COMPLETION",
        "compatibility_profile": "rtl.fsm.single_guard.v1",
    }
    plan.update(extra)
    return MemoryQuery(query_plan=plan)


def test_fresh_router_keeps_no_skill_arm_and_does_not_write_canonical_rows(tmp_tehm):
    conn, _, _ = tmp_tehm
    before = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in (
            "tehm_states", "tehm_transitions", "tehm_rules",
            "tehm_mechanism_knowledge", "tehm_assets",
        )
    }
    decision = route_memory(conn, _query())
    assert decision.decision == "NO_SKILL"
    assert decision.no_memory_budget == 3
    assert decision.memory_budget == 0
    assert decision.selected_rule_ids == ()
    assert decision.selected_path_ids == ()
    assert decision.selected_asset_ids == ()
    assert {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in before
    } == before


def test_routing_receipt_is_content_addressed_and_replayable(tmp_tehm):
    conn, _, _ = tmp_tehm
    decision = route_memory(conn, _query())
    payload = {**decision.to_dict(), "decision_digest": decision.decision_digest}
    replay = MemoryRoutingDecision.from_dict(payload)
    assert replay == decision
    assert replay.routing_receipt_id == decision.routing_receipt_id


def test_router_abstains_on_unresolved_state(tmp_tehm):
    conn, _, _ = tmp_tehm
    record_relation(
        conn, source_type="rule", source_id="missing-source",
        relation_type="SUPERSEDES", target_type="rule",
        target_id="missing-target", evidence_refs=("unresolved-witness",),
    )
    decision = route_memory(conn, _query())
    assert decision.decision == "ABSTAIN"
    assert decision.resolved_state_id == "UNRESOLVED"
    assert decision.memory_budget == 0
    assert any(reason.startswith("state_unresolved:")
               for reason in decision.abstain_reasons)


def test_router_ood_abstains_and_never_spends_memory_budget(tmp_tehm):
    conn, _, _ = tmp_tehm
    decision = route_memory(
        conn, _query(out_of_distribution=True), no_memory_budget=3,
        memory_budget=2,
    )
    assert decision.decision == "ABSTAIN"
    assert decision.no_memory_budget == 5
    assert decision.memory_budget == 0
    assert decision.abstain_reasons == ("out_of_distribution",)


def test_router_rejects_production_mode_until_authority_gates_exist(tmp_tehm):
    conn, _, _ = tmp_tehm
    with pytest.raises(StateResolutionError, match="shadow-only"):
        route_memory(conn, _query(), mode="production")


def test_candidate_knowledge_remains_offline_only(tmp_tehm):
    conn, _, _ = tmp_tehm
    claim = MechanismKnowledge(
        knowledge_id="mk_router_candidate", version=1,
        mechanism_family="HANDSHAKE_COMPLETION",
        compatibility_profile="rtl.fsm.single_guard.v1",
        antecedent={"failure": "completion_not_observed"},
        intervention={"family": "GUARD_RESTORE"},
        mediated_effects=({"effect": "legal_transition"},),
        expected_outcome={"outcome": "PASS"},
        positive_applicability=({
            "mechanism_family": "HANDSHAKE_COMPLETION",
            "compatibility_profile": "rtl.fsm.single_guard.v1",
        },),
        negative_applicability=(), preserved_obligations=(),
        known_failure_modes=(), causal_path_ids=("causal-path-not-yet-validated",),
        evidence_level="L2_CONTROLLED_INTERVENTION",
        support_lineages=("lineage-router",), status="candidate",
    )
    register_knowledge(conn, claim, evidence_refs=[])
    decision = route_memory(conn, _query(), memory_budget=2)
    assert decision.decision == "NO_SKILL"
    assert decision.memory_budget == 0
    assert decision.applicability["validated_knowledge_count"] == 0
    assert decision.abstain_reasons == ("no_validated_mechanism_knowledge",)


def test_validated_but_unresolved_applicability_abstains(tmp_tehm):
    conn, _, _ = tmp_tehm
    claim = MechanismKnowledge(
        knowledge_id="mk_router_applicability", version=1,
        mechanism_family="HANDSHAKE_COMPLETION",
        compatibility_profile="rtl.fsm.single_guard.v1",
        antecedent={}, intervention={"family": "GUARD_RESTORE"},
        mediated_effects=(), expected_outcome={"outcome": "PASS"},
        positive_applicability=({
            "mechanism_family": "HANDSHAKE_COMPLETION",
            "compatibility_profile": "rtl.fsm.single_guard.v1",
            "required_signal": "ack",
        },),
        negative_applicability=(), preserved_obligations=(),
        known_failure_modes=(), causal_path_ids=("path-not-yet-bound",),
        evidence_level="L2_CONTROLLED_INTERVENTION",
        support_lineages=("lineage-router",), status="candidate",
    )
    register_knowledge(conn, claim, evidence_refs=[])
    # This direct status mutation is test setup only; the production lifecycle
    # requires an authority receipt before a claim can become validated.
    conn.execute(
        "UPDATE tehm_mechanism_knowledge_status SET status='validated' "
        "WHERE knowledge_id=? AND version=1 AND target_scope='global'",
        (claim.knowledge_id,),
    )
    conn.commit()
    decision = route_memory(conn, _query(), memory_budget=2)
    assert decision.decision == "ABSTAIN"
    assert decision.memory_budget == 0
    assert any("positive_applicability_missing" in reason
               for reason in decision.abstain_reasons)


def test_backend_exposes_shadow_router_seam(tmp_path: Path):
    backend = TehmMemoryBackend(
        db_path=tmp_path / "tehm.sqlite",
        artifact_root=tmp_path / "artifacts",
    )
    decision = backend.route_memory(
        RepairContext(
            design_id="router-design", check="route",
            compatibility_profile="rtl.fsm.single_guard.v1",
            mechanism_signature={"mechanism_family": "HANDSHAKE_COMPLETION"},
        ),
        no_memory_budget=3,
        memory_budget=0,
    )
    assert decision.decision == "NO_SKILL"
    assert backend.retrieve_assets(decision) == []
    assert backend.record_memory_outcome(decision.routing_receipt_id, {}) is None
    backend.close()
