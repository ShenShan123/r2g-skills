"""P0 integrity regressions for the TEHM memory loop."""
from __future__ import annotations

import json

from tehm.activation.obligation_transfer import (
    obligations_transferable, transfer_obligations)
from tehm.activation.binding import bind_rule
from tehm.canonical.capture import ExecutionRecord, capture
from tehm.crystallization.build_rules import crystallize_all
from tehm.dataset import assign_lineage, assign_transition
from tehm.lifecycle.authority import apply_trial_verdict
from tehm.lifecycle.rule_status import enter_shadow, get_status
from tehm.evolution import raw_evidence_digest


def _capture_repeats(tmp_tehm, sample_record_dict, n=3):
    conn, store, _ = tmp_tehm
    for i in range(n):
        record = json.loads(json.dumps(sample_record_dict))
        record["record_id"] = f"p0_{i}"
        record["lineage_id"] = f"p0_lineage_{i}"
        record["design_id"] = f"p0_design_{i}"
        record["episode"] = {
            "episode_id": f"p0_episode_{i}",
            "lineage_id": f"p0_lineage_{i}",
            "step_index": 0,
            "terminal_status": "VERIFIED_REPAIR",
        }
        knob = "PLACE_DENSITY_LB_ADDON"
        record["action"]["payload"]["config_edits"] = {knob: f"0.1{i + 4}"}
        record["before"]["config"][knob] = "0.10"
        record["after"]["config"][knob] = f"0.1{i + 4}"
        record["observation_delta"]["first_divergence"]["before"] = 10 + i
        capture(conn, store, ExecutionRecord.from_dict(record))


def test_rule_sources_are_episode_owned_and_lineage_bound(tmp_tehm, sample_record_dict):
    conn, _, _ = tmp_tehm
    _capture_repeats(tmp_tehm, sample_record_dict)
    rule = crystallize_all(conn)[0]
    rows = conn.execute(
        "SELECT episode_id, lineage_id, source_substitution_json "
        "FROM tehm_rule_sources WHERE rule_id=? ORDER BY episode_id",
        (rule["rule_id"],)).fetchall()
    assert {row["lineage_id"] for row in rows} == {
        "p0_lineage_0", "p0_lineage_1", "p0_lineage_2"}
    for row in rows:
        owned = {r["transition_id"] for r in conn.execute(
            "SELECT transition_id FROM tehm_episode_steps WHERE episode_id=?",
            (row["episode_id"],))}
        witness = set(json.loads(row["source_substitution_json"]))
        assert witness
        assert witness <= owned


def test_recrystallize_preserves_utility_and_retires_stale_rule(
        tmp_tehm, sample_record_dict):
    conn, _, _ = tmp_tehm
    _capture_repeats(tmp_tehm, sample_record_dict)
    rule_id = crystallize_all(conn)[0]["rule_id"]
    enter_shadow(conn, rule_id=rule_id, target_scope="drc")
    conn.execute(
        "UPDATE tehm_rules SET utility_json=? WHERE rule_id=?",
        ('{"activations":7,"positive":6,"neutral":1,"harmful":0}', rule_id))
    conn.commit()
    crystallize_all(conn)
    assert json.loads(conn.execute(
        "SELECT utility_json FROM tehm_rules WHERE rule_id=?", (rule_id,)
    ).fetchone()[0])["activations"] == 7

    # A changed learner partition is represented by a new campaign, not by
    # rewriting the live membership.  A full rebuild with a stricter grouping
    # threshold produces no active rule and therefore exercises retirement
    # without mutating canonical membership.
    assert crystallize_all(conn, min_group_size=4) == []
    assert get_status(conn, rule_id=rule_id, target_scope="drc")["status"] == "retired"


def test_retirement_preserves_canonical_evidence_digest(tmp_tehm, sample_record_dict):
    conn, _, _ = tmp_tehm
    _capture_repeats(tmp_tehm, sample_record_dict)
    rule_id = crystallize_all(conn)[0]["rule_id"]
    enter_shadow(conn, rule_id=rule_id, target_scope="drc")
    # The learner partition remains immutable across the retirement rebuild.
    before = raw_evidence_digest(conn)
    assert crystallize_all(conn, min_group_size=4) == []
    after = raw_evidence_digest(conn)
    assert after == before
    assert conn.execute("SELECT COUNT(*) FROM tehm_transitions").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM tehm_episodes").fetchone()[0] == 3
    assert get_status(conn, rule_id=rule_id, target_scope="drc")["status"] == "retired"


def test_synthesizable_obligation_is_not_checked(tmp_tehm):
    context = type("Context", (), {"reports": {}, "cfg": {"LOCAL_TESTBENCH": "yes"}})()
    result = transfer_obligations(
        {"obligations": ["TARGET_FAILURE_REMOVED"]}, context)
    assert result["results"][0]["status"] == "SYNTHESIZABLE"
    assert result["obligation_coverage"] == 0.0
    assert obligations_transferable(result) is True


def test_unavailable_obligation_is_not_transferable():
    assert obligations_transferable({
        "results": [{"obligation": "PRESERVE_LVS", "status": "UNAVAILABLE"}],
    }) is False
    assert obligations_transferable({
        "results": [{"obligation": "PRESERVE_LVS", "status": "UNKNOWN"}],
    }) is False


def test_typed_target_test_obligation_binds_to_current_scope():
    context = type("Context", (), {
        "reports": {"route": {"status": "complete"}},
        "cfg": {"CORE_UTILIZATION": "50"},
        "check": "route",
    })()
    result = transfer_obligations(
        {"obligations": ["VERIFIER_TARGET_TEST"]}, context)
    assert result["results"][0]["oracle"] == "route"
    assert result["results"][0]["status"] == "BOUND"
    assert result["obligation_coverage"] == 1.0


def test_promotion_requires_explicit_complete_obligation_evidence(
        tmp_tehm, sample_record_dict):
    conn, _, _ = tmp_tehm
    _capture_repeats(tmp_tehm, sample_record_dict)
    rule_id = crystallize_all(conn)[0]["rule_id"]
    enter_shadow(conn, rule_id=rule_id, target_scope="drc")
    version = get_status(conn, rule_id=rule_id, target_scope="drc")["status_version"]
    assert apply_trial_verdict(
        conn, rule_id=rule_id, target_scope="drc", verdict="win",
        obligation_coverage=None, created_regressions=[], arms_differ=True,
        expected_status_version=version) is None


def test_crystallization_campaign_firewall_excludes_noneligible_rows(
        tmp_tehm, sample_record_dict):
    conn, _, _ = tmp_tehm
    _capture_repeats(tmp_tehm, sample_record_dict)
    lineage = "p0_lineage_2"
    assert assign_lineage(conn, lineage_id=lineage, campaign_id="heldout-v1",
                          split="heldout", learner_eligible=False) == 1
    # The live learner remains governed by its own membership; a separate
    # campaign has no eligible rows and therefore cannot crystallize evidence.
    assert crystallize_all(conn, campaign_id="heldout-v1") == []


def test_capture_boundary_can_record_ab_without_learner_membership(
        tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    record = json.loads(json.dumps(sample_record_dict))
    record["record_id"] = "ab-boundary"
    receipt = capture(
        conn, store, ExecutionRecord.from_dict(record),
        dataset_campaign_id="ab-v1", dataset_split="ab",
        dataset_learner_eligible=False, frozen_snapshot_digest="sha256:test")
    rows = conn.execute(
        "SELECT campaign_id, split, learner_eligible, frozen_snapshot_digest "
        "FROM tehm_dataset_membership WHERE transition_id=?",
        (receipt.transition_id,)).fetchall()
    assert [(r["campaign_id"], r["split"], r["learner_eligible"])
            for r in rows] == [("ab-v1", "ab", 0)]
    assert crystallize_all(conn, campaign_id="ab-v1") == []


def test_binding_proof_records_source_and_target_digest():
    from contracts import RepairContext

    rule = {
        "before_pattern": {"target_check": "drc", "knob": "$H0"},
        "after_pattern": {"rewrite.value": "$H1"},
    }
    binding = bind_rule(
        rule, RepairContext(check="drc", cfg={"CORE_UTILIZATION": "20"}),
        provided_binding={"$H0": "CORE_UTILIZATION", "$H1": "20"})
    assert binding.status == "BOUND"
    assert binding.proof["version"] == "binding-v0.1"
    assert binding.proof["resolution"]["$H0"]["source"] == "provided"
    assert len(binding.proof["target_context_digest"]) == 64


def test_binding_proof_can_resolve_an_unambiguous_graph_entity():
    from contracts import RepairContext

    rule = {
        "before_pattern": {"target_check": "rtl"},
        "after_pattern": {"rtl.target_state": "$H0"},
    }
    binding = bind_rule(
        rule,
        RepairContext(check="rtl", structural_graph={
            "nodes": [{"id": "fsm:0", "kind": "FSM_TRANSITION",
                       "target_state": "DONE"}],
            "edges": [],
        }),
    )
    assert binding.status == "BOUND"
    assert binding.substitutions == {"$H0": "DONE"}
    assert binding.proof["resolution"]["$H0"]["source"] == "structural_graph"


def test_production_activation_requires_promoted_lifecycle(tmp_tehm,
                                                           sample_record_dict):
    from contracts import RepairContext
    from tehm.activation.pipeline import ActivationError, activate

    conn, store, _ = tmp_tehm
    _capture_repeats(tmp_tehm, sample_record_dict)
    rule_id = crystallize_all(conn)[0]["rule_id"]  # no lifecycle authority yet
    try:
        activate(conn, store, rule_id=rule_id,
                 context=RepairContext(check="drc"))
    except ActivationError as exc:
        assert "not admissible" in str(exc)
    else:
        raise AssertionError("production activation accepted an unpromoted rule")
