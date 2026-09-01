"""P9 empirical production gate remains fail-closed and evaluation-only."""
from __future__ import annotations

import math

import pytest

from contracts import MemoryQuery
from tehm.retrieval.memory_router import route_memory
from tehm.retrieval.production_gate import (
    ProductionGateError, ProductionGateReceipt, evaluate_production_gate,
)
from tehm import db as tehm_db


def _evidence(**overrides):
    value = {
        "baseline_harmful_activation_rate": 0.40,
        "memory_harmful_activation_rate": 0.10,
        "no_skill_precision": 0.90,
        "no_skill_recall": 0.85,
        "no_skill_cases": 20,
        "paired_cases": 20,
        "memory_interference_rate": 0.0,
        "candidate_diversity": 0.75,
        "authority_verified": True,
        "authority_receipt_id": "authority_abc",
        "authority_receipt_digest": "sha256:authority",
        "rollback_verified": True,
        "rollback_receipt_id": "rollback_abc",
        "rollback_receipt_digest": "sha256:rollback",
        "evidence_refs": [{
            "id": "p9-report",
            "sha256": "sha256:p9-report",
        }],
    }
    value.update(overrides)
    return value


def test_missing_p9_evidence_is_not_established():
    receipt = evaluate_production_gate({})
    assert receipt.eligible is False
    assert set(receipt.not_established) == {
        "efficacy", "no_skill_calibration", "candidate_pool",
        "authority", "rollback", "evidence",
    }
    assert receipt.failed == ()


def test_harm_reduction_branch_can_pass_but_does_not_enable_runtime():
    receipt = evaluate_production_gate(_evidence())
    assert receipt.eligible is True
    assert receipt.gate_status == {
        "efficacy": "PASS", "no_skill_calibration": "PASS",
        "candidate_pool": "PASS", "authority": "PASS",
        "rollback": "PASS", "evidence": "PASS",
    }
    assert receipt.metrics["efficacy_branch"] == "harmful_activation_decrease"
    payload = {**receipt.to_dict(), "receipt_digest": receipt.receipt_digest}
    assert ProductionGateReceipt.from_dict(payload) == receipt


def test_controlled_harm_repair_gain_is_alternative_efficacy_branch():
    evidence = _evidence(
        baseline_harmful_activation_rate=None,
        memory_harmful_activation_rate=None,
        baseline_repair_rate=0.30,
        memory_repair_rate=0.55,
        controlled_harm=True,
    )
    receipt = evaluate_production_gate(evidence)
    assert receipt.eligible is True
    assert receipt.metrics["efficacy_branch"] == "controlled_harm_repair_gain"


def test_pareto_gate_rejects_harm_reduction_with_repair_collapse():
    receipt = evaluate_production_gate(_evidence(
        baseline_repair_rate=0.80, memory_repair_rate=0.20))
    assert receipt.gate_status["efficacy"] == "FAIL"
    assert "neither_harm_reduction" in " ".join(receipt.reasons)


def test_paired_repair_regression_uses_mcnemar_guard():
    unsafe = evaluate_production_gate(_evidence(
        baseline_repair_rate=0.50, memory_repair_rate=0.50,
        repair_paired_cases=20, repair_regression_cases=8,
        repair_improvement_cases=0))
    assert unsafe.gate_status["efficacy"] == "FAIL"
    assert unsafe.metrics["repair_mcnemar"]["significant_regression"] is True

    safe = evaluate_production_gate(_evidence(
        baseline_repair_rate=0.50, memory_repair_rate=0.50,
        repair_paired_cases=20, repair_regression_cases=1,
        repair_improvement_cases=1))
    assert safe.gate_status["efficacy"] == "PASS"
    assert safe.metrics["repair_regression_guard"] == "paired_mcnemar_safe"


def test_paired_repair_counts_are_complete_and_bounded():
    malformed = evaluate_production_gate(_evidence(
        repair_paired_cases=10, repair_regression_cases=7))
    assert malformed.gate_status["efficacy"] == "FAIL"
    assert "paired repair evidence requires" in " ".join(malformed.reasons)

    bounded = evaluate_production_gate(_evidence(
        repair_paired_cases=10, repair_regression_cases=8,
        repair_improvement_cases=3))
    assert bounded.gate_status["efficacy"] == "FAIL"
    assert "paired repair counts are invalid" in " ".join(bounded.reasons)


def test_unpaired_or_unknown_metrics_fail_closed():
    receipt = evaluate_production_gate(_evidence(
        paired_cases=0, memory_interference_rate=None,
        candidate_diversity=None,
    ))
    assert receipt.eligible is False
    assert "candidate_pool" in receipt.missing
    assert receipt.gate_status["candidate_pool"] == "NOT_ESTABLISHED"

    receipt = evaluate_production_gate(_evidence(
        baseline_harmful_activation_rate=math.nan))
    assert receipt.gate_status["efficacy"] == "FAIL"


def test_authority_and_rollback_require_content_bound_receipts():
    receipt = evaluate_production_gate(_evidence(
        authority_verified=True, authority_receipt_id=None,
        authority_receipt_digest=None,
    ))
    assert receipt.gate_status["authority"] == "FAIL"
    assert "authority_receipt_id_required" in " ".join(receipt.reasons)

    receipt = evaluate_production_gate(_evidence(
        rollback_verified=True, rollback_receipt_id=None,
        rollback_receipt_digest=None,
    ))
    assert receipt.gate_status["rollback"] == "FAIL"


def test_receipt_digest_tamper_is_rejected():
    receipt = evaluate_production_gate(_evidence())
    payload = {**receipt.to_dict(), "receipt_digest": receipt.receipt_digest}
    payload["metrics"]["candidate_diversity"] = 0.99
    with pytest.raises(ProductionGateError, match="digest mismatch"):
        ProductionGateReceipt.from_dict(payload)

    digestless = {key: value for key, value in payload.items()
                  if key != "receipt_digest"}
    digestless["eligible"] = False
    with pytest.raises(ProductionGateError, match="eligible projection"):
        ProductionGateReceipt.from_dict(digestless)


def test_backend_gate_is_pure_and_router_stays_shadow_only(tmp_path):
    db_path = tmp_path / "tehm.sqlite"
    conn = tehm_db.connect(db_path)
    tehm_db.ensure_schema(conn)
    before = conn.execute("SELECT COUNT(*) FROM tehm_states").fetchone()[0]
    from tehm_backend import TehmMemoryBackend

    backend = TehmMemoryBackend(db_path=db_path, artifact_root=tmp_path / "artifacts")
    receipt = backend.evaluate_production_gate(_evidence())
    assert receipt.eligible is True
    assert conn.execute("SELECT COUNT(*) FROM tehm_states").fetchone()[0] == before
    with pytest.raises(Exception, match="shadow-only"):
        route_memory(conn, MemoryQuery(query_plan={}), mode="production")
    backend.close()
    conn.close()
