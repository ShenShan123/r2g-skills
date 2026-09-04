"""P13 anti-forgetting witness provenance binder tests."""
from __future__ import annotations

import hashlib
import json

import pytest

from scripts.build_p13_anti_forgetting_witness import (
    P13AntiForgettingError, build_p13_anti_forgetting_witness,
)
from tehm.evolution import AntiForgettingWitness


def _sha256(path):
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(tmp_path):
    specs = (
        ("target_replay", "target", "passed"),
        ("non_target_regression", "non-target", "regression_free"),
        ("heldout_audit", "heldout", "passed"),
        ("rollback", "rollback", "verified"),
    )
    payload = {
        "version": "p13-anti-forgetting-manifest-v1",
        "campaign_id": "campaign",
        "case_id": "case-0",
    }
    for name, stem, status in specs:
        path = tmp_path / f"{stem}.json"
        path.write_text(json.dumps({"receipt": stem, "verdict": "PASS"}))
        payload[name] = {
            "receipt_id": f"receipt:{stem}", "path": path.name,
            "sha256": _sha256(path), status: True,
        }
    payload["rollback"]["pointer"] = "snapshot:before"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(payload))
    return manifest, payload


def test_binder_emits_replayable_eligible_witness(tmp_path):
    manifest, _payload = _manifest(tmp_path)
    report = build_p13_anti_forgetting_witness(
        manifest, output=tmp_path / "witness.json")
    witness = AntiForgettingWitness.from_dict(report["witness"])
    assert witness.eligible is True
    assert report["eligible"] is True
    assert report["canonical_memory_mutation"] == "none"
    assert report["production_runtime_imported"] is False
    assert report["memory_docs_submitted"] is False
    assert witness.receipt_digest == report["witness"]["receipt_digest"]
    assert len(witness.evidence_refs) == 4


def test_binder_preserves_failed_gate_as_ineligible_receipt(tmp_path):
    manifest, payload = _manifest(tmp_path)
    payload["heldout_audit"]["passed"] = False
    manifest.write_text(json.dumps(payload))
    report = build_p13_anti_forgetting_witness(
        manifest, output=tmp_path / "witness.json")
    assert report["eligible"] is False
    assert AntiForgettingWitness.from_dict(report["witness"]).eligible is False


def test_binder_rejects_digest_drift_and_duplicate_files(tmp_path):
    manifest, payload = _manifest(tmp_path)
    payload["target_replay"]["sha256"] = "sha256:" + "0" * 64
    manifest.write_text(json.dumps(payload))
    with pytest.raises(P13AntiForgettingError, match="does not match"):
        build_p13_anti_forgetting_witness(
            manifest, output=tmp_path / "witness.json")

    manifest, payload = _manifest(tmp_path)
    payload["heldout_audit"]["path"] = payload["target_replay"]["path"]
    payload["heldout_audit"]["sha256"] = payload["target_replay"]["sha256"]
    manifest.write_text(json.dumps(payload))
    with pytest.raises(P13AntiForgettingError, match="distinct"):
        build_p13_anti_forgetting_witness(
            manifest, output=tmp_path / "witness.json")


def test_binder_rejects_manifest_or_output_reuse(tmp_path):
    manifest, payload = _manifest(tmp_path)
    payload["target_replay"]["path"] = manifest.name
    payload["target_replay"]["sha256"] = _sha256(manifest)
    manifest.write_text(json.dumps(payload))
    with pytest.raises(P13AntiForgettingError, match="independent"):
        build_p13_anti_forgetting_witness(
            manifest, output=tmp_path / "witness.json")

    manifest, _payload = _manifest(tmp_path)
    with pytest.raises(P13AntiForgettingError, match="separate"):
        build_p13_anti_forgetting_witness(manifest, output=manifest)
