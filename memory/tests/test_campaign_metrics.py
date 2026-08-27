"""Exact denominator tests for section-13 campaign metrics."""
from __future__ import annotations

import json

from tehm.evaluation.campaign_metrics import evaluate_campaign
from tehm.ids import rule_id as mint_rule_id, stable_dumps
from tehm.lifecycle.rule_status import enter_shadow, set_status


def _rule(conn):
    now = "2026-08-01T00:00:00-07:00"
    before = {"target_check": "route", "knob": "CORE_UTILIZATION"}
    after = {"rewrite.value": "20", "execution.recheck": "route"}
    obligations = ["TARGET_FAILURE_REMOVED"]
    rule_id = mint_rule_id(
        domain="flow.signoff", before_pattern=before,
        after_pattern=after, hard_preconditions=[], obligations=obligations)
    conn.execute(
        """INSERT INTO tehm_rules (
          rule_id,domain,before_pattern_json,after_pattern_json,
          hard_preconditions_json,context_profile_json,obligations_json,
          validity_status,validity_profile_json,confidence_json,utility_json,
          risk_profile_json,predicate_schema_version,role_schema_version,
          crystallizer_version,merge_trace_digest,created_at,updated_at)
          VALUES (?,'flow.signoff',?,?, '[]','{}',?,'VALIDATED','{}','{}','{}',
                  '[]','p','v','c','m',?,?)""",
        (rule_id, stable_dumps(before), stable_dumps(after),
         stable_dumps(obligations), now, now))
    conn.execute("INSERT INTO tehm_rule_sources VALUES (?,'ep','{}','{}','train')",
                 (rule_id,))
    conn.commit()
    enter_shadow(conn, rule_id=rule_id, target_scope="route")
    set_status(conn, rule_id=rule_id, target_scope="route", status="promoted")
    return rule_id


def test_funnel_and_rollback_metrics_have_explicit_denominators(tmp_tehm):
    conn, _, _ = tmp_tehm
    rule_id = _rule(conn)
    rollback = stable_dumps({"verified": True})
    transfer = stable_dumps({"results": [{"obligation": "x", "status": "BOUND"}]})
    conn.execute(
        """INSERT INTO tehm_activations (
          activation_id,rule_id,target_state_id,retrieval_receipt_json,
          applicability_status,binding_status,binding_json,executability_status,
          obligation_transfer_json,obligation_coverage,verification_status,
          verifier_json,outcome,created_regressions_json,rollback_receipt_json,
          trial_uuid,created_at)
          VALUES ('a',?,'t','{}','APPLICABLE','BOUND','{}','EXECUTABLE',?,1.0,
                  'PASS','{}','PASS','[]',?,'trial','now')""",
        (rule_id, transfer, rollback))
    metrics = {"pairs": [{"subject_lineage": "heldout",
                           "arm_b": {"success": True}}],
               "registry_authority": {"verified": True}}
    conn.execute(
        "INSERT INTO tehm_trials VALUES (?,?,'route',NULL,NULL,'win',?,NULL,'trial',1,'now')",
        ("t", rule_id, json.dumps(metrics)))
    conn.commit()

    report = evaluate_campaign(conn, [{"case_id": "c", "design_id": "heldout",
                                       "platform": "sky130hd", "check": "route",
                                       "cfg": {"CORE_UTILIZATION": "73"}}])
    assert report["metrics"] == {
        "RC_ret": 1.0, "RC_exec": 1.0, "AY": 1.0, "BSR": 1.0,
        "IVR": 1.0, "RU": 1.0, "HAR": 0.0, "OC": 1.0, "TE": 1.0}
    assert report["rollback"]["activation_rate"] == 1.0
    assert report["rollback"]["registry_rate"] == 1.0


def test_component_discrimination_marks_same_rate_ablation_as_unidentified():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from run_controlled_m0_m8_v2 import component_discrimination

    rows = [{"arms": {
        "M8": {"repair_success": True, "action_executed": True,
                "obligation_coverage": 1.0},
        "M4": {"repair_success": True, "action_executed": True,
                "obligation_coverage": 1.0},
        "M5": {"repair_success": True, "action_executed": True,
                "obligation_coverage": 1.0},
        "M6": {"repair_success": True, "action_executed": True,
                "obligation_coverage": 1.0},
        "M7": {"repair_success": False, "action_executed": True,
                "obligation_coverage": 0.0},
    }}]
    result = component_discrimination(rows)
    assert result["role_view"]["identifiable_on_current_tasks"] is False
    assert result["predicate_view"]["interpretation"] == "not_identified_expand_task_set"
    assert result["obligation_transfer"]["identifiable_on_current_tasks"] is True


def test_component_discrimination_reports_harmful_activation_rates():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from run_controlled_m0_m8_v2 import component_discrimination

    rows = [{"arms": {
        "M8": {"repair_success": True, "action_executed": True,
                "obligation_coverage": 1.0},
        "M4": {"repair_success": False, "action_executed": True,
                "obligation_coverage": 0.0, "created_regressions": ["timing"]},
        "M5": {"repair_success": True, "action_executed": True,
                "obligation_coverage": 1.0},
        "M6": {"repair_success": True, "action_executed": True,
                "obligation_coverage": 1.0},
        "M7": {"repair_success": False, "action_executed": True,
                "obligation_coverage": 0.0},
    }}]
    result = component_discrimination(rows)
    assert result["validity_gate"]["full_harmful_activation_rate"] == 0.0
    assert result["validity_gate"]["ablated_harmful_activation_rate"] == 1.0
    assert result["validity_gate"]["harmful_rate_not_increased"] is True
    assert result["validity_gate"]["identifiable_on_current_tasks"] is True
