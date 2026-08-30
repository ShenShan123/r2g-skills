"""RTL transition -> causal shadow fragment tests."""
from __future__ import annotations

import hashlib
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
from tehm.causal.mechanism import load_transition_facts
from tehm.causal.edges import CausalEdge
from tehm.causal.path_builder import (
    causal_path_digest, validate_persisted_path_row,
)
from tehm.causal.witness import (
    learner_edge_transition_coverage, parse_source_transition_ids,
)
from tehm.dataset import assign_transition
from tehm.rtl.rtl_evidence import build_rtl_execution_record


PROJECT = Path(__file__).resolve().parent / "fixtures" / "rtl_projects" / "req_ack_bug"


def _captured(tmp_tehm):
    conn, store, _ = tmp_tehm
    record = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    receipt = capture(conn, store, record)
    return conn, receipt.transition_id


def _set_path_evidence_level(conn, path_id: str, level: str) -> None:
    """Keep a direct-SQL authority fixture internally digest-consistent."""
    row = conn.execute(
        "SELECT * FROM tehm_causal_paths WHERE path_id=?", (path_id,)
    ).fetchone()
    support = json.loads(row["support_json"])
    digest = causal_path_digest(
        mechanism_family=row["mechanism_family"],
        compatibility_profile=row["compatibility_profile"],
        evidence_level=level,
        source_transition_ids=json.loads(row["source_transitions_json"]),
        node_ids=json.loads(row["ordered_nodes_json"]),
        edge_ids=json.loads(row["ordered_edges_json"]), support=support)
    conn.execute(
        "UPDATE tehm_causal_paths SET evidence_level=?, path_digest=? "
        "WHERE path_id=?", (level, digest, path_id))


def test_causal_edge_rejects_weakly_typed_learner_flag():
    with pytest.raises(ValueError, match="learner_eligible"):
        CausalEdge(
            "state", "SUPPORTS", "outcome", "L0_ASSOCIATION",
            {"transition_id": "t"}, {"level": "L0_ASSOCIATION"},
            ("t",), "live", "false")


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


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("action_json", "not-json", "action JSON is malformed"),
        ("observation_delta_json", "[]", "observation_delta must decode to object"),
        ("verifier_json", "", "verifier JSON is empty"),
    ],
)
def test_transition_facts_reject_malformed_payloads(tmp_tehm, column, value,
                                                    message):
    """Causal extraction must not turn damaged canonical payloads into facts."""
    conn, transition_id = _captured(tmp_tehm)
    conn.execute(f"UPDATE tehm_transitions SET {column}=? WHERE transition_id=?",
                 (value, transition_id))
    conn.commit()
    with pytest.raises(ValueError, match=message):
        load_transition_facts(conn, transition_id)
    assert conn.execute("SELECT COUNT(*) FROM tehm_causal_nodes").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("action_json", {
            "domain": "flow.CONFIG_DELTA",
            "transformation_family": 7,
            "payload": {"config_edits": {"K": "V"}},
        }, "action.transformation_family must be a non-empty string"),
        ("action_json", {
            "domain": "flow.CONFIG_DELTA",
            "transformation_family": "DENSITY_RELIEF",
            "payload": [],
        }, "action.payload must be a non-empty object"),
        ("observation_delta_json", {
            "original_failure": "REMOVED",
            "created_regressions": "not-a-list",
        }, "observation_delta.created_regressions must be a list"),
        ("observation_delta_json", {
            "original_failure": 1,
        }, "observation_delta.original_failure must be a non-empty string"),
        ("verifier_json", {
            "verdict": "PASS",
            "oracle_type": "TARGET_TEST",
            "confidence_tier": "T",
            "evidence_refs": "not-a-list",
        }, "verifier.evidence_refs must be a list"),
    ],
)
def test_transition_facts_reject_semantically_malformed_payloads(
        tmp_tehm, column, value, message):
    """Object-shaped JSON must still satisfy the canonical typed contract."""
    conn, transition_id = _captured(tmp_tehm)
    conn.execute(
        f"UPDATE tehm_transitions SET {column}=? WHERE transition_id=?",
        (json.dumps(value), transition_id))
    conn.commit()
    with pytest.raises(ValueError, match=message):
        load_transition_facts(conn, transition_id)
    assert conn.execute("SELECT COUNT(*) FROM tehm_causal_nodes").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("action_domain", "flow.SDC_EDIT",
         "action_domain conflicts with action.domain"),
        ("outcome", "FAIL",
         "outcome conflicts with typed observation/verifier"),
        ("primary_effect_key", "effect_forged",
         "primary_effect_key conflicts with typed payload"),
    ],
)
def test_transition_facts_reject_tampered_derived_columns(
        tmp_tehm, column, value, message):
    """Causal extraction cannot trust duplicated/derived transition columns."""
    conn, transition_id = _captured(tmp_tehm)
    conn.execute(
        f"UPDATE tehm_transitions SET {column}=? WHERE transition_id=?",
        (value, transition_id))
    conn.commit()
    with pytest.raises(ValueError, match=message):
        load_transition_facts(conn, transition_id)
    assert conn.execute("SELECT COUNT(*) FROM tehm_causal_nodes").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("verifier_snapshot_json", "not-json", "source_verifier_snapshot JSON is malformed"),
        ("artifact_manifest_json", "[]", "target_artifact_manifest must decode to object"),
    ],
)
def test_transition_facts_reject_malformed_state_snapshots(tmp_tehm, column,
                                                            value, message):
    """A present state snapshot must be object JSON; NULL remains legacy-optional."""
    conn, transition_id = _captured(tmp_tehm)
    row = conn.execute(
        "SELECT source_state_id, target_state_id FROM tehm_transitions "
        "WHERE transition_id=?", (transition_id,)).fetchone()
    state_id = row["source_state_id"] if column == "verifier_snapshot_json" \
        else row["target_state_id"]
    conn.execute(f"UPDATE tehm_states SET {column}=? WHERE state_id=?",
                 (value, state_id))
    conn.commit()
    with pytest.raises(ValueError, match=message):
        load_transition_facts(conn, transition_id)
    assert conn.execute("SELECT COUNT(*) FROM tehm_causal_nodes").fetchone()[0] == 0


def test_causal_node_replay_rejects_tampered_payload(tmp_tehm):
    conn, transition_id = _captured(tmp_tehm)
    fragment = build_transition_causal_fragment(conn, transition_id)
    node_id = fragment.nodes[0].causal_node_id
    conn.execute(
        "UPDATE tehm_causal_nodes SET payload_json=? WHERE causal_node_id=?",
        ("{}", node_id))
    conn.commit()
    with pytest.raises(ValueError, match="causal node replay conflicts"):
        build_transition_causal_fragment(conn, transition_id)


def test_causal_edge_replay_rejects_tampered_witness(tmp_tehm):
    conn, transition_id = _captured(tmp_tehm)
    fragment = build_transition_causal_fragment(conn, transition_id)
    edge_id = fragment.edges[0].causal_edge_id
    conn.execute(
        "UPDATE tehm_causal_edges SET evidence_refs_json=? WHERE causal_edge_id=?",
        ("[\"forged-transition\"]", edge_id))
    conn.commit()
    with pytest.raises(ValueError, match="causal edge replay conflicts"):
        build_transition_causal_fragment(conn, transition_id)


def test_causal_path_replay_rejects_tampered_digest(tmp_tehm):
    conn, transition_id = _captured(tmp_tehm)
    fragment = build_transition_causal_fragment(conn, transition_id)
    path = consolidate_causal_path(conn, [fragment])
    conn.execute(
        "UPDATE tehm_causal_paths SET support_json=? WHERE path_id=?",
        ("{}", path.path_id))
    conn.commit()
    with pytest.raises(ValueError, match="causal path content digest mismatch"):
        consolidate_causal_path(conn, [fragment])


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
    node_types = [conn.execute(
        "SELECT node_type FROM tehm_causal_nodes WHERE causal_node_id=?",
        (node_id,)).fetchone()[0] for node_id in candidate.node_ids]
    assert node_types == [
        "STATE_CONDITION", "ACTION", "INTERMEDIATE_EFFECT", "ORACLE_OUTCOME"]
    edge_relations = [conn.execute(
        "SELECT relation_type FROM tehm_causal_edges WHERE causal_edge_id=?",
        (edge_id,)).fetchone()[0] for edge_id in candidate.edge_ids]
    assert edge_relations == ["INTERVENES_ON", "CHANGES", "SUPPORTS"]


def test_causal_path_rejects_reordered_topology_even_with_new_digest(tmp_tehm):
    conn, transition_id = _captured(tmp_tehm)
    fragment = build_transition_causal_fragment(conn, transition_id)
    candidate = consolidate_causal_path(conn, [fragment])
    row = conn.execute(
        "SELECT * FROM tehm_causal_paths WHERE path_id=?", (candidate.path_id,)
    ).fetchone()
    nodes = json.loads(row["ordered_nodes_json"])
    nodes[0], nodes[1] = nodes[1], nodes[0]
    support = json.loads(row["support_json"])
    digest = causal_path_digest(
        mechanism_family=row["mechanism_family"],
        compatibility_profile=row["compatibility_profile"],
        evidence_level=row["evidence_level"],
        source_transition_ids=json.loads(row["source_transitions_json"]),
        node_ids=nodes, edge_ids=json.loads(row["ordered_edges_json"]),
        support=support)
    conn.execute(
        "UPDATE tehm_causal_paths SET ordered_nodes_json=?, path_digest=? "
        "WHERE path_id=?", (json.dumps(nodes), digest, candidate.path_id))
    conn.commit()
    tampered = conn.execute(
        "SELECT * FROM tehm_causal_paths WHERE path_id=?", (candidate.path_id,)
    ).fetchone()
    with pytest.raises(ValueError, match="topology order"):
        validate_persisted_path_row(tampered, conn)


def test_causal_path_rejects_tampered_node_even_with_new_digest(tmp_tehm):
    """Recomputing a path digest cannot make a forged node authoritative."""
    conn, transition_id = _captured(tmp_tehm)
    fragment = build_transition_causal_fragment(conn, transition_id)
    candidate = consolidate_causal_path(conn, [fragment])
    node_id = candidate.node_ids[0]
    forged_payload = {"transition_id": transition_id, "forged": True}
    forged_json = json.dumps(
        forged_payload, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True)
    forged_digest = "sha1:" + hashlib.sha1(
        forged_json.encode()).hexdigest()[:16]
    conn.execute(
        "UPDATE tehm_causal_nodes SET payload_json=?, payload_digest=? "
        "WHERE causal_node_id=?",
        (forged_json, forged_digest, node_id),
    )
    row = conn.execute(
        "SELECT * FROM tehm_causal_paths WHERE path_id=?", (candidate.path_id,)
    ).fetchone()
    support = json.loads(row["support_json"])
    digest = causal_path_digest(
        mechanism_family=row["mechanism_family"],
        compatibility_profile=row["compatibility_profile"],
        evidence_level=row["evidence_level"],
        source_transition_ids=json.loads(row["source_transitions_json"]),
        node_ids=json.loads(row["ordered_nodes_json"]),
        edge_ids=json.loads(row["ordered_edges_json"]), support=support)
    conn.execute(
        "UPDATE tehm_causal_paths SET path_digest=? WHERE path_id=?",
        (digest, candidate.path_id))
    conn.commit()
    tampered = conn.execute(
        "SELECT * FROM tehm_causal_paths WHERE path_id=?", (candidate.path_id,)
    ).fetchone()
    with pytest.raises(ValueError, match="content-addressed ID mismatch"):
        validate_persisted_path_row(tampered, conn)
    authority = evaluate_causal_rule_evidence(
        conn, candidate.path_id, campaign_id="live",
        required_level="L0_ASSOCIATION", min_lineages=1)
    assert authority.eligible is False
    assert authority.reason.startswith("path_integrity_failed:")


def test_causal_path_rejects_tampered_edge_campaign_even_with_new_digest(tmp_tehm):
    """A forged edge campaign cannot be hidden by a recomputed path digest."""
    conn, transition_id = _captured(tmp_tehm)
    fragment = build_transition_causal_fragment(conn, transition_id)
    candidate = consolidate_causal_path(conn, [fragment])
    edge_id = candidate.edge_ids[0]
    conn.execute(
        "UPDATE tehm_causal_edges SET campaign_id='foreign-campaign' "
        "WHERE causal_edge_id=?", (edge_id,))
    row = conn.execute(
        "SELECT * FROM tehm_causal_paths WHERE path_id=?", (candidate.path_id,)
    ).fetchone()
    support = json.loads(row["support_json"])
    digest = causal_path_digest(
        mechanism_family=row["mechanism_family"],
        compatibility_profile=row["compatibility_profile"],
        evidence_level=row["evidence_level"],
        source_transition_ids=json.loads(row["source_transitions_json"]),
        node_ids=json.loads(row["ordered_nodes_json"]),
        edge_ids=json.loads(row["ordered_edges_json"]), support=support)
    conn.execute(
        "UPDATE tehm_causal_paths SET path_digest=? WHERE path_id=?",
        (digest, candidate.path_id))
    conn.commit()
    tampered = conn.execute(
        "SELECT * FROM tehm_causal_paths WHERE path_id=?", (candidate.path_id,)
    ).fetchone()
    with pytest.raises(ValueError, match="content-addressed ID mismatch"):
        validate_persisted_path_row(tampered, conn)


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


def test_intervention_pair_rejects_forged_lineage_override(tmp_tehm):
    conn, store, _ = tmp_tehm
    control = capture(conn, store, build_rtl_execution_record(
        PROJECT, oracle=None, store=store)).transition_id
    alternate = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    alternate.record_id = "rtl:req_ack_fsm:forged-lineage"
    alternate.action["payload"]["add_condition"] = "ready"
    treatment = capture(conn, store, alternate).transition_id
    with pytest.raises(ValueError, match="lineage_id does not match"):
        build_intervention_pair(
            control, treatment, conn=conn, lineage_id="forged-lineage")
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_intervention_pairs").fetchone()[0] == 0


def test_intervention_pair_replay_rejects_tampered_payload(tmp_tehm):
    conn, store, _ = tmp_tehm
    first = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    control_id = capture(conn, store, first).transition_id
    second = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    second.record_id = "rtl:req_ack_fsm:pair-replay"
    second.action["payload"]["add_condition"] = "ready"
    treatment_id = capture(conn, store, second).transition_id
    pair = build_intervention_pair(control_id, treatment_id, conn=conn)
    conn.execute(
        "UPDATE tehm_intervention_pairs SET validity_status=? WHERE pair_id=?",
        ("VALID_CONTROLLED_PAIR", pair.pair_id))
    conn.commit()
    with pytest.raises(ValueError, match="intervention pair replay conflicts"):
        build_intervention_pair(control_id, treatment_id, conn=conn)


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


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("not-json", "malformed_source_transitions"),
        ("[123]", "malformed_source_transitions"),
        ("[]", "source_transitions_missing"),
        ("[\"t0\", \"t0\"]", "duplicate_source_transitions"),
    ],
)
def test_shared_source_transition_witness_parser_is_strict(raw, reason):
    ids, error = parse_source_transition_ids(raw)
    assert ids is None
    assert error == reason


def test_learner_edge_coverage_rejects_non_string_source_ids(tmp_tehm):
    conn, transition_id = _captured(tmp_tehm)
    build_transition_causal_fragment(conn, transition_id)
    assert learner_edge_transition_coverage(
        conn, [123], campaign_id="live", required_level="L0_ASSOCIATION") == ()
    assert learner_edge_transition_coverage(
        conn, [transition_id, transition_id], campaign_id="live",
        required_level="L0_ASSOCIATION") == ()


def test_causal_authority_ignores_l2_edge_from_another_campaign(tmp_tehm):
    """A foreign campaign cannot satisfy controlled-intervention support."""
    conn, transition_id = _captured(tmp_tehm)
    fragment = build_transition_causal_fragment(conn, transition_id)
    path = consolidate_causal_path(conn, [fragment])
    # Construct a malformed/direct-SQL shadow state with an L2 edge, but bind
    # that edge to a different campaign.  The authority and replication
    # checks must both fail closed instead of treating global L2 evidence as
    # support for ``live``.
    _set_path_evidence_level(
        conn, path.path_id, "L2_CONTROLLED_INTERVENTION")
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


@pytest.mark.parametrize("source_json", ["not-json", "[123]"])
def test_causal_authority_and_replication_fail_closed_on_bad_source_json(
        tmp_tehm, source_json):
    conn, transition_id = _captured(tmp_tehm)
    fragment = build_transition_causal_fragment(conn, transition_id)
    path = consolidate_causal_path(conn, [fragment])
    conn.execute(
        "UPDATE tehm_causal_paths SET source_transitions_json=? WHERE path_id=?",
        (source_json, path.path_id))
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
    _set_path_evidence_level(
        conn, path.path_id, "L2_CONTROLLED_INTERVENTION")
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


def test_causal_authority_rejects_partial_unknown_edge_witness(tmp_tehm):
    """An unknown edge ref cannot be masked by one valid transition ref."""
    conn, transition_id = _captured(tmp_tehm)
    fragment = build_transition_causal_fragment(conn, transition_id)
    path = consolidate_causal_path(conn, [fragment])
    _set_path_evidence_level(
        conn, path.path_id, "L2_CONTROLLED_INTERVENTION")
    conn.execute(
        """INSERT INTO tehm_causal_edges
           (causal_edge_id, source_node_id, relation_type, target_node_id,
            evidence_level, support_json, confidence_json, evidence_refs_json,
            campaign_id, learner_eligible, created_at)
           VALUES ('partial-unknown-l2', 'source', 'SUPPORTS', 'target',
                   'L2_CONTROLLED_INTERVENTION', '{}', '{}', ?, 'live', 1,
                   '2026-01-01')""",
        (json.dumps([transition_id, "missing-transition"]),))
    conn.commit()

    authority = evaluate_causal_rule_evidence(
        conn, path.path_id, campaign_id="live",
        required_level="L2_CONTROLLED_INTERVENTION", min_lineages=1)
    assert authority.eligible is False
    assert "controlled_intervention_support_missing" in authority.reason


def test_causal_shadow_writes_preserve_outer_transaction(tmp_tehm):
    conn, transition_id = _captured(tmp_tehm)
    conn.execute(
        "INSERT INTO tehm_meta(key, value) VALUES (?, ?)",
        ("caller-sentinel", "pending"),
    )
    fragment = build_transition_causal_fragment(conn, transition_id)
    path = consolidate_causal_path(conn, [fragment])
    assert conn.in_transaction is True
    assert conn.execute(
        "SELECT 1 FROM tehm_causal_paths WHERE path_id=?", (path.path_id,)
    ).fetchone() is not None

    conn.rollback()
    assert conn.execute(
        "SELECT 1 FROM tehm_meta WHERE key='caller-sentinel'"
    ).fetchone() is None
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_causal_nodes"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT 1 FROM tehm_causal_paths WHERE path_id=?", (path.path_id,)
    ).fetchone() is None


def test_intervention_pair_preserves_outer_transaction(tmp_tehm):
    conn, store, _ = tmp_tehm
    control = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    control_id = capture(conn, store, control).transition_id
    treatment = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    treatment.record_id = "rtl:req_ack_fsm:transaction-safe"
    treatment.action["payload"]["add_condition"] = "ready"
    treatment_id = capture(conn, store, treatment).transition_id
    conn.execute(
        "INSERT INTO tehm_meta(key, value) VALUES (?, ?)",
        ("pair-sentinel", "pending"),
    )

    pair = build_intervention_pair(control_id, treatment_id, conn=conn)
    assert pair.validity_status.startswith("INVALID_")
    assert conn.in_transaction is True
    assert conn.execute(
        "SELECT 1 FROM tehm_intervention_pairs WHERE pair_id=?",
        (pair.pair_id,),
    ).fetchone() is not None

    conn.rollback()
    assert conn.execute(
        "SELECT 1 FROM tehm_meta WHERE key='pair-sentinel'"
    ).fetchone() is None
    assert conn.execute(
        "SELECT 1 FROM tehm_intervention_pairs WHERE pair_id=?",
        (pair.pair_id,),
    ).fetchone() is None
