"""Typed P6 candidate-pool evidence replay tests."""
from __future__ import annotations

import hashlib
import json

import pytest

from contracts import MemoryCandidate, MemoryQuery, MemoryRoutingDecision
from tehm.evaluation.candidate_executor import (
    CandidateExecutionReceipt, PairedCandidateExecutionReceipt,
)
from tehm.evaluation.candidate_pool_evidence import (
    CandidatePoolEvidenceError, build_candidate_pool_evidence,
    replay_candidate_pool_evidence,
)
from tehm.evaluation.candidate_pool_aggregate import (
    CandidatePoolAggregateError, build_candidate_pool_aggregate,
    replay_candidate_pool_aggregate,
)
from tehm.evaluation.rtl_cohort import RtlPairedCohortReceipt
from tehm.ids import stable_dumps
from tehm.retrieval.candidate_pool import build_candidate_pool


def _digest(value):
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _execution(case_id, source, candidate_id, outcome="PASS"):
    return CandidateExecutionReceipt(
        case_id=case_id, candidate_id=candidate_id, source=source,
        action_digest="sha256:action-" + case_id,
        candidate_digest="sha256:candidate-" + candidate_id,
        compile_result="PASS", functional_result=outcome,
        signoff_result=outcome, outcome=outcome, created_regressions=(),
        obligations={}, toolchain_digest="sha256:toolchain",
        oracle_digest="sha256:oracle", produced_transition_id=None,
        budget=3,
        metadata={"oracle_available": True,
                  "oracle_metadata": {"oracle_complete": True},
                  "policy_fallback": False})


def _cohort_and_pools(tmp_path, *, suffix=""):
    cases = {}
    source_digests = {}
    pool_entries = []
    for index in range(2):
        case_id = f"pool-case-{index}{suffix}"
        memory_id = f"memory-candidate-{index}{suffix}"
        route = MemoryRoutingDecision(
            decision="CONSIDER", resolved_state_id=f"state-{index}",
            selected_rule_ids=(memory_id,), selected_path_ids=(f"path-{index}",),
            selected_asset_ids=(), applicability={"status": "APPLICABLE"},
            causal_support={"status": "SUPPORTED"}, risk={},
            abstain_reasons=(), no_memory_budget=1, memory_budget=1)
        cases[case_id] = PairedCandidateExecutionReceipt(
            case_id=case_id,
            arm_receipts={
                "NO_MEMORY": _execution(case_id, "no_memory", "no-memory:" + case_id),
                "ALWAYS_MEMORY": _execution(case_id, "structured_memory", memory_id),
                "APPLICABILITY_GATED": _execution(case_id, "structured_memory", memory_id),
                "CAUSAL_NO_SKILL": _execution(case_id, "structured_memory", memory_id),
            },
            candidate_budget=3, case_digest=f"sha256:case-{index}",
            toolchain_digest="sha256:toolchain", oracle_digest="sha256:oracle",
            paired=True, evaluation_only=True, lineage_id=f"lineage-{index}{suffix}",
            routing_receipt_id=route.routing_receipt_id,
            routing_decision=route.decision)
        source_digests[case_id] = f"sha256:source-{index}{suffix}"
        query = MemoryQuery(
            query_plan={"check": "route", "mechanism_family": "HANDSHAKE"},
            dominant_dimensions={"platform": "icarus"}, context_ref=case_id)
        no_memory = [
            MemoryCandidate(
                f"cold-a-{index}", "cold_start",
                {"action_family": "COLD_GUARD", "mechanism_family": "guard"}),
            MemoryCandidate(
                f"cold-b-{index}", "cold_start",
                {"action_family": "COLD_RESET", "mechanism_family": "reset"}),
        ]
        memory = [MemoryCandidate(
            memory_id, "tehm_rule",
            {"rule_id": memory_id, "action_family": "MEMORY_GUARD",
             "mechanism_family": "handshake", "applicability_status": "APPLICABLE"},
        )]
        pool = build_candidate_pool(
            query, no_memory, memory, arm="CAUSAL_NO_SKILL", routing=route,
            candidate_budget=3, case_id=case_id)
        pool_entries.append({
            "query": {**query.to_dict(),
                      "query_digest": _digest(query.to_dict())},
            "receipt": {**pool.receipt.to_dict(),
                        "receipt_digest": pool.receipt.receipt_digest},
            "candidates": [
                {"candidate_id": candidate.candidate_id,
                 "source": candidate.source, "payload": candidate.payload,
                 "score": candidate.score, "provenance": candidate.provenance}
                for candidate in pool.candidates
            ],
        })
    cohort = RtlPairedCohortReceipt(
        campaign_id="candidate-pool-campaign", case_receipts=cases,
        source_digests=source_digests, candidate_budget=3,
        toolchain_digest="sha256:toolchain", oracle_digest="sha256:oracle",
        platform_digest="sha256:platform", pdk_digest="sha256:pdk",
        campaign_manifest_digest="sha256:manifest")
    cohort_path = tmp_path / "cohort.json"
    cohort_path.write_text(json.dumps({**cohort.to_dict(),
                                       "receipt_digest": cohort.receipt_digest}))
    return cohort_path, pool_entries


def test_candidate_pool_evidence_replays_typed_composition(tmp_path):
    cohort_path, pools = _cohort_and_pools(tmp_path)
    output = tmp_path / "candidate-pool.json"
    report = build_candidate_pool_evidence(
        cohort_receipt=cohort_path, policy_arm="CAUSAL_NO_SKILL",
        pools=pools, output=output)
    metrics = replay_candidate_pool_evidence(report, base=tmp_path)
    assert metrics["source"] == "typed_candidate_pool"
    assert metrics["paired_cases"] == 2
    assert metrics["memory_interference_cases"] == 0
    assert metrics["memory_interference_rate"] == 0.0
    assert metrics["candidate_diversity"] == 1.0
    assert metrics["memory_admitted_cases"] == 2


def test_candidate_pool_evidence_rejects_metric_or_payload_tamper(tmp_path):
    cohort_path, pools = _cohort_and_pools(tmp_path)
    report = build_candidate_pool_evidence(
        cohort_receipt=cohort_path, policy_arm="CAUSAL_NO_SKILL", pools=pools)
    tampered = json.loads(json.dumps(report))
    tampered["metrics"]["candidate_diversity"] = 0.5
    with pytest.raises(CandidatePoolEvidenceError, match="aggregate metrics drifted"):
        replay_candidate_pool_evidence(tampered, base=tmp_path)

    tampered = json.loads(json.dumps(report))
    tampered["pools"][0]["candidates"][0]["payload"]["fix"] = "gold"
    with pytest.raises(CandidatePoolEvidenceError, match="gold-answer"):
        replay_candidate_pool_evidence(tampered, base=tmp_path)


def test_candidate_pool_aggregate_replays_disjoint_cohorts(tmp_path):
    reports = []
    for index in range(2):
        root = tmp_path / f"cohort-{index}"
        root.mkdir()
        cohort_path, pools = _cohort_and_pools(root, suffix=f"-{index}")
        payload = json.loads(cohort_path.read_text())
        payload["campaign_id"] = f"candidate-pool-campaign-{index}"
        # Re-sign the content-addressed cohort after changing its identity.
        # Retaining the old digest would correctly be rejected as tampering.
        payload.pop("receipt_digest", None)
        payload["receipt_digest"] = RtlPairedCohortReceipt.from_dict(payload).receipt_digest
        cohort_path.write_text(json.dumps(payload))
        report_path = root / "candidate-pool.json"
        build_candidate_pool_evidence(
            cohort_receipt=cohort_path, policy_arm="CAUSAL_NO_SKILL",
            pools=pools, output=report_path)
        reports.append(report_path)
    output = tmp_path / "candidate-pool-aggregate.json"
    aggregate = build_candidate_pool_aggregate(reports, output=output)
    replayed = replay_candidate_pool_aggregate(aggregate, base=tmp_path)
    assert replayed["source"] == "typed_candidate_pool_aggregate"
    assert replayed["cohort_count"] == 2
    assert replayed["paired_cases"] == 4
    assert replayed["candidate_diversity"] == 1.0
    projected = replay_candidate_pool_evidence(aggregate, base=tmp_path)
    assert projected["receipt_digest"] == aggregate["receipt_digest"]
    assert projected["paired_cases"] == 4

    tampered = json.loads(output.read_text())
    tampered["candidate_pool_receipts"][0]["case_count"] = 99
    with pytest.raises(CandidatePoolAggregateError, match="case count binding drifted"):
        replay_candidate_pool_aggregate(tampered, base=tmp_path)
