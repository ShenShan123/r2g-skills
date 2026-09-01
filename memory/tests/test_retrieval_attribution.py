"""Counterfactual retrieval-failure attribution tests."""
from __future__ import annotations

import sqlite3

import pytest

from contracts import MemoryCandidate, MemoryQuery, MemoryRoutingDecision
from tehm.evaluation.candidate_executor import CandidateExecutionReceipt
from tehm.evolution.attribution import attribute_failure
from tehm.evolution.retrieval_attribution import (
    RetrievalAttributionError, RetrievalAttributionReceipt,
    attribute_retrieval_failure,
)
from tehm.retrieval.candidate_pool import build_candidate_pool


def _routing(case_id: str) -> MemoryRoutingDecision:
    return MemoryRoutingDecision(
        decision="CONSIDER", resolved_state_id=f"state:{case_id}",
        selected_rule_ids=("rule-selected",), selected_path_ids=("path-selected",),
        selected_asset_ids=(), applicability={"status": "APPLICABLE"},
        causal_support={"status": "SUPPORTED"}, risk={}, abstain_reasons=(),
        no_memory_budget=2, memory_budget=1)


def _pool(case_id: str):
    query = MemoryQuery(
        query_plan={"check": "route", "mechanism_family": "HANDSHAKE_COMPLETION"},
        context_ref=case_id)
    no_memory = [MemoryCandidate(
        f"cold:{case_id}", "cold_start",
        {"transformation_family": "GUARD_RESTORE"}, score=0.5)]
    memory = [MemoryCandidate(
        "rule-selected", "tehm_rule",
        {"rule_id": "rule-selected", "applicability_status": "APPLICABLE",
         "transformation_family": "GUARD_RESTORE",
         "mechanism_family": "HANDSHAKE_COMPLETION"}, score=0.9)]
    routing = _routing(case_id)
    return routing, build_candidate_pool(
        query, no_memory, memory, arm="CAUSAL_NO_SKILL", routing=routing,
        candidate_budget=3, case_id=case_id).receipt


def _execution(case_id: str, candidate_id: str, outcome: str, *, counterfactual=False):
    return CandidateExecutionReceipt(
        case_id=case_id, candidate_id=candidate_id, source="structured_memory",
        action_digest="sha256:action", candidate_digest="sha256:candidate",
        compile_result="PASS", functional_result="PASS", signoff_result="PASS",
        outcome=outcome, created_regressions=(), obligations={},
        toolchain_digest="sha256:tool", oracle_digest="sha256:oracle",
        produced_transition_id=None, budget=3,
        metadata={"counterfactual": counterfactual})


def test_retrieval_failure_requires_explicit_counterfactual_success():
    routing, pool = _pool("retrieval-case")
    selected = _execution("retrieval-case", "rule-selected", "FAIL")
    missed = _execution("retrieval-case", "rule-missed", "PASS", counterfactual=True)
    receipt = attribute_retrieval_failure(
        routing, pool, ("rule-missed", "rule-selected"),
        selected_execution=selected,
        counterfactual_executions={"rule-missed": missed})
    assert receipt.retrieval_failure is True
    assert receipt.oracle_success is True
    assert receipt.missed_candidate_ids == ("rule-missed",)
    assert receipt.reason == "eligible_candidate_missed_and_selected_failed"
    replay = RetrievalAttributionReceipt.from_dict({
        **receipt.to_dict(), "receipt_digest": receipt.receipt_digest})
    assert replay == receipt
    attributed = attribute_failure(sqlite3.connect(":memory:"),
                                   retrieval_receipt=receipt)
    assert attributed.failure_type == "RETRIEVAL_FAILURE"
    assert "candidate:rule-missed" in attributed.blamed_objects
    assert receipt.receipt_digest in attributed.evidence_refs


def test_unknown_or_unverified_counterfactual_is_not_retrieval_failure():
    routing, pool = _pool("retrieval-unknown")
    selected = _execution("retrieval-unknown", "rule-selected", "FAIL")
    unknown = _execution("retrieval-unknown", "rule-missed", "UNKNOWN",
                         counterfactual=True)
    receipt = attribute_retrieval_failure(
        routing, pool, ("rule-missed", "rule-selected"),
        selected_execution=selected,
        counterfactual_executions={"rule-missed": unknown})
    assert receipt.retrieval_failure is False
    assert receipt.oracle_success is False
    assert receipt.reason == "missed_candidate_not_verified"

    with pytest.raises(RetrievalAttributionError, match="counterfactual marker"):
        attribute_retrieval_failure(
            routing, pool, ("rule-missed", "rule-selected"),
            selected_execution=selected,
            counterfactual_executions={
                "rule-missed": _execution("retrieval-unknown", "rule-missed", "PASS")})


def test_no_skill_route_cannot_be_relabelled_as_retrieval_failure():
    routing, pool = _pool("retrieval-no-skill")
    no_skill = MemoryRoutingDecision(
        decision="NO_SKILL", resolved_state_id=routing.resolved_state_id,
        selected_rule_ids=(), selected_path_ids=(), selected_asset_ids=(),
        applicability={"status": "NOT_APPLICABLE"}, causal_support={"status": "INSUFFICIENT"},
        risk={}, abstain_reasons=(), no_memory_budget=3, memory_budget=0,
        no_skill_reason="NO_MATCH")
    # Build a pool receipt for the same no-skill route; no memory candidate is
    # admitted, so an omitted eligible candidate is not a retrieval failure.
    query = MemoryQuery(query_plan={"check": "route"}, context_ref="retrieval-no-skill")
    no_memory_candidate = MemoryCandidate(
        "cold:retrieval-no-skill", "cold_start", {"applicable": True}, score=0.5)
    no_skill_pool = build_candidate_pool(
        query, [no_memory_candidate], [MemoryCandidate(
            "rule-unused", "tehm_rule", {"applicable": True})],
        arm="CAUSAL_NO_SKILL", routing=no_skill, candidate_budget=3,
        case_id="retrieval-no-skill").receipt
    receipt = attribute_retrieval_failure(
        no_skill, no_skill_pool, ("rule-unused",))
    assert receipt.retrieval_failure is False
    assert receipt.reason == "no_memory_candidate_selected"

    with pytest.raises(RetrievalAttributionError, match="routing receipt mismatch"):
        attribute_retrieval_failure(
            _routing("different-case"), pool, ("rule-missed", "rule-selected"))
