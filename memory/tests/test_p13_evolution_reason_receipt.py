"""Independent P13 evolution-reason provenance binder tests."""
from __future__ import annotations

import hashlib
import json

import pytest

from scripts.build_p13_evolution_reason_receipt import (
    P13ReasonReceiptError, build_p13_evolution_reason_receipt,
)
from tehm.evaluation.candidate_executor import (
    P12_ARMS, CandidateExecutionReceipt, PairedCandidateExecutionReceipt,
)
from tehm.evaluation.orfs_cohort import OrfsPairedCohortReceipt
from tehm.evolution import P12ShadowTriggerError, P13EvolutionReasonReceipt


def _execution(case_id: str, candidate_id: str, source: str) -> CandidateExecutionReceipt:
    return CandidateExecutionReceipt(
        case_id=case_id, candidate_id=candidate_id, source=source,
        action_digest=f"sha256:action-{candidate_id}",
        candidate_digest=f"sha256:candidate-{candidate_id}",
        compile_result="PASS", functional_result="PASS", signoff_result="PASS",
        outcome="PASS", created_regressions=(), obligations={},
        toolchain_digest="sha256:tool", oracle_digest="sha256:oracle",
        produced_transition_id=None, budget=3,
        metadata={"oracle_available": True})


def _cohort() -> OrfsPairedCohortReceipt:
    cases = {}
    for index, lineage in enumerate(("lineage-a", "lineage-b")):
        case_id = f"case-{index}"
        baseline = _execution(case_id, f"no-memory-{index}", "no_memory")
        memory = _execution(case_id, f"memory-{index}", "structured_memory")
        cases[case_id] = PairedCandidateExecutionReceipt(
            case_id=case_id,
            arm_receipts={arm: baseline if arm == "NO_MEMORY" else memory
                          for arm in P12_ARMS},
            candidate_budget=3, case_digest=f"sha256:case-{index}",
            toolchain_digest="sha256:tool", oracle_digest="sha256:oracle",
            lineage_id=lineage)
    return OrfsPairedCohortReceipt(
        campaign_id="campaign", case_receipts=cases,
        source_digests={f"case-{i}": f"sha256:source-{i}" for i in range(2)},
        source_content_digests={f"case-{i}": f"sha256:content-{i}" for i in range(2)},
        candidate_budget=3, toolchain_digest="sha256:tool",
        oracle_digest="sha256:oracle", platform_digest="sha256:platform",
        pdk_digest="sha256:pdk", campaign_manifest_digest="sha256:manifest")


def _inputs(tmp_path):
    cohort = _cohort()
    cohort_path = tmp_path / "cohort.json"
    cohort_path.write_text(json.dumps({**cohort.to_dict(),
                                       "receipt_digest": cohort.receipt_digest}))
    evidence = tmp_path / "event.json"
    evidence.write_text(json.dumps({"event": "external-review"}))
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps({
        "version": "p13-evolution-reason-label-manifest-v1",
        "campaign_id": "campaign",
        "cohort_receipt_digest": cohort.receipt_digest,
        "label_source": "independent-event-review-v1",
        "evidence_refs": [{
            "path": evidence.name,
            "sha256": "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest(),
        }],
        "evolution_reasons": {"case-0": ["CAPABILITY_GAP"],
                               "case-1": ["NOVELTY"]},
    }))
    return cohort_path, labels


def test_reason_binder_emits_replayable_receipt(tmp_path):
    cohort, labels = _inputs(tmp_path)
    report = build_p13_evolution_reason_receipt(
        cohort, labels, output=tmp_path / "receipt.json")
    replay = P13EvolutionReasonReceipt.from_dict(report)
    assert replay.receipt_digest == report["receipt_digest"]
    assert report["canonical_memory_mutation"] == "none"
    assert report["production_runtime_imported"] is False


def test_reason_binder_rejects_cohort_digest_drift(tmp_path):
    cohort, labels = _inputs(tmp_path)
    payload = json.loads(labels.read_text())
    payload["cohort_receipt_digest"] = "sha256:tampered"
    labels.write_text(json.dumps(payload))
    with pytest.raises(P13ReasonReceiptError, match="does not match cohort"):
        build_p13_evolution_reason_receipt(
            cohort, labels, output=tmp_path / "receipt.json")


def test_reason_binder_rejects_outcome_inference_fields(tmp_path):
    cohort, labels = _inputs(tmp_path)
    payload = json.loads(labels.read_text())
    payload["memory_outcome"] = "FAIL"
    labels.write_text(json.dumps(payload))
    with pytest.raises(P13ReasonReceiptError, match="outcome or gold"):
        build_p13_evolution_reason_receipt(
            cohort, labels, output=tmp_path / "receipt.json")


def test_reason_binder_rejects_non_independent_evidence_path(tmp_path):
    cohort, labels = _inputs(tmp_path)
    payload = json.loads(labels.read_text())
    payload["evidence_refs"] = [{
        "path": cohort.name,
        "sha256": "sha256:" + hashlib.sha256(cohort.read_bytes()).hexdigest(),
    }]
    labels.write_text(json.dumps(payload))
    with pytest.raises(P13ReasonReceiptError, match="independent"):
        build_p13_evolution_reason_receipt(
            cohort, labels, output=tmp_path / "receipt.json")


def test_reason_binder_rejects_input_output_collision(tmp_path):
    cohort, labels = _inputs(tmp_path)
    with pytest.raises(P13ReasonReceiptError, match="separate"):
        build_p13_evolution_reason_receipt(cohort, labels, output=labels)


def test_reason_binder_replays_optional_case_evidence_refs(tmp_path):
    cohort, labels = _inputs(tmp_path)
    payload = json.loads(labels.read_text())
    refs = {}
    for case_id in ("case-0", "case-1"):
        evidence = tmp_path / f"{case_id}-event.json"
        evidence.write_text(json.dumps({"case_id": case_id,
                                        "event": "independent-review"}))
        refs[case_id] = [{
            "id": f"event-{case_id}",
            "path": evidence.name,
            "sha256": "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest(),
        }]
    payload["case_evidence_refs"] = refs
    labels.write_text(json.dumps(payload))
    report = build_p13_evolution_reason_receipt(
        cohort, labels, output=tmp_path / "receipt.json")
    replay = P13EvolutionReasonReceipt.from_dict(report)
    assert set(replay.case_evidence_refs) == {"case-0", "case-1"}
    assert report["case_evidence_refs"]["case-0"][0]["id"] == "event-case-0"

    tampered = dict(report)
    tampered["case_evidence_refs"] = {"case-0": refs["case-0"]}
    with pytest.raises(P12ShadowTriggerError, match="case_evidence_refs"):
        P13EvolutionReasonReceipt.from_dict(tampered)
