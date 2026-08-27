"""RTL transition -> causal shadow fragment tests."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tehm import db
from tehm.canonical.capture import capture
from tehm.causal import (
    build_intervention_pair, build_transition_causal_fragment,
    consolidate_causal_path,
    evaluate_replicated_effect,
    evaluate_causal_rule_evidence,
)
from tehm.dataset import assign_transition
from tehm.rtl.rtl_evidence import build_rtl_execution_record


PROJECT = Path(__file__).resolve().parent / "fixtures" / "rtl_projects" / "req_ack_bug"


def _captured(tmp_tehm):
    conn, store, _ = tmp_tehm
    record = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    receipt = capture(conn, store, record)
    return conn, receipt.transition_id


def test_rtl_fragment_is_deterministic_and_does_not_mutate_canonical(tmp_tehm):
    conn, transition_id = _captured(tmp_tehm)
    before = conn.execute("SELECT COUNT(*) FROM tehm_transitions").fetchone()[0]
    first = build_transition_causal_fragment(conn, transition_id)
    counts = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("tehm_causal_nodes", "tehm_causal_edges")
    }
    second = build_transition_causal_fragment(conn, transition_id)
    assert first.to_dict() == second.to_dict()
    assert counts == {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("tehm_causal_nodes", "tehm_causal_edges")
    }
    assert conn.execute("SELECT COUNT(*) FROM tehm_transitions").fetchone()[0] == before
    assert first.evidence_level == "L0_ASSOCIATION"
    assert all(transition_id in edge.evidence_refs for edge in first.edges)
    assert all(edge.learner_eligible for edge in first.edges)


def test_heldout_fragment_is_audit_only_and_cannot_consolidate(tmp_tehm):
    conn, transition_id = _captured(tmp_tehm)
    assign_transition(conn, transition_id=transition_id,
                      campaign_id="causal-heldout", split="heldout",
                      learner_eligible=False)
    conn.commit()
    fragment = build_transition_causal_fragment(
        conn, transition_id, campaign_id="causal-heldout")
    assert fragment.learner_eligible is False
    assert all(not edge.learner_eligible for edge in fragment.edges)
    with pytest.raises(ValueError, match="learner-ineligible"):
        consolidate_causal_path([fragment])


def test_learner_fragment_can_form_shadow_path(tmp_tehm):
    conn, transition_id = _captured(tmp_tehm)
    fragment = build_transition_causal_fragment(conn, transition_id)
    candidate = consolidate_causal_path(conn, [fragment])
    assert candidate.status == "shadow"
    assert candidate.evidence_level == "L0_ASSOCIATION"
    assert candidate.path_digest.startswith("sha1:")
    row = conn.execute(
        "SELECT evidence_level, status FROM tehm_causal_paths WHERE path_id=?",
        (candidate.path_id,)).fetchone()
    assert tuple(row) == ("L0_ASSOCIATION", "shadow")


def test_intervention_pair_requires_real_oracle_evidence(tmp_tehm):
    conn, store, _ = tmp_tehm
    first = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    control_id = capture(conn, store, first).transition_id
    second = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    second.record_id = "rtl:req_ack_fsm:alternate"
    second.action["payload"]["add_condition"] = "ready"
    treatment_id = capture(conn, store, second).transition_id
    pair = build_intervention_pair(control_id, treatment_id, conn=conn)
    assert pair.evidence_level == "L0_ASSOCIATION"
    assert pair.validity_status.startswith("INVALID_")
    assert conn.execute("SELECT COUNT(*) FROM tehm_intervention_pairs").fetchone()[0] == 1


def test_real_controlled_pair_creates_l2_shadow_edge(tmp_tehm):
    conn, store, tmp_path = tmp_tehm
    from tehm.rtl.rtl_oracle import IcarusOracle

    oracle = IcarusOracle()
    if not oracle.available:
        pytest.skip("Icarus unavailable")
    alternate = tmp_path / "req_ack_alternate"
    shutil.copytree(PROJECT, alternate)
    manifest_path = alternate / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["fix"]["add_condition"] = "ack && ack"
    manifest_path.write_text(json.dumps(manifest))
    from tehm.rtl.rtl_evidence import build_rtl_execution_record

    control = capture(conn, store, build_rtl_execution_record(
        PROJECT, oracle=oracle, store=store)).transition_id
    treatment = capture(conn, store, build_rtl_execution_record(
        alternate, oracle=oracle, store=store)).transition_id
    pair = build_intervention_pair(control, treatment, conn=conn)
    assert pair.validity_status == "VALID_CONTROLLED_PAIR"
    assert pair.evidence_level == "L2_CONTROLLED_INTERVENTION"
    assert pair.causal_edge_id
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_causal_edges WHERE evidence_level='L2_CONTROLLED_INTERVENTION'"
    ).fetchone()[0] == 1


def test_controlled_pair_rejects_cross_campaign_split(tmp_tehm):
    """A held-out treatment cannot be upgraded to an L2 learner edge."""
    conn, store, tmp_path = tmp_tehm
    from tehm.rtl.rtl_oracle import IcarusOracle

    oracle = IcarusOracle()
    if not oracle.available:
        pytest.skip("Icarus unavailable")
    alternate = tmp_path / "req_ack_cross_campaign"
    shutil.copytree(PROJECT, alternate)
    manifest_path = alternate / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["fix"]["add_condition"] = "ack && ack"
    manifest_path.write_text(json.dumps(manifest))
    control = capture(
        conn, store,
        build_rtl_execution_record(PROJECT, oracle=oracle, store=store),
        dataset_campaign_id="pair-training", dataset_split="training",
        dataset_learner_eligible=True).transition_id
    treatment = capture(
        conn, store,
        build_rtl_execution_record(alternate, oracle=oracle, store=store),
        dataset_campaign_id="pair-heldout", dataset_split="heldout",
        dataset_learner_eligible=False).transition_id
    pair = build_intervention_pair(
        control, treatment, conn=conn, campaign_id="pair-training")
    assert pair.validity_status.startswith("INVALID_")
    assert pair.oracle_equivalence["same_learner_campaign"] is False
    assert pair.causal_edge_id is None
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_causal_edges "
        "WHERE evidence_level='L2_CONTROLLED_INTERVENTION'"
    ).fetchone()[0] == 0


def test_replication_gate_does_not_upgrade_single_lineage(tmp_tehm):
    conn, store, tmp_path = tmp_tehm
    from tehm.rtl.rtl_oracle import IcarusOracle
    from tehm.rtl.rtl_evidence import build_rtl_execution_record

    oracle = IcarusOracle()
    if not oracle.available:
        pytest.skip("Icarus unavailable")
    alternate = tmp_path / "req_ack_alternate_replication"
    shutil.copytree(PROJECT, alternate)
    manifest_path = alternate / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["fix"]["add_condition"] = "ack && ack"
    manifest_path.write_text(json.dumps(manifest))
    control = capture(conn, store, build_rtl_execution_record(
        PROJECT, oracle=oracle, store=store)).transition_id
    treatment = capture(conn, store, build_rtl_execution_record(
        alternate, oracle=oracle, store=store)).transition_id
    build_intervention_pair(control, treatment, conn=conn)
    path = consolidate_causal_path(conn, [
        build_transition_causal_fragment(conn, control),
        build_transition_causal_fragment(conn, treatment),
    ])
    replication = evaluate_replicated_effect(conn, path.path_id)
    assert replication.eligible is False
    assert replication.evidence_level == "L1_EXECUTED_INTERVENTION"


def test_causal_authority_ignores_l2_edge_from_another_campaign(tmp_tehm):
    """A foreign campaign cannot satisfy controlled-intervention support."""
    conn, transition_id = _captured(tmp_tehm)
    fragment = build_transition_causal_fragment(conn, transition_id)
    path = consolidate_causal_path(conn, [fragment])
    # Construct a malformed/direct-SQL shadow state with an L2 edge, but bind
    # that edge to a different campaign.  The authority and replication
    # checks must both fail closed instead of treating global L2 evidence as
    # support for ``live``.
    conn.execute(
        "UPDATE tehm_causal_paths SET evidence_level='L2_CONTROLLED_INTERVENTION' "
        "WHERE path_id=?", (path.path_id,))
    conn.execute(
        """INSERT INTO tehm_causal_edges
           (causal_edge_id, source_node_id, relation_type, target_node_id,
            evidence_level, support_json, confidence_json, evidence_refs_json,
            campaign_id, learner_eligible, created_at)
           VALUES (?, 'foreign-source', 'SUPPORTS', 'foreign-target', ?, '{}',
                   '{}', ?, 'foreign-campaign', 1, '2026-01-01')""",
        ("foreign-l2", "L2_CONTROLLED_INTERVENTION",
         json.dumps([transition_id])))
    conn.commit()
    authority = evaluate_causal_rule_evidence(
        conn, path.path_id, campaign_id="live",
        required_level="L2_CONTROLLED_INTERVENTION", min_lineages=1)
    replication = evaluate_replicated_effect(
        conn, path.path_id, campaign_id="live", min_lineages=1)
    assert authority.eligible is False
    assert "controlled_intervention_support_missing" in authority.reason
    assert replication.eligible is False
    assert replication.reason == (
        "requires_controlled_pairs_and_disjoint_learner_lineages")


def test_causal_authority_and_replication_fail_closed_on_bad_source_json(
        tmp_tehm):
    conn, transition_id = _captured(tmp_tehm)
    fragment = build_transition_causal_fragment(conn, transition_id)
    path = consolidate_causal_path(conn, [fragment])
    conn.execute(
        "UPDATE tehm_causal_paths SET source_transitions_json=? WHERE path_id=?",
        ("not-json", path.path_id))
    conn.commit()
    authority = evaluate_causal_rule_evidence(
        conn, path.path_id, campaign_id="live")
    replication = evaluate_replicated_effect(conn, path.path_id)
    assert authority.eligible is False
    assert authority.reason == "malformed_source_transitions"
    assert replication.eligible is False
    assert replication.reason == "malformed_source_transitions"


def test_causal_authority_requires_complete_l2_source_coverage(tmp_tehm):
    """One controlled edge cannot stand in for an entire causal path."""
    conn, store, _ = tmp_tehm
    first_record = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    first_id = capture(conn, store, first_record).transition_id
    second_record = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    second_record.record_id = "rtl:req_ack_fsm:second-source"
    second_record.action["payload"]["add_condition"] = "ready"
    second_id = capture(conn, store, second_record).transition_id
    path = consolidate_causal_path(conn, [
        build_transition_causal_fragment(conn, first_id),
        build_transition_causal_fragment(conn, second_id),
    ])
    conn.execute(
        "UPDATE tehm_causal_paths SET evidence_level='L2_CONTROLLED_INTERVENTION' "
        "WHERE path_id=?", (path.path_id,))
    conn.execute(
        """INSERT INTO tehm_causal_edges
           (causal_edge_id, source_node_id, relation_type, target_node_id,
            evidence_level, support_json, confidence_json, evidence_refs_json,
            campaign_id, learner_eligible, created_at)
           VALUES ('partial-l2', 'source', 'SUPPORTS', 'target',
                   'L2_CONTROLLED_INTERVENTION', '{}', '{}', ?, 'live', 1,
                   '2026-01-01')""",
        (json.dumps([first_id]),))
    conn.commit()
    authority = evaluate_causal_rule_evidence(
        conn, path.path_id, campaign_id="live",
        required_level="L2_CONTROLLED_INTERVENTION", min_lineages=1)
    replication = evaluate_replicated_effect(
        conn, path.path_id, campaign_id="live", min_lineages=1)
    assert authority.eligible is False
    assert "controlled_intervention_source_coverage_incomplete" in authority.reason
    assert replication.eligible is False
