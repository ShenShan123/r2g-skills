"""Fail-closed unit boundaries for the StateShift R3-7 eligibility audit."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def _module():
    path = Path(__file__).resolve().parents[1] / "scripts" / \
        "audit_p14_state_shift_r3_7_eligibility.py"
    spec = importlib.util.spec_from_file_location("r3_7_eligibility", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_self_digest_rejects_tampering():
    module = _module()
    payload = {"value": 1}
    payload["report_digest"] = module._digest(payload)
    assert module._self_digest(payload, "report_digest", "report") == \
        payload["report_digest"]
    payload["value"] = 2
    with pytest.raises(module.R37EligibilityError, match="mismatch"):
        module._self_digest(payload, "report_digest", "report")


def test_authority_boundary_rejects_promotion_or_docs():
    module = _module()
    safe = {
        "evaluation_only": True,
        "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
        "production_integration": "not_attempted",
        "promotion_attempted": False,
        "memory_docs_submitted": False,
    }
    assert module._closed(safe, "safe") is None
    for field, value in (
            ("promotion_attempted", True),
            ("memory_docs_submitted", True),
            ("production_runtime_imported", True)):
        with pytest.raises(module.R37EligibilityError, match="boundary"):
            module._closed({**safe, field: value}, field)


def test_required_remaining_gate_order_is_exact():
    module = _module()
    assert module.REQUIRED_REMAINING_GATES == (
        "C6_heldout_transfer",
        "C7_heldout_non_regression",
        "C8_delta_memory_ablation",
    )


def test_already_passing_exact_targets_and_firewall_only_heldout_are_blocked():
    module = _module()
    envelope_digest = "sha256:" + "a" * 64
    shift_payload = {
        "version": "state-shift-v0.1",
        "current_resolution_id": "resolution-a",
        "knowledge_object_id": "knowledge-a@2",
        "support_envelope_digest": envelope_digest,
        "structural_shift": 1.0,
        "mechanism_shift": 0.0,
        "flow_shift": 0.0,
        "constraint_shift": 1.0,
        "oracle_shift": 0.0,
        "history_shift": 0.0,
        "aggregate_shift": 0.333333,
        "shifted_dimensions": ["structural_shift", "constraint_shift"],
        "transferable": False,
        "reason": "STATE_SHIFT",
        "evidence_refs": ["sha256:evidence"],
    }
    shift_payload["replay_digest"] = module._digest(shift_payload)
    pair = SimpleNamespace(
        arm_receipts={"NO_MEMORY": SimpleNamespace(outcome="PASS")})
    cohort = SimpleNamespace(
        case_receipts={"case-a": pair, "case-b": pair},
        receipt_digest="sha256:cohort",
        toolchain_digest="sha256:toolchain",
        oracle_digest="sha256:oracle",
        platform_digest="sha256:platform",
        pdk_digest="sha256:pdk",
        candidate_budget=3,
    )
    expansion = SimpleNamespace(
        case_lineages={"case-a": "lineage-a", "case-b": "lineage-b"},
        cohort_receipt_digest="sha256:cohort",
    )
    assessment = module._derive_assessment(
        p14={
            "r3_6_complete": True,
            "r3_6_gates": {"C1": True, "C2": True},
            "remaining_gates": list(module.REQUIRED_REMAINING_GATES),
            "standard_capability_claim_promotable": False,
        },
        cohort=cohort,
        expansion=expansion,
        envelope=SimpleNamespace(envelope_digest=envelope_digest),
        heldout={"heldout": {
            "case_id": "heldout-a",
            "lineage_id": "heldout-lineage",
            "routing_decision": "NO_SKILL",
            "no_skill_reason": "STATE_SHIFT",
            "memory_action_executed": False,
            "repair_success_claimed": False,
            "state_shift": shift_payload,
        }},
        non_target_regression_free=True,
    )
    assert assessment["status"] == "NOT_ESTABLISHED"
    assert assessment["recommended_next_stage"] == "R3_8_MEMORY_INTERFERENCE"
    assert assessment["gates"][
        "F3_current_delta_has_target_gain_opportunity"] is False
    assert "current_target_M_t_already_passes" in assessment["blockers"]
    assert "source_disjoint_heldout_is_outside_support_envelope" in \
        assessment["blockers"]
