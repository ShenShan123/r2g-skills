"""Online event hash-chain and revision lineage tests."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tehm.canonical.capture import capture
from tehm.causal import (build_transition_causal_fragment,
                          consolidate_causal_path)
from tehm.crystallization.build_rules import crystallize_all
from tehm.dataset import assign_transition
from tehm.evolution import (
    append_memory_event, detect_novelty, observe_transition,
    record_rule_revision, verify_event_chain,
)
from tehm.evolution.conflict import ConflictReceipt
from tehm.evolution.triggers import evaluate_consolidation_trigger
from tehm.rtl.rtl_evidence import build_rtl_execution_record


PROJECT = Path(__file__).resolve().parent / "fixtures" / "rtl_projects" / "req_ack_bug"


def _transition(tmp_tehm):
    conn, store, _ = tmp_tehm
    record = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    return conn, capture(conn, store, record).transition_id


def test_online_observation_is_idempotent_and_hash_chained(tmp_tehm):
    conn, transition_id = _transition(tmp_tehm)
    first = observe_transition(conn, transition_id)
    second = observe_transition(conn, transition_id)
    assert first.to_dict() == second.to_dict()
    assert first.novelty == "NOVEL_MECHANISM"
    assert first.consolidation_triggered is True
    assert first.trigger_reasons == ("NOVEL_MECHANISM",)
    assert first.consolidation_operation == "RETAIN"
    assert first.consolidation_decision.operation == "RETAIN"
    assert first.consolidation_preview is not None
    assert first.consolidation_preview.mode == "preview"
    assert first.consolidation_preview.full_rebuild_equivalent is True
    assert len(first.events) == 5
    assert verify_event_chain(conn, campaign_id="live")["ok"] is True
    assert conn.execute("SELECT COUNT(*) FROM tehm_memory_events").fetchone()[0] == 5
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_rule_revisions").fetchone()[0] == 0


def test_online_receipt_binds_mechanism_and_affected_rule_witness(
        tmp_tehm):
    """Fast-memory output names typed mechanism and source-owned rule impact."""
    conn, store, _ = tmp_tehm
    first = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    first_id = capture(conn, store, first).transition_id
    second = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    second.record_id = "rtl:req_ack:affected-rule"
    second.action["payload"]["add_condition"] = "ready"
    second_id = capture(conn, store, second).transition_id
    rules = crystallize_all(conn, campaign_id="live")
    assert rules
    expected_rule_ids = tuple(sorted({rule["rule_id"] for rule in rules}))

    receipt = observe_transition(conn, second_id, campaign_id="live")
    assert receipt.mechanism_signature["mechanism_family"] == (
        "HANDSHAKE_COMPLETION")
    assert receipt.affected_rule_ids == expected_rule_ids
    assert receipt.affected_path_ids == ()
    assert receipt.consolidation_decision.mechanism_signature == (
        receipt.mechanism_signature)
    assert receipt.consolidation_decision.affected_rule_ids == (
        expected_rule_ids)
    fragment_event = next(
        event for event in receipt.events
        if event.event_type == "CAUSAL_FRAGMENT_CREATED")
    payload = conn.execute(
        "SELECT payload_json FROM tehm_memory_events WHERE event_id=?",
        (fragment_event.event_id,)).fetchone()[0]
    decoded = json.loads(payload)
    assert decoded["mechanism_signature"] == receipt.mechanism_signature
    assert tuple(decoded["affected_rule_ids"]) == expected_rule_ids
    assert decoded["affected_path_ids"] == []
    trigger_event = next(
        event for event in receipt.events
        if event.event_type == "CONSOLIDATION_TRIGGERED")
    trigger_payload = json.loads(conn.execute(
        "SELECT payload_json FROM tehm_memory_events WHERE event_id=?",
        (trigger_event.event_id,)).fetchone()[0])
    assert trigger_payload["mechanism_signature"] == receipt.mechanism_signature
    assert tuple(trigger_payload["affected_rule_ids"]) == expected_rule_ids
    assert first_id != second_id


def test_online_receipt_replays_affected_path_witness(tmp_tehm):
    """A persisted shadow path is exposed only after full replay validation."""
    conn, store, _ = tmp_tehm
    first = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    first.record_id = "rtl:req_ack:path-first"
    first_id = capture(conn, store, first).transition_id
    second = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    second.record_id = "rtl:req_ack:path-second"
    second.action["payload"]["add_condition"] = "ready"
    second_id = capture(conn, store, second).transition_id
    path = consolidate_causal_path(
        conn,
        [build_transition_causal_fragment(
            conn, first_id, campaign_id="live"),
         build_transition_causal_fragment(
            conn, second_id, campaign_id="live")],
        campaign_id="live")

    receipt = observe_transition(conn, second_id, campaign_id="live")
    assert receipt.affected_path_ids == (path.path_id,)
    fragment_event = next(
        event for event in receipt.events
        if event.event_type == "CAUSAL_FRAGMENT_CREATED")
    payload = json.loads(conn.execute(
        "SELECT payload_json FROM tehm_memory_events WHERE event_id=?",
        (fragment_event.event_id,)).fetchone()[0])
    assert payload["affected_path_ids"] == [path.path_id]


def test_online_observation_rolls_back_fragment_and_events_on_late_failure(
        tmp_tehm):
    conn, transition_id = _transition(tmp_tehm)
    original = append_memory_event
    calls = {"count": 0}

    def fail_on_third(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 3:
            raise ValueError("injected online observation failure")
        return original(*args, **kwargs)

    with patch("tehm.evolution.manager.append_memory_event",
               side_effect=fail_on_third):
        with pytest.raises(ValueError, match="injected online observation failure"):
            observe_transition(conn, transition_id)
    assert calls["count"] == 3
    assert conn.execute("SELECT COUNT(*) FROM tehm_memory_events").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM tehm_causal_nodes").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM tehm_causal_edges").fetchone()[0] == 0


def test_online_observation_respects_outer_transaction(tmp_tehm):
    conn, transition_id = _transition(tmp_tehm)
    conn.execute(
        "UPDATE tehm_transitions SET outcome=outcome WHERE transition_id=?",
        (transition_id,))
    assert conn.in_transaction is True
    receipt = observe_transition(conn, transition_id)
    assert len(receipt.events) == 5
    assert conn.in_transaction is True
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM tehm_memory_events").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM tehm_causal_nodes").fetchone()[0] == 0


def test_event_chain_replays_tied_timestamps_by_predecessor(tmp_tehm):
    """Deterministic staging timestamps must not make a valid chain fail."""
    conn, _, _ = tmp_tehm
    stamp = "2026-08-22T00:00:00+00:00"
    first = append_memory_event(
        conn, event_type="TRANSITION_CAPTURED", source_type="audit",
        source_id="first", campaign_id="tied", created_at=stamp)
    second = append_memory_event(
        conn, event_type="CAUSAL_FRAGMENT_CREATED", source_type="audit",
        source_id="second", campaign_id="tied", created_at=stamp)
    assert second.previous_event_digest == first.event_digest
    assert verify_event_chain(conn, campaign_id="tied")["ok"] is True


def test_heldout_transition_cannot_enter_learner_online_lane(tmp_tehm):
    conn, transition_id = _transition(tmp_tehm)
    with pytest.raises(ValueError, match="explicit dataset membership"):
        observe_transition(conn, transition_id, campaign_id="unassigned")


def test_explicit_heldout_membership_is_audit_only_and_never_triggers_consolidation(
        tmp_tehm):
    conn, store, _ = tmp_tehm
    record = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    transition_id = capture(
        conn, store, record, dataset_campaign_id="split-campaign",
        dataset_split="heldout", dataset_learner_eligible=False).transition_id
    receipt = observe_transition(conn, transition_id, campaign_id="split-campaign")
    assert receipt.learner_eligible is False
    assert receipt.consolidation_triggered is False
    assert receipt.trigger_reasons == ("NOT_LEARNER_ELIGIBLE",)
    assert not any(event.event_type == "CONSOLIDATION_TRIGGERED"
                   for event in receipt.events)
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_memory_events "
        "WHERE event_type='CONSOLIDATION_TRIGGERED'").fetchone()[0] == 0


def test_nontraining_membership_cannot_be_marked_learner_eligible(
        tmp_tehm):
    conn, store, _ = tmp_tehm
    record = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    with pytest.raises(ValueError, match="only training"):
        capture(conn, store, record, dataset_campaign_id="calibration",
                dataset_split="calibration", dataset_learner_eligible=True)

    transition_id = capture(conn, store, record).transition_id
    with pytest.raises(ValueError, match="only training"):
        assign_transition(conn, transition_id=transition_id,
                          campaign_id="calibration", split="calibration",
                          learner_eligible=True)


def test_directly_corrupted_nontraining_membership_is_fail_closed(
        tmp_tehm):
    conn, store, _ = tmp_tehm
    record = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    transition_id = capture(conn, store, record).transition_id
    conn.execute(
        "UPDATE tehm_dataset_membership SET split='heldout', learner_eligible=1 "
        "WHERE transition_id=? AND campaign_id='live'", (transition_id,))
    conn.commit()

    assert crystallize_all(conn, campaign_id="live") == []
    receipt = observe_transition(conn, transition_id)
    assert receipt.learner_eligible is False
    assert receipt.consolidation_triggered is False
    assert receipt.trigger_reasons == ("NOT_LEARNER_ELIGIBLE",)


def test_trigger_rejects_forged_learner_eligibility(tmp_tehm):
    conn, store, _ = tmp_tehm
    record = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    transition_id = capture(
        conn, store, record, dataset_campaign_id="trigger-heldout",
        dataset_split="heldout", dataset_learner_eligible=False).transition_id
    conflict = ConflictReceipt(
        transition_id=transition_id, campaign_id="trigger-heldout",
        mechanism_family="unused", compatibility_profile=None)
    with pytest.raises(ValueError, match="learner_eligible conflicts"):
        evaluate_consolidation_trigger(
            conn, transition_id, campaign_id="trigger-heldout",
            learner_eligible=True, novelty="NOVEL_MECHANISM",
            conflict=conflict)


def test_dataset_membership_cannot_upgrade_audit_row_to_learner_support(
        tmp_tehm):
    conn, store, _ = tmp_tehm
    record = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    transition_id = capture(
        conn, store, record, dataset_campaign_id="membership-guard",
        dataset_split="heldout", dataset_learner_eligible=False).transition_id
    with pytest.raises(ValueError, match="cannot be upgraded"):
        assign_transition(
            conn, transition_id=transition_id, campaign_id="membership-guard",
            split="training", learner_eligible=True)
    row = conn.execute(
        "SELECT split, learner_eligible FROM tehm_dataset_membership "
        "WHERE transition_id=? AND campaign_id='membership-guard'",
        (transition_id,)).fetchone()
    assert (row["split"], row["learner_eligible"]) == ("heldout", 0)


def test_novelty_ignores_path_sourced_only_from_heldout_campaign(tmp_tehm):
    """Evaluation-only causal paths cannot suppress learner novelty."""
    conn, store, _ = tmp_tehm
    heldout = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    heldout.record_id = "rtl:req_ack_fsm:heldout-path"
    heldout.action["payload"]["add_condition"] = "ack && ready"
    heldout_id = capture(
        conn, store, heldout, dataset_campaign_id="heldout-campaign",
        dataset_split="heldout", dataset_learner_eligible=False).transition_id
    fragment = build_transition_causal_fragment(
        conn, heldout_id, campaign_id="heldout-campaign")

    # Simulate a legacy/direct-SQL shadow row.  The path is validly shaped but
    # its only source transition is held-out, so it must not count as learner
    # knowledge for the live campaign.
    conn.execute(
        """INSERT INTO tehm_causal_paths
           (path_id, mechanism_family, compatibility_profile,
            ordered_nodes_json, ordered_edges_json, evidence_level,
            support_json, source_transitions_json, path_digest, status,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'shadow', ?, ?)""",
        ("heldout_path", fragment.mechanism_family,
         fragment.compatibility_profile, json.dumps(list(fragment.node_ids)),
         json.dumps(list(fragment.edge_ids)), fragment.evidence_level, "{}",
         json.dumps([heldout_id]), "sha1:heldout-path", "2026-01-01",
         "2026-01-01"))
    conn.commit()

    training = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    training.record_id = "rtl:req_ack_fsm:training-path"
    training.action["payload"]["add_condition"] = "ack && grant"
    training_id = capture(conn, store, training).transition_id
    novelty = detect_novelty(conn, training_id, campaign_id="live")
    assert novelty["status"] == "NOVEL_MECHANISM"


def test_event_writer_cannot_mark_heldout_source_as_learner(tmp_tehm):
    conn, store, _ = tmp_tehm
    record = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    transition_id = capture(
        conn, store, record, dataset_campaign_id="heldout-campaign",
        dataset_split="heldout", dataset_learner_eligible=False).transition_id
    with pytest.raises(ValueError, match="not training learner evidence"):
        append_memory_event(
            conn, event_type="TRANSITION_CAPTURED", source_type="transition",
            source_id=transition_id, campaign_id="heldout-campaign",
            learner_eligible=True, payload={"direct": True})

    with pytest.raises(ValueError, match="no canonical transition witness"):
        append_memory_event(
            conn, event_type="TRANSITION_CAPTURED", source_type="unknown",
            source_id="model-output", campaign_id="live",
            learner_eligible=True, payload={"direct": True})


def test_rule_revision_requires_event_and_evidence(tmp_tehm):
    conn, transition_id = _transition(tmp_tehm)
    receipt = observe_transition(conn, transition_id)
    event_id = receipt.events[-1].event_id
    revision = record_rule_revision(
        conn, parent_rule_id="rule-old", child_rule_id="rule-new",
        operation="SPECIALIZE", trigger_event_id=event_id,
        evidence_refs=[transition_id], validation={"shadow": True})
    assert revision.operation == "SPECIALIZE"
    assert conn.execute("SELECT COUNT(*) FROM tehm_rule_revisions").fetchone()[0] == 1
    with pytest.raises(ValueError, match="invalid rule revision"):
        record_rule_revision(
            conn, parent_rule_id=None, child_rule_id="r2", operation="ADD",
            trigger_event_id=event_id, evidence_refs=[transition_id])


def test_rule_revision_rejects_non_consolidation_trigger(tmp_tehm):
    conn, transition_id = _transition(tmp_tehm)
    receipt = observe_transition(conn, transition_id)
    non_trigger = receipt.events[0].event_id  # TRANSITION_CAPTURED
    with pytest.raises(ValueError, match="consolidation or rule-proposal"):
        record_rule_revision(
            conn, parent_rule_id=None, child_rule_id="rule-new",
            operation="SPECIALIZE", trigger_event_id=non_trigger,
            evidence_refs=[transition_id])


def test_rule_revision_rejects_unknown_evidence_reference(tmp_tehm):
    conn, transition_id = _transition(tmp_tehm)
    receipt = observe_transition(conn, transition_id)
    event_id = receipt.events[-1].event_id
    with pytest.raises(ValueError, match="canonical transitions"):
        record_rule_revision(
            conn, parent_rule_id=None, child_rule_id="rule-new",
            operation="SPECIALIZE", trigger_event_id=event_id,
            evidence_refs=[transition_id, "missing-transition"])


def test_rule_revision_rejects_heldout_evidence_in_training_campaign(tmp_tehm):
    conn, store, _ = tmp_tehm
    training_record = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    training_id = capture(conn, store, training_record).transition_id
    heldout_record = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    heldout_record.record_id = "rtl:req_ack_fsm:heldout-revision"
    heldout_record.action["payload"]["add_condition"] = "ack && ack"
    heldout_id = capture(
        conn, store, heldout_record, dataset_campaign_id="live",
        dataset_split="heldout", dataset_learner_eligible=False).transition_id
    receipt = observe_transition(conn, training_id)
    event_id = receipt.events[-1].event_id
    with pytest.raises(ValueError, match="learner-eligible training"):
        record_rule_revision(
            conn, parent_rule_id=None, child_rule_id="rule-new",
            operation="SPECIALIZE", trigger_event_id=event_id,
            evidence_refs=[training_id, heldout_id])


def test_rule_revision_rejects_tampered_event_chain(tmp_tehm):
    conn, transition_id = _transition(tmp_tehm)
    receipt = observe_transition(conn, transition_id)
    event_id = receipt.events[-1].event_id
    conn.execute(
        "UPDATE tehm_memory_events SET event_digest='sha256:tampered' "
        "WHERE event_id=?", (event_id,))
    conn.commit()
    with pytest.raises(ValueError, match="event chain is invalid"):
        record_rule_revision(
            conn, parent_rule_id=None, child_rule_id="rule-new",
            operation="SPECIALIZE", trigger_event_id=event_id,
            evidence_refs=[transition_id])


def test_rule_revision_replay_rejects_conflicting_validation(tmp_tehm):
    conn, transition_id = _transition(tmp_tehm)
    observation = observe_transition(conn, transition_id)
    event_id = observation.events[-1].event_id
    first = record_rule_revision(
        conn, parent_rule_id="rule-old", child_rule_id="rule-new",
        operation="SPECIALIZE", trigger_event_id=event_id,
        evidence_refs=[transition_id], validation={"shadow": True})
    replay = record_rule_revision(
        conn, parent_rule_id="rule-old", child_rule_id="rule-new",
        operation="SPECIALIZE", trigger_event_id=event_id,
        evidence_refs=[transition_id], validation={"shadow": True})
    assert replay.revision_id == first.revision_id
    # The immutable identity cannot be used to overwrite the validation
    # witness; the conflicting replay must be rejected instead.
    with pytest.raises(ValueError, match="replay conflicts"):
        record_rule_revision(
            conn, parent_rule_id="rule-old", child_rule_id="rule-new",
            operation="SPECIALIZE", trigger_event_id=event_id,
            evidence_refs=[transition_id], validation={"shadow": "tampered"})


def test_event_replay_rejects_tampered_content_and_id(tmp_tehm):
    conn, transition_id = _transition(tmp_tehm)
    event = append_memory_event(
        conn, event_type="TRANSITION_CAPTURED", source_type="transition",
        source_id=transition_id, campaign_id="live", learner_eligible=True,
        payload={"tamper": "guard"})
    conn.execute(
        "UPDATE tehm_memory_events SET payload_json=? WHERE event_id=?",
        (json.dumps({"tamper": "changed"}), event.event_id))
    conn.commit()
    with pytest.raises(ValueError, match="replay conflicts"):
        append_memory_event(
            conn, event_type="TRANSITION_CAPTURED", source_type="transition",
            source_id=transition_id, campaign_id="live", learner_eligible=True,
            payload={"tamper": "guard"})

    conn.execute(
        "UPDATE tehm_memory_events SET payload_json=?, event_digest=? "
        "WHERE event_id=?",
        (json.dumps({"tamper": "guard"}), event.event_digest, event.event_id))
    conn.execute(
        "UPDATE tehm_memory_events SET event_id=? WHERE event_id=?",
        ("event_wrong_id", event.event_id))
    conn.commit()
    assert verify_event_chain(conn, campaign_id="live")["ok"] is False


def test_event_writer_preserves_outer_transaction(tmp_tehm):
    conn, transition_id = _transition(tmp_tehm)
    conn.execute(
        "INSERT INTO tehm_meta(key, value) VALUES (?, ?)",
        ("event-caller-sentinel", "pending"),
    )
    event = append_memory_event(
        conn, event_type="TRANSITION_CAPTURED", source_type="transition",
        source_id=transition_id, campaign_id="live", learner_eligible=True,
        payload={"transaction_safe": True})
    assert conn.in_transaction is True
    assert conn.execute(
        "SELECT 1 FROM tehm_memory_events WHERE event_id=?", (event.event_id,)
    ).fetchone() is not None

    conn.rollback()
    assert conn.execute(
        "SELECT 1 FROM tehm_meta WHERE key='event-caller-sentinel'"
    ).fetchone() is None
    assert conn.execute(
        "SELECT 1 FROM tehm_memory_events WHERE event_id=?", (event.event_id,)
    ).fetchone() is None


def test_rule_revision_writer_preserves_outer_transaction(tmp_tehm):
    conn, transition_id = _transition(tmp_tehm)
    observation = observe_transition(conn, transition_id)
    event_id = observation.events[-1].event_id
    conn.execute(
        "INSERT INTO tehm_meta(key, value) VALUES (?, ?)",
        ("revision-caller-sentinel", "pending"),
    )
    revision = record_rule_revision(
        conn, parent_rule_id="rule-old", child_rule_id="rule-new",
        operation="SPECIALIZE", trigger_event_id=event_id,
        evidence_refs=[transition_id], validation={"shadow": True})
    assert conn.in_transaction is True
    assert conn.execute(
        "SELECT 1 FROM tehm_rule_revisions WHERE revision_id=?",
        (revision.revision_id,),
    ).fetchone() is not None

    conn.rollback()
    assert conn.execute(
        "SELECT 1 FROM tehm_meta WHERE key='revision-caller-sentinel'"
    ).fetchone() is None
    assert conn.execute(
        "SELECT 1 FROM tehm_rule_revisions WHERE revision_id=?",
        (revision.revision_id,),
    ).fetchone() is None
