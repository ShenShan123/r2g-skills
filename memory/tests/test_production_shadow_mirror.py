"""P17 production shadow-mirror preparation contract tests."""
from __future__ import annotations

import json

import pytest

from tehm.evaluation.production_readiness import ProductionReadinessReceipt
from tehm.evaluation.production_shadow_mirror import (
    ProductionShadowMirrorError, build_shadow_mirror_report,
    prepare_shadow_mirror, replay_shadow_mirror, replay_shadow_mirror_report,
)


def _readiness(*, eligible: bool) -> ProductionReadinessReceipt:
    statuses = {
        "multi_lineage": "PASS",
        "reason_stratified_calibration": "PASS",
        "mir_upper_ci": "PASS" if eligible else "FAIL",
        "repair_pareto": "PASS",
        "anti_forgetting": "PASS",
        "authority_replay": "PASS",
        "rollback": "PASS",
    }
    return ProductionReadinessReceipt(
        campaign_id="shadow-test-campaign", input_refs=(),
        gates={name: value == "PASS" for name, value in statuses.items()},
        gate_status=statuses, metrics={},
        production_gate={"eligible": eligible}, eligible=eligible,
        reasons=(() if eligible else ("mir_upper_ci:fail",)),
        schema_contract_status="PASS",
    )


def test_ineligible_readiness_produces_blocked_receipt():
    receipt = prepare_shadow_mirror(_readiness(eligible=False))
    assert receipt.mirror_status == "BLOCKED_READINESS"
    assert receipt.readiness_eligible is False
    assert receipt.comparison_count == 0
    assert "readiness_ineligible" in receipt.reasons
    assert receipt.canonical_memory_mutation == "none"
    assert receipt.production_runtime_imported is False
    assert receipt.promotion_attempted is False
    assert receipt.memory_docs_submitted is False
    replayed = replay_shadow_mirror({
        **receipt.to_dict(),
        "receipt_id": receipt.receipt_id,
        "receipt_digest": receipt.receipt_digest,
    })
    assert replayed.to_dict() == receipt.to_dict()


def test_ineligible_readiness_cannot_carry_shadow_observations():
    with pytest.raises(ProductionShadowMirrorError, match="before readiness"):
        prepare_shadow_mirror(
            _readiness(eligible=False),
            comparisons=[{
                "case_id": "case-1",
                "base_decision": {"decision": "NO_SKILL"},
                "shadow_decision": {"decision": "CONSIDER"},
            }],
        )


def test_eligible_readiness_records_only_comparison_receipt():
    receipt = prepare_shadow_mirror(
        {"readiness": _readiness(eligible=True).to_dict()},
        allowlist=("case-1",),
        comparisons=[{
            "case_id": "case-1",
            "base_decision": {"decision": "NO_SKILL", "selected_rule_ids": []},
            "shadow_decision": {"decision": "CONSIDER", "selected_rule_ids": ["r1"]},
        }],
    )
    assert receipt.mirror_status == "READY_FOR_SHADOW_COMPARISON"
    assert receipt.readiness_eligible is True
    assert receipt.comparison_count == 1
    assert receipt.changed_count == 1
    row = receipt.comparisons[0]
    assert row["base_decision_digest"].startswith("sha256:")
    assert row["shadow_decision_digest"].startswith("sha256:")
    assert row["changed"] is True
    assert receipt.production_integration == "not_attempted"
    replayed = replay_shadow_mirror({
        **receipt.to_dict(),
        "receipt_id": receipt.receipt_id,
        "receipt_digest": receipt.receipt_digest,
    })
    assert replayed.receipt_digest == receipt.receipt_digest


def test_comparison_must_stay_inside_allowlist():
    with pytest.raises(ProductionShadowMirrorError, match="allowlist"):
        prepare_shadow_mirror(
            _readiness(eligible=True), allowlist=("case-1",),
            comparisons=[{
                "case_id": "case-2",
                "base_decision": {},
                "shadow_decision": {},
            }],
        )


def test_tampered_mirror_receipt_is_rejected():
    receipt = prepare_shadow_mirror(_readiness(eligible=False))
    payload = {
        **receipt.to_dict(),
        "receipt_id": receipt.receipt_id,
        "receipt_digest": receipt.receipt_digest,
        "changed_count": 1,
    }
    with pytest.raises(ProductionShadowMirrorError, match="changed_count"):
        replay_shadow_mirror(payload)


def test_tampered_inner_decision_digest_is_rejected():
    receipt = prepare_shadow_mirror(
        _readiness(eligible=True),
        comparisons=[{
            "case_id": "case-1",
            "base_decision": {"decision": "NO_SKILL"},
            "shadow_decision": {"decision": "NO_SKILL"},
        }],
    )
    payload = {
        **receipt.to_dict(),
        "receipt_id": receipt.receipt_id,
        "receipt_digest": receipt.receipt_digest,
    }
    payload["comparisons"][0]["base_decision"] = {"decision": "CONSIDER"}
    with pytest.raises(ProductionShadowMirrorError, match="decision digest"):
        replay_shadow_mirror(payload)


def test_readiness_wrapper_digest_and_firewall_are_bound():
    readiness = _readiness(eligible=False)
    wrapper = {
        "readiness": readiness.to_dict(),
        "receipt_digest": readiness.receipt_digest,
        "production_integration": "not_attempted",
        "canonical_memory_mutation": "none",
        "memory_docs_submitted": False,
    }
    blocked = prepare_shadow_mirror(wrapper)
    assert blocked.mirror_status == "BLOCKED_READINESS"
    wrapper["receipt_digest"] = "sha256:" + "0" * 64
    with pytest.raises(ProductionShadowMirrorError, match="wrapper receipt digest"):
        prepare_shadow_mirror(wrapper)
    wrapper["receipt_digest"] = readiness.receipt_digest
    wrapper["memory_docs_submitted"] = True
    with pytest.raises(ProductionShadowMirrorError, match="memory_docs_submitted"):
        prepare_shadow_mirror(wrapper)


def _write_readiness_report(path, *, eligible: bool):
    readiness = _readiness(eligible=eligible)
    payload = {
        **readiness.to_dict(),
        "receipt_id": readiness.receipt_id,
        "receipt_digest": readiness.receipt_digest,
    }
    report = {
        "receipt": payload,
        "readiness": payload,
        "receipt_id": readiness.receipt_id,
        "receipt_digest": readiness.receipt_digest,
        "production_integration": "not_attempted",
        "canonical_memory_mutation": "none",
        "memory_docs_submitted": False,
    }
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return readiness


def test_cli_report_binds_readiness_file_and_replays(tmp_path):
    readiness_path = tmp_path / "readiness.json"
    readiness = _write_readiness_report(readiness_path, eligible=False)
    output = tmp_path / "shadow-mirror.json"
    report = build_shadow_mirror_report(readiness_path, output=output)
    assert report["receipt"]["mirror_status"] == "BLOCKED_READINESS"
    assert report["readiness_ref"]["sha256"] == (
        "sha256:" + __import__("hashlib").sha256(readiness_path.read_bytes()).hexdigest())
    replayed = replay_shadow_mirror_report(output)
    assert replayed.readiness_receipt_digest == readiness.receipt_digest

    readiness_path.write_text(readiness_path.read_text() + "\n")
    with pytest.raises(ProductionShadowMirrorError, match="input digest drifted"):
        replay_shadow_mirror_report(output)


def test_cli_comparison_source_is_content_bound(tmp_path):
    readiness_path = tmp_path / "readiness.json"
    _write_readiness_report(readiness_path, eligible=True)
    comparisons_path = tmp_path / "comparisons.json"
    comparisons_path.write_text(json.dumps([{
        "case_id": "case-1",
        "base_decision": {"decision": "NO_SKILL"},
        "shadow_decision": {"decision": "CONSIDER"},
    }]))
    output = tmp_path / "shadow-mirror.json"
    report = build_shadow_mirror_report(
        readiness_path, output=output, comparisons_path=comparisons_path,
        allowlist=("case-1",))
    assert report["comparison_ref"]["content_digest"].startswith("sha256:")
    replayed = replay_shadow_mirror_report(output)
    assert replayed.changed_count == 1
    comparisons_path.write_text(comparisons_path.read_text().replace("CONSIDER", "NO_SKILL"))
    with pytest.raises(ProductionShadowMirrorError, match="input digest drifted"):
        replay_shadow_mirror_report(output)
