"""P9 report builder binds local evidence and preserves fail-closed state."""
from __future__ import annotations

import hashlib
import json

import pytest

from scripts.build_production_gate_report import (
    MANIFEST_VERSION,
    build_production_gate_report,
)
from tehm.retrieval.production_gate import ProductionGateError


def _metrics():
    return {
        "baseline_harmful_activation_rate": 0.4,
        "memory_harmful_activation_rate": 0.1,
        "no_skill_precision": 0.9,
        "no_skill_recall": 0.9,
        "no_skill_cases": 10,
        "paired_cases": 10,
        "memory_interference_rate": 0.0,
        "candidate_diversity": 0.8,
        "authority_verified": True,
        "authority_receipt_id": "authority-1",
        "authority_receipt_digest": "sha256:authority",
        "rollback_verified": True,
        "rollback_receipt_id": "rollback-1",
        "rollback_receipt_digest": "sha256:rollback",
    }


def _write_manifest(tmp_path, *, expected_digest=None, metrics=None):
    evidence = tmp_path / "oracle.json"
    evidence.write_text('{"verdict":"PASS"}\n')
    ref = {"id": "oracle", "path": evidence.name}
    if expected_digest is not None:
        ref["sha256"] = expected_digest
    manifest = {
        "version": MANIFEST_VERSION,
        "campaign_id": "p9-test",
        "metrics": _metrics() if metrics is None else metrics,
        "evidence_refs": [ref],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest, sort_keys=True) + "\n")
    return path, evidence


def test_report_builder_binds_digest_and_replays_receipt(tmp_path):
    manifest, evidence = _write_manifest(tmp_path)
    report_path = tmp_path / "report.json"
    report = build_production_gate_report(manifest, output=report_path)
    assert report["receipt"]["eligible"] is True
    assert report["receipt"]["production_integration"] == "not_attempted"
    assert report["memory_docs_submitted"] is False
    assert report["evidence_refs"][0]["path"] == str(evidence.resolve())
    expected = "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest()
    assert report["evidence_refs"][0]["sha256"] == expected
    decoded = json.loads(report_path.read_text())
    assert decoded == report


def test_report_builder_rejects_stale_evidence_digest(tmp_path):
    manifest, _ = _write_manifest(tmp_path, expected_digest="sha256:stale")
    with pytest.raises(ProductionGateError, match="digest mismatch"):
        build_production_gate_report(manifest, output=tmp_path / "report.json")


def test_report_builder_preserves_not_established_metrics(tmp_path):
    manifest, _ = _write_manifest(tmp_path, metrics={})
    report = build_production_gate_report(manifest, output=tmp_path / "report.json")
    assert report["receipt"]["eligible"] is False
    assert "efficacy" in report["receipt"]["not_established"]
    assert report["promotion_attempted"] is False
    assert report["canonical_memory_mutation"] == "none"
