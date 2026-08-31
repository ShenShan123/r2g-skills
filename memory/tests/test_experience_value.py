"""P2 deterministic Experience Value selection and shadow persistence tests."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tehm.canonical.capture import capture
from tehm.causal import build_intervention_pair
from tehm.evolution import (
    ensure_experience_value_schema,
    evaluate_experience_value, evaluate_and_record_experience_value,
    experience_value_digest, load_experience_value, observe_transition,
)
from tehm.rtl.rtl_evidence import build_rtl_execution_record


PROJECT = Path(__file__).resolve().parent / "fixtures" / "rtl_projects" / "req_ack_bug"


def _record(store, *, record_id: str, verdict: str = "PASS", lineage_id: str | None = None):
    record = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    record.record_id = record_id
    record.lineage_id = lineage_id
    record.verification.update({
        "verdict": verdict,
        "oracle_type": "TARGET_TEST",
        "scope": "fixture:target",
        "confidence_tier": "T",
        "oracle_complete": True,
        "evidence_refs": [f"fixture-{record_id}"],
    })
    if verdict == "FAIL":
        record.observation_delta["created_regressions"] = ["target-regression"]
    return record


def _capture(tmp_tehm, *, record_id: str, verdict: str = "PASS",
             lineage_id: str | None = None, split: str = "training",
             learner: bool = True):
    conn, store, _ = tmp_tehm
    receipt = capture(
        conn, store, _record(store, record_id=record_id, verdict=verdict,
                              lineage_id=lineage_id),
        dataset_campaign_id="live", dataset_split=split,
        dataset_learner_eligible=learner)
    return conn, receipt.transition_id


def test_value_is_deterministic_and_keeps_legacy_trigger_separate(tmp_tehm):
    conn, transition_id = _capture(tmp_tehm, record_id="value-novel")
    before = conn.execute("SELECT COUNT(*) FROM tehm_memory_events").fetchone()[0]
    first = evaluate_experience_value(conn, transition_id, campaign_id="live")
    second = evaluate_experience_value(conn, transition_id, campaign_id="live")
    assert first.to_dict() == second.to_dict()
    assert first.novelty == 1.0
    assert first.capability_gap == 1.0
    assert first.update_layers == ("STATE", "CAUSAL", "RULE", "ASSET", "CAPABILITY")
    assert first.priority == "P2_MEDIUM"
    assert conn.execute("SELECT COUNT(*) FROM tehm_memory_events").fetchone()[0] == before

    observation = observe_transition(conn, transition_id)
    assert observation.consolidation_triggered is True
    assert observation.experience_value.to_dict() == first.to_dict()
    replay = observe_transition(conn, transition_id)
    assert replay.to_dict() == observation.to_dict()
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_experience_values").fetchone()[0] == 1


def test_value_detects_promoted_counterexample_and_prediction_surprise(tmp_tehm):
    conn, transition_id = _capture(tmp_tehm, record_id="value-counterexample",
                                    verdict="FAIL")
    conn.execute(
        """INSERT INTO tehm_rules (
           rule_id, domain, before_pattern_json, after_pattern_json,
           hard_preconditions_json, context_profile_json, obligations_json,
           validity_status, validity_profile_json, confidence_json,
           utility_json, risk_profile_json, predicate_schema_version,
           role_schema_version, crystallizer_version, merge_trace_digest,
           created_at, updated_at)
           VALUES ('promoted-rule', 'rtl', '{}', '{}', '[]', '{}', '[]',
                   'VALIDATED', '{}', '{}', '{}', '{}', 'p', 'r', 'c', 'm',
                   'now', 'now')""")
    conn.execute(
        """INSERT INTO tehm_rule_status
           (rule_id, target_scope, status, status_version, provenance_json,
            updated_at)
           VALUES ('promoted-rule', 'global', 'promoted', 1, '{}', 'now')""")
    conn.execute(
        """INSERT INTO tehm_activations (
           activation_id, rule_id, target_state_id, retrieval_receipt_json,
           applicability_status, binding_status, binding_json,
           executability_status, obligation_transfer_json, obligation_coverage,
           verification_status, verifier_json, outcome, created_regressions_json,
           produced_transition_id, rollback_receipt_json, trial_uuid, created_at)
           VALUES ('activation-counterexample', 'promoted-rule', 'target', ?,
                   'APPLICABLE', 'BOUND', '{}', 'EXECUTABLE', '{}', 1.0,
                   'FAIL', '{}', 'FAIL', '["target-regression"]', ?, NULL,
                   NULL, 'now')""",
        (json.dumps({"predicted_outcome": "PASS",
                     "no_memory_outcome": "PASS"}), transition_id))
    conn.commit()

    receipt = evaluate_experience_value(conn, transition_id, campaign_id="live")
    assert receipt.counterexample == 1.0
    assert receipt.memory_interference == 1.0
    assert receipt.surprise == 1.0
    assert receipt.priority == "P0_CRITICAL"
    assert "PROMOTED_MEMORY_COUNTEREXAMPLE" in receipt.reasons
    assert "MEMORY_INTERFERENCE" in receipt.reasons
    assert "PREDICTION_SURPRISE" in receipt.reasons


def test_value_uses_controlled_intervention_for_causal_discrimination(tmp_tehm):
    conn, first_id = _capture(tmp_tehm, record_id="value-control",
                              lineage_id="lineage-value")
    record = _record(tmp_tehm[1], record_id="value-treatment",
                     lineage_id="lineage-value")
    record.action["payload"]["add_condition"] = "ready"
    treatment_id = capture(conn, tmp_tehm[1], record).transition_id
    pair = build_intervention_pair(
        first_id, treatment_id, conn=conn, campaign_id="live")
    assert pair.validity_status == "VALID_CONTROLLED_PAIR"
    receipt = evaluate_experience_value(
        conn, treatment_id, campaign_id="live", predicted_outcome="FAIL")
    assert receipt.causal_discrimination == 1.0
    assert receipt.surprise == 1.0
    assert "CAUSAL_DISCRIMINATION" in receipt.reasons


def test_nontraining_value_is_audit_only(tmp_tehm):
    conn, transition_id = _capture(
        tmp_tehm, record_id="value-heldout", split="heldout", learner=False)
    receipt = evaluate_experience_value(conn, transition_id,
                                        campaign_id="live")
    assert receipt.update_layers == ("NONE",)
    assert receipt.reasons[0] == "NOT_LEARNER_ELIGIBLE"
    assert receipt.priority in {"P2_MEDIUM", "P3_LOW"}


def test_value_receipt_is_immutable_and_content_bound(tmp_tehm):
    conn, transition_id = _capture(tmp_tehm, record_id="value-persist")
    receipt = evaluate_and_record_experience_value(
        conn, transition_id, campaign_id="live", created_at="now")
    assert load_experience_value(conn, transition_id).to_dict() == receipt.to_dict()
    assert conn.execute(
        "SELECT receipt_digest FROM tehm_experience_values").fetchone()[0] == (
            experience_value_digest(receipt))
    conn.execute(
        "UPDATE tehm_experience_values SET receipt_json=?",
        (json.dumps({**receipt.to_dict(), "value_score": 0.0}),))
    conn.commit()
    with pytest.raises(ValueError, match="digest mismatch"):
        load_experience_value(conn, transition_id)


def test_value_table_is_lazy_on_existing_v4_store(tmp_path):
    conn = sqlite3.connect(tmp_path / "old-v4.sqlite")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """CREATE TABLE tehm_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
           INSERT INTO tehm_meta VALUES ('schema_version', 'tehm-v4');""")
    ensure_experience_value_schema(conn)
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        ("tehm_experience_values",)).fetchone() is not None
    assert conn.execute(
        "SELECT value FROM tehm_meta WHERE key='schema_version'"
    ).fetchone()[0] == "tehm-v4"
    conn.close()
