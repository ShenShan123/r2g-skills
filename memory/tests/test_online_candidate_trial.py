"""Phase B4: candidate trials are isolated and authority-gated."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from contracts import RepairContext
from tehm.canonical.capture import capture
from tehm.evolution import (
    CandidateTrialError,
    preview_affected_groups,
    run_shadow_candidate_trial,
)
from tehm.rtl.rtl_evidence import build_rtl_execution_record
from tehm.rtl.rtl_oracle import IcarusOracle


PROJECTS = Path(__file__).resolve().parent / "fixtures" / "rtl_projects"


def _preview(tmp_tehm):
    conn, store, _ = tmp_tehm
    oracle = IcarusOracle()
    if not oracle.available:
        pytest.skip("Icarus unavailable")
    ids = []
    for name in ("req_ack_bug", "req_ack_bug2"):
        ids.append(capture(
            conn, store, build_rtl_execution_record(
                PROJECTS / name, oracle=oracle, store=store)).transition_id)
    return conn, preview_affected_groups(conn, ids, campaign_id="live")


def test_candidate_trial_isolated_and_fail_closed(tmp_tehm):
    conn, preview = _preview(tmp_tehm)
    before = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("tehm_rules", "tehm_rule_status", "tehm_trials",
                      "tehm_rule_revisions")
    }

    def control(plan, context):
        return {"success": False}

    def candidate(plan, context):
        return {"success": True}

    receipt = run_shadow_candidate_trial(
        conn, preview, context=RepairContext(check="rtl"),
        arm_a_evaluator=control, arm_b_evaluator=candidate,
        repeats=2)
    assert receipt.candidate_rule_ids
    assert all(row["verdict"] == "win" for row in receipt.trial_results)
    assert receipt.gate_report["eligible"] is False
    assert receipt.promotion_eligible is False
    assert receipt.promotion_attempted is False
    assert receipt.production_promotion_eligible is False
    assert receipt.source_unchanged is True
    assert receipt.rollback_receipt["verified"] is True
    assert receipt.rollback_receipt["authority"] == "isolated_staging_discard"
    assert receipt.rollback_receipt["source_digest_before"] == receipt.source_digest_before
    assert receipt.rollback_receipt["source_digest_after"] == receipt.source_digest_after
    assert receipt.canonical_memory_mutation == "none"
    assert receipt.lifecycle_mutation == "isolated_staging_only"
    assert receipt.staging_counts["tehm_trials"] == len(receipt.trial_results)
    assert receipt.staging_counts["tehm_rule_status"] >= len(receipt.candidate_rule_ids)
    assert all(row["status"] == "candidate"
               for row in receipt.staging_rule_statuses)
    after = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in before
    }
    assert after == before


def test_candidate_trial_never_promotes_even_when_gates_are_true(tmp_tehm):
    conn, preview = _preview(tmp_tehm)

    receipt = run_shadow_candidate_trial(
        conn, preview, context=RepairContext(check="rtl"),
        arm_a_evaluator=lambda plan, context: {"success": False},
        arm_b_evaluator=lambda plan, context: {"success": True},
        repeats=2,
        promotion_gates={
            "rollback_verified": True,
            "registry_verified": True,
            "obligation_coverage": 1.0,
            "cross_lineage_te": 1.0,
            "harmful_rate": 0.0,
            "conformal_coverage": 1.0,
        })
    assert receipt.gate_report["eligible"] is True
    assert receipt.promotion_eligible is True
    assert receipt.promotion_attempted is False
    assert receipt.production_promotion_eligible is False
    assert conn.execute("SELECT COUNT(*) FROM tehm_rule_status").fetchone()[0] == 0


def test_isolated_rollback_receipt_fails_closed_on_source_change():
    from tehm.evolution import build_isolated_rollback_receipt

    receipt = build_isolated_rollback_receipt(
        source_digest_before="sha256:before",
        source_digest_after="sha256:after",
        staging_digest_before="sha256:stage-before",
        staging_digest_after="sha256:stage-after")
    assert receipt.verified is False
    assert receipt.reason == "source_digest_changed"


def test_candidate_trial_requires_equivalent_preview(tmp_tehm):
    conn, preview = _preview(tmp_tehm)
    # A persisted receipt is deliberately not accepted as a candidate-trial
    # input; the caller must retain the shadow preview witness.
    persisted = replace(preview, mode="persist")
    with pytest.raises(CandidateTrialError, match="mode=preview"):
        run_shadow_candidate_trial(
            conn,
            persisted,
            context=RepairContext(check="rtl"),
            arm_a_evaluator=lambda plan, context: {"success": False},
            arm_b_evaluator=lambda plan, context: {"success": True})
