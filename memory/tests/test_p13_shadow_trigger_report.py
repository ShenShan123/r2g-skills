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
from scripts.build_p13_state_shift_admission_report import (
    P13StateShiftAdmissionReportError,
    build_p13_state_shift_admission_report,
)
from scripts.build_p13_state_shift_proposal_report import (
    P13StateShiftProposalReportError,
    build_p13_state_shift_proposal_report,
)
from scripts.build_p13_state_shift_plan_report import (
    P13StateShiftPlanReportError,
    build_p13_state_shift_plan_report,
)
from scripts.build_p13_state_shift_reason_bundle import (
    P13StateShiftReasonBundleError,
    build_p13_state_shift_reason_bundle,
)
from tehm.evaluation.candidate_executor import (
    P12_ARMS,
    CandidateExecutionReceipt,
    PairedCandidateExecutionReceipt,
)
from tehm.evaluation.orfs_cohort import OrfsPairedCohortReceipt
from tehm.ids import stable_dumps
from tehm.evolution import derive_state_shift_reason
from tehm.state.shift_receipts import StateShiftReceipt


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
    context_digest = "sha256:" + hashlib.sha256(
        stable_dumps({"case_id": case_id, "constraint_regime": "u50"}).encode()
    ).hexdigest()
    payload = {
        "version": "state-shift-v0.2",
        "current_resolution_id": f"state:{case_id}",
        "current_context_digest": context_digest,
        "knowledge_object_id": "knowledge:shared@1",
        "support_envelope_digest": "sha256:" + "e" * 64,
        "structural_shift": 1.0, "mechanism_shift": 0.0,
        "flow_shift": 0.0, "constraint_shift": 0.0,
        "oracle_shift": 0.0, "history_shift": 0.0,
        "aggregate_shift": 0.166667,
        "shifted_dimensions": ("structural_shift",),
        "transferable": False, "reason": "STATE_SHIFT",
        "evidence_refs": (f"event:{case_id}",),
    }
    receipt = StateShiftReceipt(
        **payload,
        replay_digest="sha256:" + hashlib.sha256(
            stable_dumps(payload).encode()).hexdigest())
    return MemoryRoutingDecision(
        decision="NO_SKILL", resolved_state_id=f"state:{case_id}",
        selected_rule_ids=(), selected_path_ids=(), selected_asset_ids=(),
        applicability={"status": "APPLICABLE", "state_shift_status": "SHIFTED"},
        causal_support={"status": "SUPPORTED"},
        risk={"state_shift_status": "SHIFTED"},
        abstain_reasons=("state_shift",), no_memory_budget=3, memory_budget=0,
        no_skill_reason="STATE_SHIFT",
        state_shift_receipt_id=receipt.receipt_id,
        state_shift_receipt=receipt.to_dict())


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
        baseline = bundle.arm_receipts["NO_MEMORY"]
        cases[case_id] = replace(
            bundle,
            arm_receipts={
                arm: baseline if arm in {"NO_MEMORY", "CAUSAL_NO_SKILL"}
                else bundle.arm_receipts[arm]
                for arm in P12_ARMS
            },
            no_skill_reason="STATE_SHIFT",
            state_shift_receipt_id=route.state_shift_receipt_id,
            routing_receipt_id=route.routing_receipt_id,
            routing_decision="NO_SKILL")
    cohort = replace(base, campaign_id="state-shift-campaign", case_receipts=cases)
    cohort_path = tmp_path / "state-shift-cohort.json"
    cohort_path.write_text(json.dumps({**cohort.to_dict(),
                                       "receipt_digest": cohort.receipt_digest}))
    manifest_path = tmp_path / "state-shift-manifest.json"
    manifest_path.write_text(json.dumps({
        "version": "p13-learner-partition-v1",
        "campaign_id": cohort.campaign_id, "learner_eligible": True,
        "cases": [{"case_id": case_id, "dataset_split": "training",
                   "role": "training", "learner_eligible": True}
                  for case_id in sorted(cases)],
        "evaluation_only": True, "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
        "memory_docs_submitted": False,
    }))
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


def _write_state_shift_audit(tmp_path, cohort_path, route_path):
    cohort = OrfsPairedCohortReceipt.from_dict(json.loads(cohort_path.read_text()))
    routes = {
        case_id: MemoryRoutingDecision.from_dict(payload)
        for case_id, payload in json.loads(route_path.read_text()).items()
    }
    cases = []
    for index, (case_id, paired) in enumerate(sorted(cohort.case_receipts.items())):
        route = routes[case_id]
        shift = StateShiftReceipt.from_dict(route.state_shift_receipt)
        preregistered = derive_state_shift_reason(
            shift, campaign_id="preregistered-campaign", case_id=case_id,
            routing=route, lineage_id=paired.lineage_id)
        cases.append({
            "case_id": case_id,
            "lineage_id": paired.lineage_id,
            "flow_config_observation": {
                "values": {"DESIGN_NAME": f"design-{index}"},
            },
            "admitted_to_state_shift_bucket": True,
            "state_shift": shift.to_dict(),
            "routing": route.to_dict(),
            "evolution_reason": {
                **preregistered.to_dict(),
                "receipt_id": preregistered.receipt_id,
                "receipt_digest": preregistered.receipt_digest,
            },
        })
    audit = tmp_path / "state-shift-preregistration-audit.json"
    payload = {
        "version": "test-state-shift-audit-v1",
        "execution_started": False,
        "promotion_attempted": False,
        "production_database_writes": False,
        "parent_replay": {
            "knowledge": {"knowledge_id": "knowledge:shared", "version": 1},
            "pairs": [{
                "lineage_id": f"flow-v2:design-{index}",
                "treatment_transition_id": f"transition-treatment-{index}",
                "validity_status": "VALID_CONTROLLED_PAIR",
                "evidence_level": "L2_CONTROLLED_INTERVENTION",
                "outcome_delta": {
                    "treatment": {"outcome": "PASS", "verdict": "PASS"},
                },
            } for index in range(2)],
        },
        "cases": cases,
    }
    payload["audit_digest"] = "sha256:" + hashlib.sha256(
        stable_dumps(payload).encode()).hexdigest()
    audit.write_text(json.dumps(payload))
    return audit


def test_report_fails_closed_without_route_and_reasons(tmp_path):
    cohort, manifest, _ = _write_inputs(tmp_path, include_routes=False)
    report = build_p13_shadow_trigger_report(
        cohort, manifest, output=tmp_path / "report.json")
    assert report["p13_eligible"] is False
    assert report["blocked_reasons"] == ["missing_routing_decision"]
    assert report["triggered_count"] == 0
    assert report["canonical_memory_mutation"] == "none"
    assert report["production_runtime_imported"] is False
    assert report["memory_docs_submitted"] is False


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


def test_typed_state_shift_bundle_rebinds_preregistered_detector_receipts(tmp_path):
    cohort, manifest, routes, _ = _write_state_shift_inputs(tmp_path)
    audit = _write_state_shift_audit(tmp_path, cohort, routes)
    bundle_path = tmp_path / "typed-state-shift-reasons.json"
    bundle = build_p13_state_shift_reason_bundle(
        cohort, audit, output=bundle_path)
    assert bundle["campaign_id"] == "state-shift-campaign"
    assert bundle["reason_receipt"]["label_source"] == (
        "typed-detector:state_shift_receipt_adapter")
    report = build_p13_shadow_trigger_report(
        cohort, manifest, routing_path=routes,
        typed_reason_bundle_path=bundle_path,
        output=tmp_path / "typed-report.json")
    assert report["p13_eligible"] is True
    assert report["triggered_count"] == 2
    assert report["evolution_reasons"]["bundle_digest"] == bundle["bundle_digest"]
    assert all(item["triggered"] for item in report["triggers"])


def test_state_shift_admission_report_replays_all_reason_specific_gates(tmp_path):
    cohort, manifest, routes, _ = _write_state_shift_inputs(tmp_path)
    audit = _write_state_shift_audit(tmp_path, cohort, routes)
    bundle_path = tmp_path / "typed-state-shift-reasons.json"
    build_p13_state_shift_reason_bundle(cohort, audit, output=bundle_path)
    trigger_path = tmp_path / "typed-trigger-report.json"
    build_p13_shadow_trigger_report(
        cohort, manifest, routing_path=routes,
        typed_reason_bundle_path=bundle_path, output=trigger_path)
    report = build_p13_state_shift_admission_report(
        cohort, audit, bundle_path, routes, manifest, trigger_path,
        output=tmp_path / "admission-report.json")
    assert report["p13_admission_eligible"] is True
    assert report["admitted_count"] == report["case_count"] == 2
    assert all(item["admitted"] for item in report["admissions"].values())
    assert report["mutation_plan_present"] is False
    assert report["shadow_update_attempted"] is False
    assert report["canonical_memory_mutation"] == "none"
    assert report["production_runtime_imported"] is False


def test_state_shift_admission_report_rejects_partition_drift(tmp_path):
    cohort, manifest, routes, _ = _write_state_shift_inputs(tmp_path)
    audit = _write_state_shift_audit(tmp_path, cohort, routes)
    bundle_path = tmp_path / "typed-state-shift-reasons.json"
    build_p13_state_shift_reason_bundle(cohort, audit, output=bundle_path)
    trigger_path = tmp_path / "typed-trigger-report.json"
    build_p13_shadow_trigger_report(
        cohort, manifest, routing_path=routes,
        typed_reason_bundle_path=bundle_path, output=trigger_path)
    partition = json.loads(manifest.read_text())
    partition["tampered_after_trigger"] = True
    manifest.write_text(json.dumps(partition))
    with pytest.raises(P13StateShiftAdmissionReportError,
                       match="input file binding mismatch"):
        build_p13_state_shift_admission_report(
            cohort, audit, bundle_path, routes, manifest, trigger_path,
            output=tmp_path / "admission-report.json")


def test_state_shift_proposal_replays_admitted_orfs_evidence(tmp_path):
    cohort, manifest, routes, _ = _write_state_shift_inputs(tmp_path)
    audit = _write_state_shift_audit(tmp_path, cohort, routes)
    bundle_path = tmp_path / "typed-state-shift-reasons.json"
    build_p13_state_shift_reason_bundle(cohort, audit, output=bundle_path)
    trigger_path = tmp_path / "typed-trigger-report.json"
    build_p13_shadow_trigger_report(
        cohort, manifest, routing_path=routes,
        typed_reason_bundle_path=bundle_path, output=trigger_path)
    admission_path = tmp_path / "admission-report.json"
    build_p13_state_shift_admission_report(
        cohort, audit, bundle_path, routes, manifest, trigger_path,
        output=admission_path)

    report = build_p13_state_shift_proposal_report(
        admission_path, output=tmp_path / "proposal-report.json")
    assert report["proposal_eligible"] is True
    assert report["proposal"]["operation"] == "REVISE"
    assert report["proposal"]["evolution_reason"] == (
        "SUPPORT_ENVELOPE_EXPANSION")
    assert len(report["proposal"]["state_context_digests"]) == 2
    assert report["localized_update_plan_present"] is False
    assert report["anti_forgetting_present"] is False
    assert report["shadow_update_attempted"] is False
    assert report["canonical_memory_mutation"] == "none"
    assert report["production_runtime_imported"] is False


def test_state_shift_proposal_rejects_admission_input_drift(tmp_path):
    cohort, manifest, routes, _ = _write_state_shift_inputs(tmp_path)
    audit = _write_state_shift_audit(tmp_path, cohort, routes)
    bundle_path = tmp_path / "typed-state-shift-reasons.json"
    build_p13_state_shift_reason_bundle(cohort, audit, output=bundle_path)
    trigger_path = tmp_path / "typed-trigger-report.json"
    build_p13_shadow_trigger_report(
        cohort, manifest, routing_path=routes,
        typed_reason_bundle_path=bundle_path, output=trigger_path)
    admission_path = tmp_path / "admission-report.json"
    build_p13_state_shift_admission_report(
        cohort, audit, bundle_path, routes, manifest, trigger_path,
        output=admission_path)

    audit_payload = json.loads(audit.read_text())
    audit_payload["parent_replay"]["pairs"][0][
        "treatment_transition_id"] = "transition-drifted"
    audit_payload.pop("audit_digest")
    audit_payload["audit_digest"] = "sha256:" + hashlib.sha256(
        stable_dumps(audit_payload).encode()).hexdigest()
    audit.write_text(json.dumps(audit_payload))
    with pytest.raises(P13StateShiftProposalReportError,
                       match="admission input preregistration_audit digest mismatch"):
        build_p13_state_shift_proposal_report(
            admission_path, output=tmp_path / "proposal-report.json")


def _build_state_shift_proposal_chain(tmp_path):
    cohort, manifest, routes, _ = _write_state_shift_inputs(tmp_path)
    audit = _write_state_shift_audit(tmp_path, cohort, routes)
    bundle_path = tmp_path / "typed-state-shift-reasons.json"
    build_p13_state_shift_reason_bundle(cohort, audit, output=bundle_path)
    trigger_path = tmp_path / "typed-trigger-report.json"
    build_p13_shadow_trigger_report(
        cohort, manifest, routing_path=routes,
        typed_reason_bundle_path=bundle_path, output=trigger_path)
    admission_path = tmp_path / "admission-report.json"
    build_p13_state_shift_admission_report(
        cohort, audit, bundle_path, routes, manifest, trigger_path,
        output=admission_path)
    proposal_path = tmp_path / "proposal-report.json"
    build_p13_state_shift_proposal_report(
        admission_path, output=proposal_path)
    return proposal_path, trigger_path


def test_state_shift_plan_binds_every_admission_and_trigger(tmp_path):
    proposal_path, trigger_path = _build_state_shift_proposal_chain(tmp_path)
    report = build_p13_state_shift_plan_report(
        proposal_path, output=tmp_path / "plan-report.json")
    trigger_payload = json.loads(trigger_path.read_text())
    trigger_digests = {
        item["receipt_digest"] for item in trigger_payload["triggers"]
    }
    plan = report["localized_update_plan"]
    assert plan["operation"] == "REVISE"
    assert plan["update_target"] == "UPDATE_CAUSAL_KNOWLEDGE"
    assert trigger_digests <= set(plan["evidence_refs"])
    assert len(report["admission_receipt_digests"]) == 2
    assert report["plan_eligible_for_anti_forgetting"] is True
    assert report["source_database_present"] is False
    assert report["anti_forgetting_present"] is False
    assert report["shadow_update_attempted"] is False
    assert report["canonical_memory_mutation"] == "none"
    assert report["production_runtime_imported"] is False


def test_state_shift_plan_rejects_rehashed_authority_drift(tmp_path):
    proposal_path, _trigger_path = _build_state_shift_proposal_chain(tmp_path)
    payload = json.loads(proposal_path.read_text())
    payload["canonical_memory_mutation"] = "write"
    payload.pop("report_digest")
    payload["report_digest"] = "sha256:" + hashlib.sha256(
        stable_dumps(payload).encode()).hexdigest()
    proposal_path.write_text(json.dumps(payload))
    with pytest.raises(P13StateShiftPlanReportError,
                       match="crosses an authority boundary"):
        build_p13_state_shift_plan_report(
            proposal_path, output=tmp_path / "plan-report.json")


def test_typed_state_shift_bundle_rejects_preregistration_drift(tmp_path):
    cohort, _manifest, routes, _ = _write_state_shift_inputs(tmp_path)
    audit = _write_state_shift_audit(tmp_path, cohort, routes)
    payload = json.loads(audit.read_text())
    payload["cases"][0]["evolution_reason"]["input_digests"][0] = "sha256:tampered"
    payload["cases"][0]["evolution_reason"].pop("receipt_digest")
    payload["cases"][0]["evolution_reason"].pop("receipt_id")
    audit.write_text(json.dumps(payload))
    with pytest.raises(P13StateShiftReasonBundleError, match="drifted"):
        build_p13_state_shift_reason_bundle(
            cohort, audit, output=tmp_path / "typed-state-shift-reasons.json")


def test_report_rejects_tampered_typed_reason_bundle(tmp_path):
    cohort, manifest, routes, _ = _write_state_shift_inputs(tmp_path)
    audit = _write_state_shift_audit(tmp_path, cohort, routes)
    bundle_path = tmp_path / "typed-state-shift-reasons.json"
    build_p13_state_shift_reason_bundle(cohort, audit, output=bundle_path)
    payload = json.loads(bundle_path.read_text())
    payload["canonical_memory_mutation"] = "write"
    bundle_path.write_text(json.dumps(payload))
    with pytest.raises(P13ShadowTriggerReportError, match="cannot mutate"):
        build_p13_shadow_trigger_report(
            cohort, manifest, routing_path=routes,
            typed_reason_bundle_path=bundle_path,
            output=tmp_path / "typed-report.json")


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


def test_report_binds_case_evidence_refs(tmp_path):
    cohort, manifest, routes = _write_inputs(tmp_path)
    case_refs = {}
    for case_id in ("case-0", "case-1"):
        evidence = tmp_path / f"{case_id}-reason.json"
        evidence.write_text(json.dumps({"case_id": case_id, "event": "review"}))
        case_refs[case_id] = [{
            "id": f"reason-{case_id}", "path": evidence.name,
            "sha256": "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest(),
        }]
    reasons = tmp_path / "reasons.json"
    reasons.write_text(json.dumps({
        "version": "p13-evolution-reason-receipt-v1",
        "campaign_id": "campaign",
        "cohort_receipt_digest": json.loads(cohort.read_text())["receipt_digest"],
        "label_source": "independent-review-v1",
        "evidence_refs": case_refs["case-0"],
        "case_evidence_refs": case_refs,
        "evolution_reasons": {"case-0": ["NOVELTY"], "case-1": ["CAPABILITY_GAP"]},
        "evaluation_only": True, "canonical_memory_mutation": "none",
    }))
    report = build_p13_shadow_trigger_report(
        cohort, manifest, routing_path=routes,
        evolution_reasons_path=reasons, output=tmp_path / "report.json")
    bound = report["evolution_reasons"]["case_evidence_refs"]
    assert set(bound) == {"case-0", "case-1"}
    assert bound["case-1"][0]["id"] == "reason-case-1"
