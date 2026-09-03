"""Typed, evaluation-only RTL conformal calibration tests."""
from __future__ import annotations

import json

import pytest

from tehm.rtl.conformal import (
    RTLConformalError, RTLConformalSample, calibrate_rtl_obligations,
)


def _record(case_id: str, lineage_id: str, *, domain: str = "rtl.GUARD_STRENGTHEN"):
    return {
        "record_id": f"rtl:{case_id}",
        "lineage_id": lineage_id,
        "action": {
            "domain": domain,
            "transformation_family": "GUARD_STRENGTHEN",
            "payload": {"compatibility_profile": "rtl.fsm.single_guard.v1"},
        },
        "verification": {
            "verdict": "PASS",
            "oracle_type": "REGRESSION",
            "oracle_complete": True,
            "target": {"verdict": "PASS", "compile_verdict": "PASS"},
            "regression": {"verdict": "PASS", "compile_verdict": "PASS"},
        },
    }


def _receipt():
    samples = [
        RTLConformalSample.from_record(_record("a", "lineage-a"), case_id="a"),
        RTLConformalSample.from_record(_record("b", "lineage-b"), case_id="b"),
        RTLConformalSample.from_record(_record("c", "lineage-c"), case_id="c"),
    ]
    return calibrate_rtl_obligations(
        samples,
        calibration_digest="sha256:" + "a" * 64,
        training_lineages=("training-lineage",),
        min_lineages=3,
    )


def test_rtl_conformal_receipt_is_content_addressed_and_authority_typed():
    receipt = _receipt()
    assert receipt.eligible is True
    assert receipt.payload["coverage"] == {
        "covered": 9, "total": 9, "coverage": 1.0,
        "required_coverage": 0.8,
    }
    replay = receipt.from_dict(json.loads(json.dumps(receipt.to_dict())))
    assert replay.receipt_digest == receipt.receipt_digest
    authority = receipt.authority_payload()
    assert authority["method"] == "split_conformal_rtl_obligation_set_v1"
    assert authority["calibration_action_domain"] == "rtl.GUARD_STRENGTHEN"
    assert authority["calibration_transformation_family"] == "GUARD_STRENGTHEN"
    assert authority["calibration_compatibility_profile"] == "rtl.fsm.single_guard.v1"


def test_rtl_conformal_receipt_rejects_tampering_and_lineage_overlap():
    receipt = _receipt()
    tampered = json.loads(json.dumps(receipt.to_dict()))
    tampered["coverage"]["covered"] = 8
    with pytest.raises(RTLConformalError, match="digest mismatch"):
        receipt.from_dict(tampered)
    sample = RTLConformalSample.from_record(
        _record("overlap", "training-lineage"), case_id="overlap")
    with pytest.raises(RTLConformalError, match="lineage overlap"):
        calibrate_rtl_obligations(
            [sample], calibration_digest="sha256:" + "b" * 64,
            training_lineages=("training-lineage",), min_lineages=1)


def test_rtl_conformal_rejects_unknown_or_mixed_action_population():
    bad = _record("unknown", "lineage-unknown")
    bad["verification"]["target"]["verdict"] = "UNKNOWN"
    with pytest.raises(RTLConformalError, match="must be PASS or FAIL"):
        RTLConformalSample.from_record(bad, case_id="unknown")
    first = RTLConformalSample.from_record(_record("x", "lineage-x"), case_id="x")
    second = RTLConformalSample.from_record(
        _record("y", "lineage-y", domain="rtl.RESET_RESTORE"), case_id="y")
    with pytest.raises(RTLConformalError, match="mix typed action identities"):
        calibrate_rtl_obligations(
            [first, second], calibration_digest="sha256:" + "c" * 64,
            min_lineages=1)
