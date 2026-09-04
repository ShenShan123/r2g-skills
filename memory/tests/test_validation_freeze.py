"""Revision3 Validation Cohort V0 freeze/replay tests."""
from __future__ import annotations

import json

import pytest

from tehm.evaluation.candidate_executor import (
    CandidateExecutionReceipt, P12_ARMS, PairedCandidateExecutionReceipt,
)
from tehm.evaluation.rtl_cohort import RtlPairedCohortReceipt
from tehm.evaluation.validation_freeze import (
    ValidationCohortFreezeReceipt, ValidationFreezeError,
    freeze_validation_cohort, replay_validation_freeze,
)


def _execution(case_id: str, source: str, candidate_id: str) -> CandidateExecutionReceipt:
    return CandidateExecutionReceipt(
        case_id=case_id, candidate_id=candidate_id, source=source,
        action_digest="sha256:action", candidate_digest="sha256:candidate",
        compile_result="PASS", functional_result="PASS", signoff_result="PASS",
        outcome="PASS", created_regressions=(), obligations={},
        toolchain_digest="sha256:toolchain", oracle_digest="sha256:oracle",
        produced_transition_id=None, budget=3,
        metadata={"oracle_available": True, "oracle_metadata": {}})


def _reports(tmp_path):
    cases = {}
    for index, lineage in enumerate(("lineage-a", "lineage-b")):
        case_id = f"case-{index}"
        cases[case_id] = PairedCandidateExecutionReceipt(
            case_id=case_id,
            arm_receipts={
                "NO_MEMORY": _execution(case_id, "no_memory", "no_memory:" + case_id),
                "ALWAYS_MEMORY": _execution(case_id, "structured_memory", "memory:" + case_id),
                "APPLICABILITY_GATED": _execution(case_id, "structured_memory", "memory:" + case_id),
                "CAUSAL_NO_SKILL": _execution(case_id, "structured_memory", "memory:" + case_id),
            },
            candidate_budget=3, case_digest="sha256:case-" + str(index),
            toolchain_digest="sha256:toolchain", oracle_digest="sha256:oracle",
            paired=True, evaluation_only=True, lineage_id=lineage,
            routing_decision="CONSIDER")
    cohort = RtlPairedCohortReceipt(
        campaign_id="validation-v0", case_receipts=cases,
        source_digests={"case-0": "sha256:source-a", "case-1": "sha256:source-b"},
        candidate_budget=3, toolchain_digest="sha256:toolchain",
        oracle_digest="sha256:oracle", platform_digest="sha256:platform",
        pdk_digest="sha256:pdk", campaign_manifest_digest="sha256:manifest")
    cohort_payload = {**cohort.to_dict(), "receipt_digest": cohort.receipt_digest}
    cohort_report = {
        "campaign_id": cohort.campaign_id,
        "cohort_receipt": cohort_payload,
        "cohort_receipt_digest": cohort.receipt_digest,
        "outcome_counts": cohort.outcome_counts,
        "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
    }
    trigger_report = {
        "campaign_id": cohort.campaign_id,
        "cohort_receipt_digest": cohort.receipt_digest,
        "trigger_count": 2, "triggered_count": 0, "p13_eligible": False,
        "blocked_reasons": ["no_evolution_signal"],
        "canonical_memory_mutation": "none", "production_runtime_imported": False,
    }
    cohort_path = tmp_path / "cohort.json"
    trigger_path = tmp_path / "trigger.json"
    output_path = tmp_path / "freeze.json"
    cohort_path.write_text(json.dumps(cohort_report))
    trigger_path.write_text(json.dumps(trigger_report))
    return cohort_path, trigger_path, output_path


def test_validation_freeze_requires_all_pass_zero_trigger_and_replays(tmp_path):
    cohort_path, trigger_path, output_path = _reports(tmp_path)
    report = freeze_validation_cohort(cohort_path, trigger_path, output=output_path)
    assert report["lane"] == "VALIDATION"
    assert report["expected_action"] == "RETAIN"
    assert report["expected_evolution"] is False
    receipt = replay_validation_freeze(output_path)
    assert isinstance(receipt, ValidationCohortFreezeReceipt)
    assert receipt.triggered_count == 0


def test_validation_freeze_replay_rejects_wrapper_docs_boundary_tamper(tmp_path):
    cohort_path, trigger_path, output_path = _reports(tmp_path)
    freeze_validation_cohort(cohort_path, trigger_path, output=output_path)
    payload = json.loads(output_path.read_text())
    payload["memory_docs_submitted"] = True
    output_path.write_text(json.dumps(payload))
    with pytest.raises(ValidationFreezeError, match="memory_docs_submitted"):
        replay_validation_freeze(output_path)


def test_validation_freeze_replay_rejects_wrapper_digest_tamper(tmp_path):
    cohort_path, trigger_path, output_path = _reports(tmp_path)
    freeze_validation_cohort(cohort_path, trigger_path, output=output_path)
    payload = json.loads(output_path.read_text())
    payload["report_digest"] = "sha256:" + "0" * 64
    output_path.write_text(json.dumps(payload))
    with pytest.raises(ValidationFreezeError, match="report digest"):
        replay_validation_freeze(output_path)
