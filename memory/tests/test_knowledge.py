"""P3 intervention-grounded Mechanism Knowledge shadow/candidate tests."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from tehm import db
from tehm.canonical.capture import capture
from tehm.causal import build_transition_causal_fragment, consolidate_causal_path
from tehm.knowledge import (
    MechanismKnowledge, build_knowledge_from_path, evaluate_applicability,
    evaluate_knowledge_authority, ensure_knowledge_schema, get_knowledge,
    get_knowledge_status, register_knowledge, resolve_knowledge,
    merge_knowledge, revise_knowledge, set_knowledge_status, split_knowledge,
)
from tehm.rtl.rtl_evidence import build_rtl_execution_record
from tehm.state import StateResolutionError


PROJECT = Path(__file__).resolve().parent / "fixtures" / "rtl_projects" / "req_ack_bug"


def _record(store, *, record_id: str, action_value: str = "ack",
            lineage_id: str | None = None):
    record = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    record.record_id = record_id
    record.lineage_id = lineage_id or record.lineage_id
    record.action["payload"]["add_condition"] = action_value
    return record


def _capture(tmp_tehm, *, record_id: str, action_value: str = "ack",
             campaign: str = "live", split: str = "training",
             learner: bool = True, lineage_id: str | None = None):
    conn, store, _ = tmp_tehm
    receipt = capture(
        conn, store, _record(store, record_id=record_id,
                              action_value=action_value, lineage_id=lineage_id),
        dataset_campaign_id=campaign, dataset_split=split,
        dataset_learner_eligible=learner)
    return conn, receipt.transition_id


def _claim(*, knowledge_id: str = "mk_manual", version: int = 1,
           status: str = "shadow", evidence_level: str = "L1_EXECUTED_INTERVENTION",
           intervention: dict | None = None) -> MechanismKnowledge:
    return MechanismKnowledge(
        knowledge_id=knowledge_id, version=version,
        mechanism_family="HANDSHAKE_COMPLETION",
        compatibility_profile="rtl.fsm.single_guard.v1",
        antecedent={"failure": "completion_not_observed"},
        intervention=intervention or {"family": "GUARD_RESTORE"},
        mediated_effects=({"effect": "legal_transition"},),
        expected_outcome={"outcome": "PASS"},
        positive_applicability=({"mechanism_family": "HANDSHAKE_COMPLETION",
                                 "compatibility_profile": "rtl.fsm.single_guard.v1"},),
        negative_applicability=({"priority_conflict": True},),
        preserved_obligations=("target_trace_pass",),
        known_failure_modes=("ambiguous_target_binding",),
        causal_path_ids=("causal_path_manual",),
        evidence_level=evidence_level, support_lineages=("lineage-a",),
        status=status)


def _path_from_two_training_transitions(tmp_tehm):
    conn, first_id = _capture(tmp_tehm, record_id="knowledge-first",
                               action_value="ack")
    _, second_id = _capture(tmp_tehm, record_id="knowledge-second",
                            action_value="ready")
    fragments = [
        build_transition_causal_fragment(conn, first_id, campaign_id="live"),
        build_transition_causal_fragment(conn, second_id, campaign_id="live"),
    ]
    return conn, consolidate_causal_path(
        conn, fragments, campaign_id="live"), first_id, second_id


def test_path_builds_shadow_knowledge_without_canonical_mutation(tmp_tehm):
    conn, path, _, _ = _path_from_two_training_transitions(tmp_tehm)
    before = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("tehm_states", "tehm_transitions", "tehm_causal_paths")
    }
    claim = build_knowledge_from_path(conn, path.path_id)
    assert claim.status == "shadow"
    assert claim.evidence_level == "L0_ASSOCIATION"
    receipt = register_knowledge(conn, claim)
    assert receipt.status == "shadow"
    assert get_knowledge(conn, claim.knowledge_id, claim.version).to_dict() == claim.to_dict()
    assert {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in before
    } == before
    assert conn.execute(
        "SELECT value FROM tehm_meta WHERE key='schema_version'").fetchone()[0] == "tehm-v4"


def test_builder_keeps_l0_l1_shadow_only(tmp_tehm):
    conn, path, _, _ = _path_from_two_training_transitions(tmp_tehm)
    with pytest.raises(ValueError, match="shadow-only"):
        build_knowledge_from_path(conn, path.path_id, status="candidate")
    with pytest.raises(ValueError, match="cannot grant validated"):
        build_knowledge_from_path(conn, path.path_id, status="validated")


def test_negative_context_does_not_leak_heldout_evidence(tmp_tehm):
    conn, source_id = _capture(tmp_tehm, record_id="knowledge-source",
                               action_value="ack")
    _capture(tmp_tehm, record_id="knowledge-heldout", action_value="other",
             campaign="audit", split="heldout", learner=False)
    fragment = build_transition_causal_fragment(conn, source_id, campaign_id="live")
    path = consolidate_causal_path(conn, [fragment], campaign_id="live")
    claim = build_knowledge_from_path(conn, path.path_id)
    assert claim.negative_applicability == ()


def test_candidate_authority_is_explicit_and_never_auto_promoted(tmp_tehm):
    conn, _, _ = tmp_tehm
    candidate = _claim(status="candidate", evidence_level="L2_CONTROLLED_INTERVENTION")
    register_knowledge(conn, candidate, evidence_refs=[])
    authority = evaluate_knowledge_authority(conn, candidate)
    assert authority.eligible is False
    assert authority.gates["no_automatic_promotion"] is True
    assert "promoted" not in authority.gates
    assert get_knowledge_status(
        conn, knowledge_id=candidate.knowledge_id, version=1)["status"] == "candidate"
    with pytest.raises(ValueError, match="eligible authority"):
        set_knowledge_status(
            conn, knowledge_id=candidate.knowledge_id, version=1,
            status="validated", authority_receipt=authority)
    with pytest.raises(StateResolutionError, match="no promoted runtime status"):
        resolve_knowledge(conn, mode="production")


def test_registry_replay_is_immutable_and_content_bound(tmp_tehm):
    conn, _, _ = tmp_tehm
    claim = _claim()
    first = register_knowledge(conn, claim, evidence_refs=[])
    second = register_knowledge(conn, claim, evidence_refs=[])
    assert first.to_dict() == second.to_dict()
    forged = replace(claim, intervention={"family": "FORGED"})
    with pytest.raises(ValueError, match="immutable and conflicts"):
        register_knowledge(conn, forged, evidence_refs=[])
    conn.execute(
        "UPDATE tehm_mechanism_knowledge SET content_digest='sha256:forged' "
        "WHERE knowledge_id=? AND version=?", (claim.knowledge_id, claim.version))
    conn.commit()
    with pytest.raises(ValueError, match="digest mismatch"):
        get_knowledge(conn, claim.knowledge_id, claim.version)


def test_applicability_requires_positive_and_rejects_negative_context():
    claim = _claim()
    good = evaluate_applicability(claim, {
        "mechanism_family": "HANDSHAKE_COMPLETION",
        "compatibility_profile": "rtl.fsm.single_guard.v1",
    })
    assert good.eligible is True
    blocked = evaluate_applicability(claim, {
        "mechanism_family": "HANDSHAKE_COMPLETION",
        "compatibility_profile": "rtl.fsm.single_guard.v1",
        "priority_conflict": True,
    })
    assert blocked.eligible is False
    assert blocked.reason == "negative_applicability"
    missing = evaluate_applicability(claim, {
        "mechanism_family": "HANDSHAKE_COMPLETION",
    })
    assert missing.eligible is False
    assert missing.reason == "compatibility_profile_mismatch"


def test_revision_registers_child_and_shadow_supersession(tmp_tehm):
    conn, _, _ = tmp_tehm
    parent = _claim()
    register_knowledge(conn, parent, evidence_refs=[])
    child = replace(parent, version=2, intervention={"family": "GUARD_RESTORE", "variant": "v2"})
    receipt = revise_knowledge(
        conn, parent_object_id=parent.object_id, replacement=child,
        evidence_refs=[{
            "evidence_type": "manual_review", "evidence_id": "review-1",
            "split": "training", "lineage_id": "lineage-a",
            "evidence_level": "L1_EXECUTED_INTERVENTION",
        }])
    assert receipt.shadow_only is True
    assert receipt.parent_object_id == parent.object_id
    resolved = resolve_knowledge(conn, {
        "target_scope": "global",
        "mechanism_family": "HANDSHAKE_COMPLETION",
        "compatibility_profile": "rtl.fsm.single_guard.v1",
    })
    assert resolved.active_knowledge == (child.object_id,)
    assert parent.object_id not in resolved.active_knowledge
    assert get_knowledge_status(
        conn, knowledge_id=child.knowledge_id, version=2)["status"] == "shadow"


def test_structural_specialization_changes_identity_and_preserves_parent(tmp_tehm):
    conn, _, _ = tmp_tehm
    parent = _claim(knowledge_id="mk_structural_parent")
    register_knowledge(conn, parent, evidence_refs=[])
    child = replace(parent, knowledge_id="mk_structural_child", version=1,
                    intervention={"family": "GUARD_RESTORE", "variant": "narrow"})
    receipt = revise_knowledge(
        conn, parent_object_id=parent.object_id, replacement=child,
        operation="SPECIALIZE", evidence_refs=[{
            "evidence_type": "manual_review", "evidence_id": "specialize-witness",
            "split": "training", "lineage_id": "lineage-a",
            "evidence_level": "L1_EXECUTED_INTERVENTION",
        }])
    assert receipt.operation == "SPECIALIZE"
    relation = conn.execute(
        "SELECT relation_type FROM tehm_memory_relations WHERE relation_id=?",
        (receipt.relation_id,)).fetchone()
    assert relation[0] == "SPECIALIZES"
    resolved = resolve_knowledge(conn, {
        "target_scope": "global", "mechanism_family": parent.mechanism_family,
        "compatibility_profile": parent.compatibility_profile,
    })
    assert set(resolved.active_knowledge) == {parent.object_id, child.object_id}


def test_split_and_merge_require_partition_and_multi_parent_witness(tmp_tehm):
    conn, _, _ = tmp_tehm
    parent = _claim(knowledge_id="mk_split_parent")
    register_knowledge(conn, parent, evidence_refs=[])
    child_a = replace(parent, knowledge_id="mk_split_a", version=1,
                      intervention={"family": "GUARD_RESTORE", "variant": "a"})
    child_b = replace(parent, knowledge_id="mk_split_b", version=1,
                      intervention={"family": "GUARD_RESTORE", "variant": "b"})
    with pytest.raises(ValueError, match="partition witness"):
        split_knowledge(conn, parent_object_id=parent.object_id,
                        children=(child_a, child_b), partition_evidence={})
    split = split_knowledge(
        conn, parent_object_id=parent.object_id, children=(child_a, child_b),
        partition_evidence={child_a.object_id: ("partition-a",),
                            child_b.object_id: ("partition-b",)},
        evidence_refs=[])
    assert len(split.relation_ids) == 2
    parent_b = replace(parent, knowledge_id="mk_merge_parent", version=1,
                       intervention={"family": "GUARD_RESTORE", "variant": "parent-b"})
    register_knowledge(conn, parent_b, evidence_refs=[])
    merged = replace(parent, knowledge_id="mk_merged", version=1,
                     intervention={"family": "GUARD_RESTORE", "variant": "merged"})
    with pytest.raises(ValueError, match="witness"):
        merge_knowledge(conn, parent_object_ids=(parent.object_id, parent_b.object_id),
                        replacement=merged)
    receipt = merge_knowledge(
        conn, parent_object_ids=(parent.object_id, parent_b.object_id),
        replacement=merged,
        merge_witness={parent.object_id: ("merge-a",),
                       parent_b.object_id: ("merge-b",)}, evidence_refs=[])
    assert len(receipt.relation_ids) == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_memory_relations WHERE relation_type='GENERALIZES'"
    ).fetchone()[0] == 2


def test_knowledge_schema_is_lazy_and_keeps_v4_version(tmp_path):
    conn = db.connect(tmp_path / "old-v4.sqlite")
    conn.execute("CREATE TABLE tehm_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO tehm_meta VALUES ('schema_version', 'tehm-v4')")
    ensure_knowledge_schema(conn)
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        ("tehm_mechanism_knowledge",)).fetchone() is not None
    assert conn.execute(
        "SELECT value FROM tehm_meta WHERE key='schema_version'").fetchone()[0] == "tehm-v4"
    conn.close()
