"""Frozen-input binding and negative controls; synthetic, not empirical evidence."""
from dataclasses import replace
import json

import pytest

from contracts import MemoryRoutingDecision
from scripts.build_p13_interference_reason_bundle import (
    InterferenceReasonBundleError, build_interference_reason_bundle,
)
from scripts.build_p13_shadow_trigger_report import _digest, _sha256, _typed_reasons
from tehm.evaluation import OrfsPairedCohortReceipt
from tehm.evaluation.orfs_paired_utility import apply_orfs_paired_utility_contract
from tehm.physical.utility_contracts import p12_density_relief_interference_nonregression_v1
from test_orfs_paired_utility import _bundle


def _fixture(tmp_path, *, harm=True, incomplete=False, case_id=None, training=False):
    paired, arms = _bundle()
    if case_id is not None:
        paired = replace(paired, case_id=case_id, lineage_id="lineage-" + case_id,
                         arm_receipts={arm: replace(item, case_id=case_id)
                                       for arm, item in paired.arm_receipts.items()})
    candidate = arms["ALWAYS_MEMORY"]
    route = MemoryRoutingDecision(
        decision="CONSIDER", resolved_state_id=candidate.resolved_state_id,
        selected_rule_ids=(), selected_path_ids=candidate.causal_path_ids,
        selected_asset_ids=(), applicability={"status": "APPLICABLE"},
        causal_support={"status": "SUPPORTED"}, risk={}, abstain_reasons=(),
        no_memory_budget=3, memory_budget=1)
    paired = replace(paired, routing_receipt_id=route.routing_receipt_id)
    if harm:
        paired = apply_orfs_paired_utility_contract(
            paired, arms, contract=p12_density_relief_interference_nonregression_v1())
    if incomplete:
        paired = replace(paired, arm_receipts={
            **paired.arm_receipts, "ALWAYS_MEMORY": replace(
                paired.arm_receipts["ALWAYS_MEMORY"], metadata={})})
    cid = paired.case_id
    case_partition = {"dataset_split": "training", "role": "training",
                      "learner_eligible": True} if training else {}
    candidate_path, routes_path, authority_path = (
        tmp_path / name for name in ("candidate.json", "routes.json", "authority.json"))
    candidate_path.write_text(json.dumps(candidate.to_dict()))
    routes_path.write_text(json.dumps({"routes": {cid: route.to_dict()}}))
    authority = {
        "version": "r3-8-source-bound-orfs-input-authority-v1",
        "campaign_id": "test-interference",
        "cases": {cid: {"source_digest": "sha256:source", "route": {
            **route.to_dict(), "decision_digest": route.decision_digest}}},
        "candidate_freeze": {cid: {
            "path": str(candidate_path), "sha256": _sha256(candidate_path),
            "candidate_digest": candidate.candidate_digest}},
        "routing_decisions": {"path": str(routes_path), "sha256": _sha256(routes_path)},
        "evaluation_only": True, "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
        "eda_executed": False,
        **{field: True for field in (
            "actual_router_used", "actual_selector_used",
            "actual_runtime_binding_used", "actual_candidate_builder_used")},
        **({"learner_partition": {"learner_eligible": True,
                                   "cases": {cid: case_partition}}} if training else {}),
    }
    authority["authority_digest"] = _digest(authority)
    authority_path.write_text(json.dumps(authority))
    manifest = {
        "campaign_id": "test-interference", "cases": [{
            "case_id": cid, "lineage_id": paired.lineage_id,
            "source_digest": "sha256:source",
            "candidate_paths": {"ALWAYS_MEMORY": str(candidate_path)},
            **case_partition}],
        **({"learner_eligible": True} if training else {}),
        "input_authority": {"path": str(authority_path),
                            "sha256": _sha256(authority_path),
                            "authority_digest": authority["authority_digest"]},
    }
    receipt = OrfsPairedCohortReceipt(
        campaign_id=manifest["campaign_id"], case_receipts={cid: paired},
        source_digests={cid: "sha256:source"},
        source_content_digests={cid: "sha256:content"}, candidate_budget=3,
        toolchain_digest="sha256:tool", oracle_digest="sha256:oracle",
        platform_digest="sha256:platform", pdk_digest="sha256:pdk",
        campaign_manifest_digest=_digest(manifest))
    cohort_path, manifest_path = tmp_path / "cohort.json", tmp_path / "manifest.json"
    cohort_path.write_text(json.dumps({**receipt.to_dict(),
                                     "receipt_digest": receipt.receipt_digest}))
    manifest_path.write_text(json.dumps(manifest))
    return cohort_path, manifest_path, receipt


def test_interference_bundle_replays_typed_detector_and_existing_bridge(tmp_path):
    cohort, manifest, receipt = _fixture(tmp_path)
    output = tmp_path / "bundle.json"
    payload = build_interference_reason_bundle(cohort, manifest, output=output)
    assert payload["status"] == "ALL_CASES_DERIVED"
    assert payload["derived_count"] == 1
    reason, derivations, _ = _typed_reasons(
        payload, output, set(receipt.case_receipts), campaign_id=receipt.campaign_id,
        cohort_digest=receipt.receipt_digest)
    assert reason.evolution_reasons[next(iter(receipt.case_receipts))] == (
        "MEMORY_INTERFERENCE",)
    assert len(derivations) == 1
    assert payload["mutation_authority_granted"] is False
    assert payload["shadow_mutation_eligible"] is False
    assert all(not item["admitted"] for item in payload["admission_receipts"].values())


def test_prospectively_bound_training_partition_admits_shadow_evidence_only(tmp_path):
    cohort, manifest, _ = _fixture(tmp_path, training=True)
    payload = build_interference_reason_bundle(cohort, manifest, output=tmp_path / "bundle.json")
    assert payload["prospective_learner_partition_bound"] is True
    assert payload["shadow_mutation_eligible"] is True
    assert all(item["admitted"] for item in payload["admission_receipts"].values())
    assert payload["mutation_authority_granted"] is False


def test_no_harm_retains_without_reason_envelope(tmp_path):
    cohort, manifest, _ = _fixture(tmp_path, harm=False)
    payload = build_interference_reason_bundle(cohort, manifest, output=tmp_path / "report.json")
    assert payload["status"] == "NO_EVOLUTION_SIGNAL"
    assert payload["version"] == "p13-interference-detector-report-v1"
    assert "reason_receipt" not in payload


def test_manifest_drift_rejected(tmp_path):
    cohort, manifest, _ = _fixture(tmp_path)
    raw = json.loads(manifest.read_text())
    raw["campaign_id"] = "other"
    manifest.write_text(json.dumps(raw))
    with pytest.raises(InterferenceReasonBundleError, match="manifest binding"):
        build_interference_reason_bundle(cohort, manifest, output=tmp_path / "bundle.json")


def test_candidate_bytes_drift_rejected(tmp_path):
    cohort, manifest, _ = _fixture(tmp_path)
    (tmp_path / "candidate.json").write_text("{}")
    with pytest.raises(InterferenceReasonBundleError, match="candidate file digest"):
        build_interference_reason_bundle(cohort, manifest, output=tmp_path / "bundle.json")


def test_existing_output_never_overwritten(tmp_path):
    cohort, manifest, _ = _fixture(tmp_path)
    output = tmp_path / "bundle.json"
    output.write_text("preserve")
    with pytest.raises(InterferenceReasonBundleError, match="output must be new"):
        build_interference_reason_bundle(cohort, manifest, output=output)
    assert output.read_text() == "preserve"


def test_incomplete_oracle_is_rejected_not_a_harm_label(tmp_path):
    cohort, manifest, _ = _fixture(tmp_path, incomplete=True)
    payload = build_interference_reason_bundle(cohort, manifest, output=tmp_path / "report.json")
    assert payload["status"] == "DETECTOR_REJECTED"
    assert payload["derived_count"] == 0
    assert payload["derivation_errors"]
    assert "reason_receipt" not in payload


def test_mixed_cohort_keeps_every_case_without_admission_bundle(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _, ma, ra = _fixture(a, harm=True, case_id="a")
    _, mb, rb = _fixture(b, harm=False, case_id="b")
    manifest_a, manifest_b = (json.loads(p.read_text()) for p in (ma, mb))
    authority_a, authority_b = (json.loads((p / "authority.json").read_text()) for p in (a, b))
    routes = {"routes": {**json.loads((a / "routes.json").read_text())["routes"],
                         **json.loads((b / "routes.json").read_text())["routes"]}}
    route_path = tmp_path / "routes.json"
    route_path.write_text(json.dumps(routes))
    for key in ("cases", "candidate_freeze"):
        authority_a[key].update(authority_b[key])
    authority_a["cases"]["b"]["source_digest"] = "sha256:source-b"
    authority_a["routing_decisions"] = {"path": str(route_path), "sha256": _sha256(route_path)}
    authority_a.pop("authority_digest")
    authority_a["authority_digest"] = _digest(authority_a)
    authority_path = tmp_path / "authority.json"
    authority_path.write_text(json.dumps(authority_a))
    manifest_a["cases"] += manifest_b["cases"]
    manifest_a["cases"][1]["source_digest"] = "sha256:source-b"
    manifest_a["input_authority"] = {
        "path": str(authority_path), "sha256": _sha256(authority_path),
        "authority_digest": authority_a["authority_digest"]}
    receipt = replace(ra, case_receipts={**ra.case_receipts, **rb.case_receipts},
                      source_digests={"a": "sha256:source", "b": "sha256:source-b"},
                      source_content_digests={"a": "sha256:content-a", "b": "sha256:content-b"},
                      campaign_manifest_digest=_digest(manifest_a))
    manifest_path, cohort_path = tmp_path / "manifest.json", tmp_path / "cohort.json"
    manifest_path.write_text(json.dumps(manifest_a))
    cohort_path.write_text(json.dumps(receipt.to_dict()))
    payload = build_interference_reason_bundle(cohort_path, manifest_path, output=tmp_path / "report.json")
    assert payload["status"] == "MIXED_SIGNALS"
    assert payload["case_count"] == 2
    assert payload["derived_count"] == 1
    assert payload["no_signal_cases"] == ["b"]
    assert "reason_receipt" not in payload
