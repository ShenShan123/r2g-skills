"""P6 candidate-pool A/B composition and interference metrics."""
from __future__ import annotations

import hashlib

import pytest

from contracts import MemoryCandidate, MemoryQuery, MemoryRoutingDecision
from tehm.ids import stable_dumps
from tehm.state import RiskReceipt
from tehm.retrieval.candidate_pool import (
    CandidatePoolError,
    CandidatePoolOutcome,
    CandidatePoolReceipt,
    build_candidate_pool,
    summarize_candidate_pool,
)


def _query(case_id: str = "case-1") -> MemoryQuery:
    return MemoryQuery(
        query_plan={"check": "route", "mechanism_family": "HANDSHAKE_COMPLETION"},
        context_ref=case_id,
    )


def _no_memory() -> list[MemoryCandidate]:
    return [
        MemoryCandidate(
            "cold-a", "cold_start",
            {"transformation_family": "GUARD_RESTORE"}, score=0.7,
        ),
        MemoryCandidate(
            "cold-b", "cold_start",
            {"transformation_family": "RESET_RESTORE"}, score=0.6,
        ),
        MemoryCandidate(
            "cold-c", "cold_start",
            {"transformation_family": "WIDTH_CORRECT"}, score=0.5,
        ),
    ]


def _memory() -> list[MemoryCandidate]:
    return [MemoryCandidate(
        "rule-memory", "tehm_rule",
        {
            "rule_id": "rule-memory",
            "transformation_family": "GUARD_RESTORE",
            "mechanism_family": "HANDSHAKE_COMPLETION",
            "applicability_status": "APPLICABLE",
        }, score=0.95,
    )]


def _routing(*, decision: str = "CONSIDER", case: str = "case-1"):
    memory_budget = 0 if decision in {"ABSTAIN", "INAPPLICABLE", "NO_SKILL"} else 1
    return MemoryRoutingDecision(
        decision=decision, resolved_state_id=f"state-{case}",
        selected_rule_ids=("rule-memory",), selected_path_ids=("path-1",),
        selected_asset_ids=(), applicability={"status": "APPLICABLE"},
        causal_support={"status": "SUPPORTED"}, risk={},
        abstain_reasons=(), no_memory_budget=2, memory_budget=memory_budget,
    )


def _risk_routing() -> tuple[MemoryRoutingDecision, RiskReceipt]:
    payload = {
        "version": "risk-receipt-v0.1",
        "current_resolution_id": "state-risk-pool",
        "expected_utility": -0.5,
        "evidence_refs": ["pool-risk-evidence"],
        "risk_model": "typed_expected_utility_v1",
        "reason": "RISK",
    }
    risk = RiskReceipt(
        current_resolution_id=payload["current_resolution_id"],
        expected_utility=payload["expected_utility"],
        evidence_refs=tuple(payload["evidence_refs"]),
        risk_model=payload["risk_model"], reason=payload["reason"],
        replay_digest="sha256:" + hashlib.sha256(
            stable_dumps(payload).encode()).hexdigest(),
    )
    routing = MemoryRoutingDecision(
        decision="NO_SKILL", resolved_state_id=risk.current_resolution_id,
        selected_rule_ids=(), selected_path_ids=(), selected_asset_ids=(),
        applicability={}, causal_support={}, risk={}, abstain_reasons=(),
        no_memory_budget=2, memory_budget=0, no_skill_reason="RISK",
        risk_receipt_id=risk.receipt_id, risk_receipt=risk.to_dict(),
    )
    return routing, risk


def test_no_memory_arm_and_always_memory_keep_unbiased_slot():
    no_memory = build_candidate_pool(
        _query(), _no_memory(), _memory(), arm="NO_MEMORY", candidate_budget=3,
    )
    assert no_memory.receipt.memory_admitted is False
    assert no_memory.receipt.memory_candidate_ids == ()
    assert len(no_memory.candidates) == 3

    always = build_candidate_pool(
        _query(), _no_memory(), _memory(), arm="ALWAYS_MEMORY", candidate_budget=3,
    )
    assert always.receipt.memory_candidate_ids == ("rule-memory",)
    assert len(always.receipt.no_memory_candidate_ids) == 2
    assert len(always.candidates) == 3


def test_applicability_and_causal_no_skill_gates_are_distinct():
    gated = build_candidate_pool(
        _query(), _no_memory(), _memory(), arm="APPLICABILITY_GATED",
        candidate_budget=3,
    )
    assert gated.receipt.memory_candidate_ids == ("rule-memory",)

    blocked = build_candidate_pool(
        _query(), _no_memory(), _memory(), arm="CAUSAL_NO_SKILL",
        routing=_routing(decision="ABSTAIN"), candidate_budget=3,
    )
    assert blocked.receipt.memory_candidate_ids == ()
    assert blocked.receipt.routing_decision == "ABSTAIN"

    admitted = build_candidate_pool(
        _query(), _no_memory(), _memory(), arm="CAUSAL_NO_SKILL",
        routing=_routing(), candidate_budget=3,
    )
    assert admitted.receipt.memory_candidate_ids == ("rule-memory",)
    assert admitted.receipt.candidate_diversity == pytest.approx(2 / 3)
    assert admitted.receipt.search_entropy == pytest.approx(0.918296)


def test_pool_receipt_preserves_reason_aware_no_skill_metadata():
    routing = _routing(decision="NO_SKILL")
    routing = MemoryRoutingDecision(
        decision=routing.decision, resolved_state_id=routing.resolved_state_id,
        selected_rule_ids=routing.selected_rule_ids,
        selected_path_ids=routing.selected_path_ids,
        selected_asset_ids=(), applicability=routing.applicability,
        causal_support=routing.causal_support, risk=routing.risk,
        abstain_reasons=routing.abstain_reasons,
        no_memory_budget=routing.no_memory_budget, memory_budget=0,
        no_skill_reason="STATE_SHIFT", state_shift_receipt_id="shift-receipt")
    pool = build_candidate_pool(
        _query(), _no_memory(), _memory(), arm="CAUSAL_NO_SKILL",
        routing=routing, candidate_budget=3)
    assert pool.receipt.no_skill_reason == "STATE_SHIFT"
    assert pool.receipt.state_shift_receipt_id == "shift-receipt"
    replay = CandidatePoolReceipt.from_dict({
        **pool.receipt.to_dict(), "receipt_digest": pool.receipt.receipt_digest})
    assert replay == pool.receipt


def test_pool_receipt_preserves_replayable_risk_witness():
    routing, risk = _risk_routing()
    pool = build_candidate_pool(
        _query(), _no_memory(), _memory(), arm="CAUSAL_NO_SKILL",
        routing=routing, candidate_budget=3)
    assert pool.receipt.risk_receipt == risk.to_dict()
    replay = CandidatePoolReceipt.from_dict({
        **pool.receipt.to_dict(), "receipt_digest": pool.receipt.receipt_digest})
    assert replay == pool.receipt
    tampered = {**pool.receipt.to_dict(),
                "risk_receipt": {**pool.receipt.risk_receipt,
                                 "expected_utility": 0.5}}
    with pytest.raises(CandidatePoolError, match="risk_receipt"):
        CandidatePoolReceipt.from_dict(tampered)


def test_budget_one_cannot_displace_no_memory():
    pool = build_candidate_pool(
        _query(), _no_memory(), _memory(), arm="ALWAYS_MEMORY", candidate_budget=1,
    )
    assert pool.receipt.candidate_ids == ("cold-a",)
    assert pool.receipt.memory_candidate_ids == ()
    assert "candidate_budget_reserved_for_no_memory" in pool.receipt.reasons


def test_pool_receipt_replay_and_alias_are_deterministic():
    pool = build_candidate_pool(
        _query(), _no_memory(), _memory(), arm="GATED_MEMORY", candidate_budget=3,
    )
    payload = {**pool.receipt.to_dict(), "receipt_digest": pool.receipt.receipt_digest}
    assert CandidatePoolReceipt.from_dict(payload) == pool.receipt


def test_candidate_pool_metrics_do_not_promote_unknown_outcomes():
    receipts = []
    outcomes = []
    for case_id in ("case-1", "case-2", "case-3"):
        pool = build_candidate_pool(
            _query(case_id), _no_memory(), _memory(), arm="ALWAYS_MEMORY",
            candidate_budget=3, case_id=case_id,
        )
        receipts.append(pool.receipt)
    outcomes.extend([
        CandidatePoolOutcome("case-1", "ALWAYS_MEMORY", "PASS", "FAIL"),
        CandidatePoolOutcome("case-2", "ALWAYS_MEMORY", "PASS", "ABSTAIN"),
        CandidatePoolOutcome("case-3", "ALWAYS_MEMORY", "UNKNOWN", "PASS"),
    ])
    metrics = summarize_candidate_pool(receipts, outcomes)
    assert metrics.cases == 3
    assert metrics.paired_cases == 2
    assert metrics.memory_interference_cases == 1
    assert metrics.memory_interference_rate == pytest.approx(0.5)
    assert metrics.memory_harm_cases == 1
    assert metrics.abstain_cases == 1
    assert metrics.abstention_utility == pytest.approx(1.0)
    assert metrics.memory_admitted_cases == 3
    assert metrics.candidate_budget_efficiency == pytest.approx(1.0)


def test_pool_rejects_duplicate_candidates_and_missing_no_memory():
    duplicate = MemoryCandidate("same", "cold_start", {}, score=0.1)
    with pytest.raises(CandidatePoolError, match="duplicate candidate_id"):
        build_candidate_pool(_query(), [duplicate, duplicate], [], arm="NO_MEMORY")
    with pytest.raises(CandidatePoolError, match="at least one no-memory"):
        build_candidate_pool(_query(), [], _memory(), arm="ALWAYS_MEMORY")


def test_memory_arm_rejects_cold_start_and_cross_arm_duplicate_ids():
    cold_memory = MemoryCandidate(
        "memory-cold", "cold_start", {"applicability_status": "APPLICABLE"},
    )
    with pytest.raises(CandidatePoolError, match="cannot use cold_start"):
        build_candidate_pool(_query(), _no_memory(), [cold_memory], arm="ALWAYS_MEMORY")
    overlapping = MemoryCandidate("cold-a", "tehm_rule", {"applicable": True})
    with pytest.raises(CandidatePoolError, match="overlap"):
        build_candidate_pool(_query(), _no_memory(), [overlapping], arm="ALWAYS_MEMORY")
