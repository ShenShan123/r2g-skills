"""P12-to-P13 replay report builder tests."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from contracts import MemoryRoutingDecision
from scripts.build_p13_shadow_trigger_report import (
    P13ShadowTriggerReportError,
    build_p13_shadow_trigger_report,
)
from tehm.evaluation.candidate_executor import (
    P12_ARMS,
    CandidateExecutionReceipt,
    PairedCandidateExecutionReceipt,
)
from tehm.evaluation.orfs_cohort import OrfsPairedCohortReceipt


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


def _routing(case_id: str) -> MemoryRoutingDecision:
    return MemoryRoutingDecision(
        decision="CONSIDER", resolved_state_id=f"state:{case_id}",
        selected_rule_ids=(f"rule:{case_id}",), selected_path_ids=(f"path:{case_id}",),
        selected_asset_ids=(), applicability={"status": "APPLICABLE"},
        causal_support={"status": "SUPPORTED"}, risk={}, abstain_reasons=(),
        no_memory_budget=2, memory_budget=1)


def _state_shift_routing(case_id: str) -> MemoryRoutingDecision:
    return MemoryRoutingDecision(
        decision="NO_SKILL", resolved_state_id=f"state:{case_id}",
        selected_rule_ids=(), selected_path_ids=(), selected_asset_ids=(),
        applicability={"status": "APPLICABLE", "state_shift_status": "SHIFTED"},
        causal_support={"status": "SUPPORTED"},
        risk={"state_shift_status": "SHIFTED"},
        abstain_reasons=("state_shift",), no_memory_budget=3, memory_budget=0,
        no_skill_reason="STATE_SHIFT",
        state_shift_receipt_id=f"state-shift:{case_id}")


def _cohort() -> OrfsPairedCohortReceipt:
    cases = {}
    for index, lineage in enumerate(("lineage-a", "lineage-b")):
        case_id = f"case-{index}"
        baseline = _execution(case_id, f"no-memory-{index}", "no_memory")
        memory = _execution(case_id, f"memory-{index}", "structured_memory")
        route = _routing(case_id)
        cases[case_id] = PairedCandidateExecutionReceipt(
            case_id=case_id,
            arm_receipts={arm: baseline if arm == "NO_MEMORY" else memory
                          for arm in P12_ARMS},
            candidate_budget=3, case_digest=f"sha256:case-{index}",
            toolchain_digest="sha256:tool", oracle_digest="sha256:oracle",
            lineage_id=lineage, routing_receipt_id=route.routing_receipt_id)
    return OrfsPairedCohortReceipt(
        campaign_id="campaign", case_receipts=cases,
        source_digests={f"case-{i}": f"sha256:source-{i}" for i in range(2)},
        source_content_digests={f"case-{i}": f"sha256:content-{i}" for i in range(2)},
        candidate_budget=3, toolchain_digest="sha256:tool",
        oracle_digest="sha256:oracle", platform_digest="sha256:platform",
        pdk_digest="sha256:pdk", campaign_manifest_digest="sha256:manifest")


def _write_inputs(tmp_path, *, include_routes: bool = True):
    cohort = _cohort()
    cohort_path = tmp_path / "cohort.json"
    cohort_path.write_text(json.dumps({**cohort.to_dict(),
                                       "receipt_digest": cohort.receipt_digest}))
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({
        "campaign_id": "campaign", "learner_eligible": True,
        "cases": [{"case_id": f"case-{i}", "dataset_split": "training",
                   "role": "training"} for i in range(2)]}))
    route_path = None
    if include_routes:
        route_path = tmp_path / "routes.json"
        route_path.write_text(json.dumps({
            case_id: {**_routing(case_id).to_dict(),
                      "decision_digest": _routing(case_id).decision_digest}
            for case_id in ("case-0", "case-1")}))
    return cohort_path, manifest_path, route_path


def _write_state_shift_inputs(tmp_path):
    base = _cohort()
    cases = {}
    routes = {}
    for index, (case_id, bundle) in enumerate(sorted(base.case_receipts.items())):
        route = _state_shift_routing(case_id)
        routes[case_id] = route
        cases[case_id] = replace(
            bundle, no_skill_reason="STATE_SHIFT",
            state_shift_receipt_id=route.state_shift_receipt_id,
            routing_receipt_id=route.routing_receipt_id)
    cohort = replace(base, campaign_id="state-shift-campaign", case_receipts=cases)
    cohort_path = tmp_path / "state-shift-cohort.json"
    cohort_path.write_text(json.dumps({**cohort.to_dict(),
                                       "receipt_digest": cohort.receipt_digest}))
    manifest_path = tmp_path / "state-shift-manifest.json"
    manifest_path.write_text(json.dumps({
        "campaign_id": cohort.campaign_id, "learner_eligible": True,
        "cases": [{"case_id": case_id, "dataset_split": "training",
                   "role": "training"} for case_id in sorted(cases)]}))
    route_path = tmp_path / "state-shift-routes.json"
    route_path.write_text(json.dumps({
        case_id: {**route.to_dict(), "decision_digest": route.decision_digest}
        for case_id, route in sorted(routes.items())}))
    evidence = tmp_path / "state-shift-event.json"
    evidence.write_text(json.dumps({"event": "independent-state-shift-review"}))
    reasons_path = tmp_path / "state-shift-reasons.json"
    reasons_path.write_text(json.dumps({
        "version": "p13-evolution-reason-receipt-v1",
        "campaign_id": cohort.campaign_id,
        "cohort_receipt_digest": cohort.receipt_digest,
        "label_source": "independent-state-shift-review-v1",
        "evidence_refs": [{
            "id": "state-shift-review", "path": evidence.name,
            "sha256": "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest(),
        }],
        "evolution_reasons": {case_id: ["STATE_SHIFT"] for case_id in cases},
        "evaluation_only": True, "canonical_memory_mutation": "none",
    }))
    return cohort_path, manifest_path, route_path, reasons_path


def test_report_fails_closed_without_route_and_reasons(tmp_path):
    cohort, manifest, _ = _write_inputs(tmp_path, include_routes=False)
    report = build_p13_shadow_trigger_report(
        cohort, manifest, output=tmp_path / "report.json")
    assert report["p13_eligible"] is False
    assert report["blocked_reasons"] == ["missing_routing_decision"]
    assert report["triggered_count"] == 0
    assert report["canonical_memory_mutation"] == "none"
    assert report["production_runtime_imported"] is False


def test_report_requires_explicit_evolution_signal_for_trigger(tmp_path):
    cohort, manifest, routes = _write_inputs(tmp_path)
    report_path = tmp_path / "report.json"
    retained = build_p13_shadow_trigger_report(
        cohort, manifest, routing_path=routes, output=report_path)
    assert retained["p13_eligible"] is False
    assert retained["blocked_reasons"] == ["no_evolution_signal"]
    evidence = tmp_path / "reason-evidence.json"
    evidence.write_text(json.dumps({"event": "explicit-evolution-signal"}))
    reasons = tmp_path / "reasons.json"
    reasons.write_text(json.dumps({
        "version": "p13-evolution-reason-receipt-v1",
        "campaign_id": "campaign",
        "cohort_receipt_digest": json.loads(cohort.read_text())["receipt_digest"],
        "label_source": "independent-event-review-v1",
        "evidence_refs": [{
            "id": "event-review", "path": evidence.name,
            "sha256": "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest(),
        }],
        "evolution_reasons": {"case-0": ["CAPABILITY_GAP"], "case-1": ["NOVELTY"]},
        "evaluation_only": True,
        "canonical_memory_mutation": "none",
    }))
    report = build_p13_shadow_trigger_report(
        cohort, manifest, routing_path=routes,
        evolution_reasons_path=reasons, output=report_path)
    assert report["p13_eligible"] is True
    assert report["triggered_count"] == 2
    assert all(item["triggered"] for item in report["triggers"])


def test_report_rejects_tampered_route_receipt(tmp_path):
    cohort, manifest, routes = _write_inputs(tmp_path)
    payload = json.loads(routes.read_text())
    payload["case-0"]["decision_digest"] = "sha256:tampered"
    routes.write_text(json.dumps(payload))
    with pytest.raises(P13ShadowTriggerReportError, match="routing decision for case-0"):
        build_p13_shadow_trigger_report(
            cohort, manifest, routing_path=routes, output=tmp_path / "report.json")


def test_report_rejects_unbound_manual_evolution_reason_map(tmp_path):
    cohort, manifest, routes = _write_inputs(tmp_path)
    reasons = tmp_path / "reasons.json"
    reasons.write_text(json.dumps({"case-0": ["CAPABILITY_GAP"],
                                   "case-1": ["NOVELTY"]}))
    with pytest.raises(P13ShadowTriggerReportError, match="missing fields"):
        build_p13_shadow_trigger_report(
            cohort, manifest, routing_path=routes,
            evolution_reasons_path=reasons, output=tmp_path / "report.json")


def test_report_admits_typed_state_shift_route(tmp_path):
    cohort, manifest, routes, reasons = _write_state_shift_inputs(tmp_path)
    report = build_p13_shadow_trigger_report(
        cohort, manifest, routing_path=routes,
        evolution_reasons_path=reasons, output=tmp_path / "report.json")
    assert report["p13_eligible"] is True
    assert report["triggered_count"] == 2
    assert all(item["routing_decision"] == "NO_SKILL"
               and item["no_skill_reason"] == "STATE_SHIFT"
               and item["triggered"]
               for item in report["triggers"])


def test_report_rejects_independent_evidence_input_reuse(tmp_path):
    cohort, manifest, routes = _write_inputs(tmp_path)
    reasons = tmp_path / "reasons.json"
    reasons.write_text(json.dumps({
        "version": "p13-evolution-reason-receipt-v1",
        "campaign_id": "campaign",
        "cohort_receipt_digest": json.loads(cohort.read_text())["receipt_digest"],
        "label_source": "independent-review-v1",
        "evidence_refs": [{
            "path": routes.name,
            "sha256": "sha256:" + hashlib.sha256(routes.read_bytes()).hexdigest(),
        }],
        "evolution_reasons": {"case-0": ["NOVELTY"], "case-1": ["NOVELTY"]},
        "evaluation_only": True, "canonical_memory_mutation": "none",
    }))
    with pytest.raises(P13ShadowTriggerReportError, match="independent"):
        build_p13_shadow_trigger_report(
            cohort, manifest, routing_path=routes,
            evolution_reasons_path=reasons, output=tmp_path / "report.json")


def test_report_rejects_output_input_collision(tmp_path):
    cohort, manifest, routes = _write_inputs(tmp_path)
    with pytest.raises(P13ShadowTriggerReportError, match="separate"):
        build_p13_shadow_trigger_report(
            cohort, manifest, routing_path=routes, output=routes)
