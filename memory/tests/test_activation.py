"""Eight-step activation (design doc 10, 11, 21.3, 26 Phase 8; test list 27.1).

Steps 2-8 run against an admissible rule; the R2G execution/oracle base is
injected as callables (real flow wiring is the shared base, Phase 1-full). The
three axes — Applicable, Executable, Verifiable — are stored SEPARATELY and a
successful activation produces a NEW verified transition.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from contracts import RepairContext
from tehm.activation.pipeline import ActivationError, activate
from tehm.canonical.capture import ExecutionRecord, capture
from tehm.crystallization.build_rules import crystallize_all

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _crystallize_one_rule(tmp_tehm, sample_record_dict) -> str:
    conn, store, _ = tmp_tehm
    base = json.loads(json.dumps(sample_record_dict))
    for i in range(3):
        rec = json.loads(json.dumps(base))
        rec["record_id"] = f"act_{i}"
        rec["lineage_id"] = f"lineage_{i}"
        rec["design_id"] = f"design_{i}"
        rec["episode"] = {"episode_id": f"ep_act_{i}", "lineage_id": f"lineage_{i}",
                          "step_index": 0, "terminal_status": "VERIFIED_REPAIR"}
        rec["action"]["payload"]["config_edits"] = {"PLACE_DENSITY_LB_ADDON": f"0.1{i + 4}"}
        rec["before"]["config"]["PLACE_DENSITY_LB_ADDON"] = "0.10"
        rec["after"]["config"]["PLACE_DENSITY_LB_ADDON"] = f"0.1{i + 4}"
        rec["observation_delta"]["first_divergence"]["before"] = 10 + i
        capture(conn, store, ExecutionRecord.from_dict(rec))
    rules = crystallize_all(conn)
    return rules[0]["rule_id"]


def fake_executor(action, context):
    knob = next(iter(action["payload"]["config_edits"]), None)
    value = action["payload"]["config_edits"].get(knob)
    return {
        "before_state": {
            "config": {"PLACE_DENSITY_LB_ADDON": "0.10"},
            "reports": {"drc": {"status": "violations", "total_violations": 7}},
            "failure_signature": {"check": "drc", "class": "antenna_diode"},
        },
        "after_state": {
            "config": {knob: value} if knob else {},
            "reports": {"drc": {"status": "clean", "total_violations": 0}},
        },
        "observation_delta": {
            "original_failure": "REMOVED",
            "first_divergence": {"before": 7, "after": 0},
            "failing_tests": {"before": 1, "after": 0},
            "created_regressions": [], "newly_observed_failures": [],
        },
        "tool_versions": {"iverilog": "1.0"},
    }


def fake_oracle(execution, obligations):
    status = execution["after_state"]["reports"]["drc"]["status"]
    return {
        "verdict": "PASS" if status == "clean" else "FAIL",
        "oracle_type": "REGRESSION", "confidence_tier": "R",
        "obligation_coverage": 1.0, "evidence_refs": ["drc"],
        "created_regressions": [], "newly_observed_failures": [],
    }


def _holes_of_rule(conn, rule_id):
    from tehm.retrieval.index import build_index
    rule = build_index(conn).get(rule_id)
    holes = set()
    for pattern in (rule["before_pattern"], rule["after_pattern"]):
        for v in pattern.values():
            if isinstance(v, str) and v.startswith("$H"):
                holes.add(v)
    return holes


def test_full_activation_loop(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    rule_id = _crystallize_one_rule(tmp_tehm, sample_record_dict)
    n_transitions_before = conn.execute(
        "SELECT COUNT(*) FROM tehm_transitions").fetchone()[0]

    holes = _holes_of_rule(conn, rule_id)
    binding = {h: ("PLACE_DENSITY_LB_ADDON" if "knob" in str(h) else "0.16")
               for h in holes}
    record = activate(
        conn, store, rule_id=rule_id, context=RepairContext(check="drc"),
        provided_binding=binding, executor=fake_executor, oracle=fake_oracle,
        authority_mode="evaluation")
    assert record.applicability_status == "APPLICABLE"
    assert record.binding_status == "BOUND"
    assert record.executability_status == "EXECUTABLE"
    assert record.verification_status == "PASS"
    assert record.outcome == "PASS"
    assert record.produced_transition_id.startswith("transition_")

    # a NEW verified transition was captured into the canonical store
    assert conn.execute("SELECT COUNT(*) FROM tehm_transitions").fetchone()[0] == \
        n_transitions_before + 1
    # the activation authority row persisted
    row = conn.execute(
        "SELECT activation_id, applicability_status, executability_status, "
        "verification_status, outcome, produced_transition_id "
        "FROM tehm_activations WHERE activation_id=?",
        (record.activation_id,)).fetchone()
    assert row is not None
    assert row["outcome"] == "PASS"
    # rule utility updated
    from tehm import db as tehm_db
    util = tehm_db.read_json(conn.execute(
        "SELECT utility_json FROM tehm_rules WHERE rule_id=?",
        (rule_id,)).fetchone()["utility_json"])
    assert util["activations"] == 1 and util["positive"] == 1
    feedback = conn.execute(
        "SELECT event_type, campaign_id, learner_eligible, payload_json "
        "FROM tehm_memory_events WHERE source_id=?",
        (record.activation_id,)).fetchone()
    assert feedback["event_type"] == "SUPPORT_INCREASED"
    assert feedback["campaign_id"] == "activation-evaluation"
    assert feedback["learner_eligible"] == 0
    from tehm.activation.update import update_rule_utility
    update_rule_utility(
        conn, rule_id, "PASS", activation_id=record.activation_id,
        campaign_id="activation-evaluation")
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_memory_events WHERE source_id=?",
        (record.activation_id,)).fetchone()[0] == 1


def test_activation_can_link_runtime_receipt_to_trial(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    rule_id = _crystallize_one_rule(tmp_tehm, sample_record_dict)
    record = activate(
        conn, store, rule_id=rule_id, context=RepairContext(check="drc"),
        provided_binding={"$H0": "PLACE_DENSITY_LB_ADDON", "$H1": "0.16"},
        executor=fake_executor, oracle=fake_oracle,
        authority_mode="evaluation", trial_uuid="trial-linked")
    stored = conn.execute(
        "SELECT trial_uuid FROM tehm_activations WHERE activation_id=?",
        (record.activation_id,)).fetchone()
    assert stored["trial_uuid"] == "trial-linked"


def test_three_axes_stored_separately(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    rule_id = _crystallize_one_rule(tmp_tehm, sample_record_dict)
    # unbound holes -> applicable but NOT executable; no oracle -> UNKNOWN verifiable
    record = activate(conn, store, rule_id=rule_id,
                      context=RepairContext(check="drc"), authority_mode="evaluation")
    assert record.applicability_status == "APPLICABLE"
    assert record.binding_status == "UNRESOLVED"
    assert record.executability_status == "NOT_EXECUTABLE"
    assert record.verification_status == "UNKNOWN"
    assert record.produced_transition_id is None
    assert record.outcome == "UNKNOWN"


def test_inapplicable_check_not_executed(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    rule_id = _crystallize_one_rule(tmp_tehm, sample_record_dict)
    record = activate(conn, store, rule_id=rule_id,
                      context=RepairContext(check="lvs"),
                      provided_binding={"$H0": "PLACE_DENSITY_LB_ADDON",
                                        "$H1": "0.16"},
                      executor=fake_executor, oracle=fake_oracle,
                      authority_mode="evaluation")
    assert record.applicability_status == "INAPPLICABLE"
    assert record.executability_status == "NOT_EXECUTABLE"
    assert record.produced_transition_id is None


def test_obligation_transfer_coverage(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    rule_id = _crystallize_one_rule(tmp_tehm, sample_record_dict)
    record = activate(conn, store, rule_id=rule_id,
                      context=RepairContext(check="drc"), authority_mode="evaluation")
    assert record.obligation_coverage is not None
    assert record.obligation_transfer["results"]


def test_failed_verification_captures_negative_transition(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    rule_id = _crystallize_one_rule(tmp_tehm, sample_record_dict)

    def failing_oracle(execution, obligations):
        return {"verdict": "FAIL", "oracle_type": "REGRESSION",
                "confidence_tier": "R", "obligation_coverage": 1.0,
                "evidence_refs": ["drc"], "created_regressions": ["lvs"],
                "newly_observed_failures": []}

    record = activate(conn, store, rule_id=rule_id,
                      context=RepairContext(check="drc"),
                      provided_binding={"$H0": "PLACE_DENSITY_LB_ADDON",
                                        "$H1": "0.16"},
                      executor=fake_executor, oracle=failing_oracle,
                      authority_mode="evaluation")
    assert record.verification_status == "FAIL"
    assert record.outcome == "REGRESSION"   # created regression dominates
    assert record.created_regressions == ["lvs"]
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_memory_events "
        "WHERE event_type='RULE_HARMFUL' AND source_id=?",
        (record.activation_id,)).fetchone()[0] == 1


def test_utility_feedback_is_atomic_on_event_failure(tmp_tehm, sample_record_dict):
    conn, _, _ = tmp_tehm
    rule_id = _crystallize_one_rule(tmp_tehm, sample_record_dict)
    from tehm import db as tehm_db
    from tehm.activation.update import update_rule_utility

    before = tehm_db.read_json(conn.execute(
        "SELECT utility_json FROM tehm_rules WHERE rule_id=?",
        (rule_id,)).fetchone()["utility_json"])
    with patch("tehm.evolution.events.append_memory_event",
               side_effect=ValueError("injected utility feedback failure")):
        with pytest.raises(ValueError, match="injected utility feedback failure"):
            update_rule_utility(
                conn, rule_id, "PASS", activation_id="activation-failure",
                campaign_id="activation-evaluation")
    after = tehm_db.read_json(conn.execute(
        "SELECT utility_json FROM tehm_rules WHERE rule_id=?",
        (rule_id,)).fetchone()["utility_json"])
    assert after == before
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_memory_events WHERE source_id=?",
        ("activation-failure",)).fetchone()[0] == 0


def test_dry_run_persists_nothing(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    rule_id = _crystallize_one_rule(tmp_tehm, sample_record_dict)
    record = activate(conn, store, rule_id=rule_id,
                      context=RepairContext(check="drc"),
                      provided_binding={"$H0": "PLACE_DENSITY_LB_ADDON",
                                        "$H1": "0.16"},
                      executor=fake_executor, oracle=fake_oracle, dry_run=True,
                      authority_mode="evaluation")
    assert conn.execute("SELECT COUNT(*) FROM tehm_activations").fetchone()[0] == 0
    assert record.produced_transition_id is None


def test_unknown_rule_rejected(tmp_tehm):
    conn, _, _ = tmp_tehm
    with pytest.raises(ActivationError):
        activate(conn, None, rule_id="rule_does_not_exist",
                 context=RepairContext(check="drc"), authority_mode="evaluation")
