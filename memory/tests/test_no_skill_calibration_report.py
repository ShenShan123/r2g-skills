"""P15 calibration manifest/report builder tests."""
from __future__ import annotations

import hashlib
import json

import pytest

from contracts import MemoryRoutingDecision
from scripts.build_no_skill_calibration_report import (
    MANIFEST_VERSION, CalibrationReportError,
    build_no_skill_calibration_report,
)


def _samples():
    rows = []
    dimensions = {
        "mechanism_family": "density", "design": "aes", "platform": "sky130",
        "flow_regime": "route", "model_identity": "oracle-v1",
        "state_shift_dimension": "none",
    }
    for index, reason in enumerate(("NO_MATCH", "STATE_SHIFT", "RISK")):
        rows.append({
            "case_id": f"abstain-{index}",
            "predicted_decision": "NO_SKILL", "expected_decision": "NO_SKILL",
            "predicted_reason": reason, "expected_reason": reason,
            "confidence": 0.9, "strata": dimensions,
            "routing_receipt_id": f"routing-abstain-{index}",
        })
    for index in range(17):
        rows.append({
            "case_id": f"memory-{index}",
            "predicted_decision": "USE_MEMORY", "expected_decision": "USE_MEMORY",
            "confidence": 0.9, "strata": dimensions,
            "routing_receipt_id": f"routing-memory-{index}",
        })
    return rows


def _manifest(tmp_path, **overrides):
    evidence = tmp_path / "oracle-labels.json"
    evidence.write_text('{"source":"independent-oracle"}\n')
    payload = {
        "version": MANIFEST_VERSION,
        "campaign_id": "p15-test",
        "oracle_label_source": "independent-oracle-v1",
        "samples": _samples(),
        "evidence_refs": [{
            "id": "oracle-labels", "path": evidence.name,
            "sha256": "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest(),
        }],
    }
    payload.update(overrides)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload, sort_keys=True) + "\n")
    return path, evidence


def test_builder_binds_external_labels_and_never_promotes(tmp_path):
    manifest, evidence = _manifest(tmp_path)
    report_path = tmp_path / "report.json"
    report = build_no_skill_calibration_report(manifest, output=report_path)
    assert report["receipt"]["status"] == "PASS"
    assert report["receipt"]["sample_count"] == 20
    assert report["oracle_label_source"] == "independent-oracle-v1"
    assert report["no_skill_calibration"]["version"] == "no-skill-calibration-v1"
    assert report["canonical_memory_mutation"] == "none"
    assert report["promotion_attempted"] is False
    assert report["production_integration"] == "not_attempted"
    assert report["memory_docs_submitted"] is False
    assert report["evidence_refs"][0]["path"] == str(evidence.resolve())
    assert json.loads(report_path.read_text()) == report


def test_builder_rejects_stale_evidence_and_outcome_inference(tmp_path):
    manifest, _ = _manifest(tmp_path)
    payload = json.loads(manifest.read_text())
    payload["evidence_refs"][0]["sha256"] = "sha256:stale"
    manifest.write_text(json.dumps(payload))
    with pytest.raises(CalibrationReportError, match="digest mismatch"):
        build_no_skill_calibration_report(manifest, output=tmp_path / "report.json")

    manifest, _ = _manifest(tmp_path)
    payload = json.loads(manifest.read_text())
    payload["samples"][0]["memory_outcome"] = "PASS"
    manifest.write_text(json.dumps(payload))
    with pytest.raises(CalibrationReportError, match="outcome or gold"):
        build_no_skill_calibration_report(manifest, output=tmp_path / "report.json")


def test_builder_requires_explicit_oracle_source_and_evidence_refs(tmp_path):
    manifest, _ = _manifest(tmp_path, oracle_label_source="")
    with pytest.raises(CalibrationReportError, match="oracle_label_source"):
        build_no_skill_calibration_report(manifest, output=tmp_path / "report.json")
    manifest, _ = _manifest(tmp_path, evidence_refs=[])
    with pytest.raises(CalibrationReportError, match="evidence_refs"):
        build_no_skill_calibration_report(manifest, output=tmp_path / "report.json")


def test_builder_assembles_typed_routing_inputs_without_manual_prediction(tmp_path):
    manifest, _ = _manifest(tmp_path)
    payload = json.loads(manifest.read_text())
    payload.pop("samples")
    strata = {
        "mechanism_family": "density", "design": "aes", "platform": "sky130",
        "flow_regime": "route", "model_identity": "oracle-v1",
        "state_shift_dimension": "none",
    }
    routes = {}
    index = {}
    labels = {}
    reasons = ("NO_MATCH", "STATE_SHIFT", "RISK")
    for number in range(20):
        case_id = f"case-{number}"
        decision = "CONSIDER" if number >= 6 else "NO_SKILL"
        reason = reasons[number % len(reasons)] if decision == "NO_SKILL" else None
        route = MemoryRoutingDecision(
            decision=decision, resolved_state_id="state",
            selected_rule_ids=("rule",) if decision == "CONSIDER" else (),
            selected_path_ids=("path",) if decision == "CONSIDER" else (),
            selected_asset_ids=(), applicability={"status": "APPLICABLE"},
            causal_support={"status": "SUPPORTED"}, risk={}, abstain_reasons=(),
            no_memory_budget=1, memory_budget=1 if decision == "CONSIDER" else 0,
            no_skill_reason=reason)
        routes[case_id] = {**route.to_dict(), "routing_receipt_id": route.routing_receipt_id}
        index[case_id] = {"routing_receipt_id": route.routing_receipt_id}
        labels[case_id] = {
            "expected_decision": "NO_SKILL" if number < 6 else "USE_MEMORY",
            "expected_reason": reason,
            "confidence": 0.9, "strata": strata,
        }
    payload.update({"paired_routing_index": {"case_receipts": index},
                    "routing_decisions": routes, "oracle_labels": labels})
    manifest.write_text(json.dumps(payload, sort_keys=True) + "\n")
    report = build_no_skill_calibration_report(manifest, output=tmp_path / "report.json")
    assert report["input_mode"] == "paired_routing_index"
    assert report["receipt"]["status"] == "PASS"
