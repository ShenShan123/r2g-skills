"""Asset Memory registry, gap, validation, and lifecycle tests."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tehm.assets import (
    bind_rtl_asset_to_project, build_rtl_asset_proposal, detect_capability_gaps,
    evaluate_asset_authority, get_asset, register_asset_proposal, set_asset_status,
    validate_rtl_asset_project, validate_rtl_rewrite_asset,
)
from tehm.causal.rtl import capture_rtl_causal_fragment
from tehm.rtl.rtl_oracle import IcarusOracle


PROJECTS = Path(__file__).resolve().parent / "fixtures" / "rtl_projects"


def _proposal():
    gap = {
        "gap_id": "gap-handshake",
        "evidence_transitions": ["transition-a", "transition-b"],
    }
    return build_rtl_asset_proposal(
        gap, name="rtl.guard_strengthen.template",
        transformation_family="GUARD_STRENGTHEN",
        action_payload_template={
            "domain": "rtl.GUARD_STRENGTHEN", "module": "req_ack_fsm",
            "source_state": "SEND", "target_state": "DONE",
            "add_condition": "ack",
        },
        compatibility_profile="rtl.fsm.single_guard.v1",
        verifier_obligations=("RTL_TARGET_TEST_PASS", "RTL_FROZEN_REGRESSION_PASS"),
    )


def test_asset_proposal_is_static_and_never_promotion_eligible(tmp_tehm):
    conn, _, _ = tmp_tehm
    proposal = _proposal()
    source = (PROJECTS / "req_ack_bug" / "rtl" / "req_ack_fsm.v").read_text()
    static = validate_rtl_rewrite_asset(proposal.to_dict(), source)
    assert static.static_valid is True
    assert static.independent_verifier is False
    receipt = register_asset_proposal(conn, proposal)
    assert receipt.status == "draft"
    set_asset_status(conn, asset_id=receipt.asset_id,
                     target_scope=receipt.target_scope, status="shadow")
    with pytest.raises(ValueError, match="invalid asset status transition"):
        set_asset_status(conn, asset_id=receipt.asset_id,
                         target_scope=receipt.target_scope, status="promoted",
                         gates={})


def test_asset_candidate_requires_all_independent_gates(tmp_tehm):
    conn, _, _ = tmp_tehm
    receipt = register_asset_proposal(conn, _proposal())
    set_asset_status(conn, asset_id=receipt.asset_id,
                     target_scope=receipt.target_scope, status="shadow")
    set_asset_status(conn, asset_id=receipt.asset_id,
                     target_scope=receipt.target_scope, status="candidate")
    gates = {
        "schema_valid": True, "static_valid": True,
        "independent_verifier": True, "compatibility_verified": True,
        "cross_lineage_verified": True, "regression_zero": True,
        "rollback_verified": True,
    }
    promoted = set_asset_status(
        conn, asset_id=receipt.asset_id, target_scope=receipt.target_scope,
        status="promoted", gates=gates)
    assert promoted.status == "promoted"
    assert get_asset(conn, receipt.asset_id)["asset_id"] == receipt.asset_id


def test_asset_authority_is_derived_from_receipts_and_rollback(tmp_tehm):
    proposal = _proposal().to_dict()
    bound_one = bind_rtl_asset_to_project(
        proposal, PROJECTS / "req_ack_bug",
        expected_mechanism_family="HANDSHAKE_COMPLETION")
    bound_two = bind_rtl_asset_to_project(
        proposal, PROJECTS / "req_ack_bug2",
        expected_mechanism_family="HANDSHAKE_COMPLETION")
    validation = {
        "static_valid": True, "independent_verifier": True,
        "oracle_verdict": "PASS", "regression_verdict": "PASS", "errors": [],
    }
    receipt = evaluate_asset_authority(
        proposal, validation_receipts=[validation, validation],
        bindings=[bound_one, bound_two], rollback_receipt={"verified": True},
        target_scope="rtl.fsm.single_guard.v1")
    assert receipt.eligible is True
    assert all(receipt.checks.values())
    failed = evaluate_asset_authority(
        proposal, validation_receipts=[validation, validation],
        bindings=[bound_one, bound_two], rollback_receipt={"verified": False},
        target_scope="rtl.fsm.single_guard.v1")
    assert failed.eligible is False
    assert "rollback_verified" in failed.missing


def test_asset_project_validation_uses_real_icarus_oracle(tmp_tehm):
    oracle = IcarusOracle()
    if not oracle.available:
        pytest.skip("Icarus unavailable")
    receipt = validate_rtl_asset_project(
        _proposal().to_dict(), PROJECTS / "req_ack_bug", oracle=oracle)
    assert receipt.status == "SHADOW_ORACLE_PASS"
    assert receipt.independent_verifier is True
    assert receipt.oracle_verdict == "PASS"


def test_asset_binding_reuses_typed_manifest_slots_across_lineages(tmp_tehm):
    proposal = _proposal().to_dict()
    bound = bind_rtl_asset_to_project(
        proposal, PROJECTS / "req_ack_bug2",
        expected_mechanism_family="HANDSHAKE_COMPLETION")
    payload = bound["definition"]["action"]["payload"]
    assert payload["source_state"] == "WRITE"
    assert payload["target_state"] == "VERIFY"
    assert payload["add_condition"] == "wr_ack"
    assert bound["provenance"]["binding_contract"] == "manifest_fix_v1"
    assert bound["provenance"]["binding_digest"].startswith("sha256:")
    oracle = IcarusOracle()
    if not oracle.available:
        pytest.skip("Icarus unavailable")
    receipt = validate_rtl_asset_project(
        bound, PROJECTS / "req_ack_bug2", oracle=oracle)
    assert receipt.status == "SHADOW_ORACLE_PASS"
    assert receipt.independent_verifier is True


def test_asset_binding_rejects_incompatible_mechanism(tmp_tehm):
    with pytest.raises(ValueError, match="mechanism family is incompatible"):
        bind_rtl_asset_to_project(
            _proposal().to_dict(), PROJECTS / "valid_ready_bug",
            expected_mechanism_family="HANDSHAKE_COMPLETION")


def test_static_validator_fails_closed_on_negative_oracle_verdict(tmp_tehm):
    source = (PROJECTS / "req_ack_bug" / "rtl" / "req_ack_fsm.v").read_text()
    receipt = validate_rtl_rewrite_asset(
        _proposal().to_dict(), source,
        verifier=lambda _source, _asset: {"verdict": "FAIL"})
    assert receipt.status == "VALIDATION_FAILED"
    assert receipt.independent_verifier is True
    assert receipt.oracle_verdict == "FAIL"


def test_asset_promotion_gate_fails_closed_on_malformed_contracts(tmp_tehm):
    from tehm.assets import (
        evaluate_asset_authority, evaluate_asset_promotion_gates,
        validate_asset_schema,
    )

    gates = {name: True for name in (
        "schema_valid", "static_valid", "independent_verifier",
        "compatibility_verified", "cross_lineage_verified",
        "regression_zero", "rollback_verified")}
    malformed = {
        "asset_id": "asset-malformed",
        "verifier_contract": "not-json",
        "provenance": "not-json",
    }
    receipt = evaluate_asset_promotion_gates(
        malformed, gates, target_scope="rtl.test")
    assert receipt.eligible is False
    assert "independent_verifier" in receipt.missing
    assert validate_asset_schema("not-a-mapping")[0] is False
    assert evaluate_asset_promotion_gates(
        "not-a-mapping", gates, target_scope="rtl.test").eligible is False
    authority = evaluate_asset_authority(
        "not-a-mapping", validation_receipts="not-iterable-mapping",
        bindings=None, rollback_receipt="not-a-mapping", target_scope="rtl.test")
    assert authority.eligible is False
    assert authority.evidence["reason"] == "asset_not_mapping"


def test_asset_registry_corruption_is_not_loaded(tmp_tehm):
    conn, _, _ = tmp_tehm
    receipt = register_asset_proposal(conn, _proposal())
    conn.execute(
        "UPDATE tehm_assets SET compatibility_json='[]' WHERE asset_id=?",
        (receipt.asset_id,))
    conn.commit()
    assert get_asset(conn, receipt.asset_id) is None


def test_asset_authority_is_content_bound_and_replayable(tmp_tehm):
    from tehm.assets import (
        promote_asset, record_asset_authority, verify_asset_authority,
    )

    conn, _, _ = tmp_tehm
    registered = register_asset_proposal(conn, _proposal())
    set_asset_status(conn, asset_id=registered.asset_id,
                     target_scope=registered.target_scope, status="shadow")
    set_asset_status(conn, asset_id=registered.asset_id,
                     target_scope=registered.target_scope, status="candidate")
    asset = get_asset(conn, registered.asset_id)
    assert asset is not None
    asset["asset_id"] = registered.asset_id
    bound_one = bind_rtl_asset_to_project(
        asset, PROJECTS / "req_ack_bug",
        expected_mechanism_family="HANDSHAKE_COMPLETION")
    bound_two = bind_rtl_asset_to_project(
        asset, PROJECTS / "req_ack_bug2",
        expected_mechanism_family="HANDSHAKE_COMPLETION")
    validation = {
        "static_valid": True, "independent_verifier": True,
        "oracle_verdict": "PASS", "regression_verdict": "PASS", "errors": [],
    }
    authority = record_asset_authority(
        conn, asset_id=registered.asset_id, target_scope=registered.target_scope,
        validation_receipts=[validation, validation],
        bindings=[bound_one, bound_two], rollback_receipt={"verified": True})
    assert authority.eligible is True
    checked = verify_asset_authority(conn, authority)
    assert checked["eligible"] is True, checked
    promoted = promote_asset(conn, authority)
    assert promoted.status == "promoted"

    tampered = authority.to_dict()
    tampered["checks"]["schema_valid"] = False
    assert verify_asset_authority(conn, tampered)["eligible"] is False
    malformed = authority.to_dict()
    malformed["checks"] = "not-a-map"
    assert verify_asset_authority(conn, malformed)["eligible"] is False
    with pytest.raises(ValueError, match="strict asset promotion"):
        set_asset_status(
            conn, asset_id=registered.asset_id,
            target_scope=registered.target_scope, status="promoted",
            strict_asset_authority=True)


def test_asset_authority_ledger_is_idempotent_and_tamper_evident(tmp_tehm):
    from tehm.assets import record_asset_authority, verify_asset_authority

    conn, _, _ = tmp_tehm
    registered = register_asset_proposal(conn, _proposal())
    asset = get_asset(conn, registered.asset_id)
    assert asset is not None
    asset["asset_id"] = registered.asset_id
    bound_one = bind_rtl_asset_to_project(
        asset, PROJECTS / "req_ack_bug",
        expected_mechanism_family="HANDSHAKE_COMPLETION")
    bound_two = bind_rtl_asset_to_project(
        asset, PROJECTS / "req_ack_bug2",
        expected_mechanism_family="HANDSHAKE_COMPLETION")
    validation = {
        "static_valid": True, "independent_verifier": True,
        "oracle_verdict": "PASS", "regression_verdict": "PASS", "errors": [],
    }
    kwargs = dict(
        asset_id=registered.asset_id, target_scope=registered.target_scope,
        validation_receipts=[
            {"receipt": validation, "split": "training", "lineage_id": "l1",
             "project": "l1"},
            {"receipt": validation, "split": "training", "lineage_id": "l2",
             "project": "l2"},
        ],
        bindings=[
            {"asset": bound_one, "split": "training", "lineage_id": "l1",
             "project": "l1"},
            {"asset": bound_two, "split": "training", "lineage_id": "l2",
             "project": "l2"},
        ],
        rollback_receipt={"verified": True},
    )
    authority = record_asset_authority(conn, **kwargs)
    row_counts = (
        conn.execute("SELECT COUNT(*) FROM tehm_asset_authority_evidence").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM tehm_asset_authority_receipts").fetchone()[0],
    )
    replay = record_asset_authority(conn, **kwargs)
    assert replay.to_dict() == authority.to_dict()
    assert row_counts == (
        conn.execute("SELECT COUNT(*) FROM tehm_asset_authority_evidence").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM tehm_asset_authority_receipts").fetchone()[0],
    )
    assert verify_asset_authority(conn, authority)["eligible"] is True

    evidence_id = conn.execute(
        "SELECT evidence_id FROM tehm_asset_authority_evidence "
        "WHERE evidence_type='asset_validation' LIMIT 1").fetchone()[0]
    conn.execute(
        "UPDATE tehm_asset_authority_evidence SET payload_json='{}' "
        "WHERE asset_id=? AND target_scope=? AND evidence_type='asset_validation' "
        "AND evidence_id=?",
        (registered.asset_id, registered.target_scope, evidence_id),
    )
    conn.commit()
    checked = verify_asset_authority(conn, authority)
    assert checked["eligible"] is False
    assert "evidence:validation:digest_mismatch" in checked["reasons"]


def test_asset_authority_evidence_is_atomic_on_late_failure(tmp_tehm):
    from tehm.assets import record_asset_authority
    import tehm.assets.authority as authority_module

    conn, _, _ = tmp_tehm
    registered = register_asset_proposal(conn, _proposal())
    asset = get_asset(conn, registered.asset_id)
    assert asset is not None
    asset["asset_id"] = registered.asset_id
    bound_one = bind_rtl_asset_to_project(
        asset, PROJECTS / "req_ack_bug",
        expected_mechanism_family="HANDSHAKE_COMPLETION")
    bound_two = bind_rtl_asset_to_project(
        asset, PROJECTS / "req_ack_bug2",
        expected_mechanism_family="HANDSHAKE_COMPLETION")
    validation = {
        "static_valid": True, "independent_verifier": True,
        "oracle_verdict": "PASS", "regression_verdict": "PASS", "errors": [],
    }
    kwargs = dict(
        asset_id=registered.asset_id, target_scope=registered.target_scope,
        validation_receipts=[
            {"receipt": validation, "split": "training", "lineage_id": "l1",
             "project": "l1"},
            {"receipt": validation, "split": "training", "lineage_id": "l2",
             "project": "l2"},
        ],
        bindings=[
            {"asset": bound_one, "split": "training", "lineage_id": "l1",
             "project": "l1"},
            {"asset": bound_two, "split": "training", "lineage_id": "l2",
             "project": "l2"},
        ],
        rollback_receipt={"verified": True},
    )
    original = authority_module._insert_evidence_row
    calls = {"count": 0}

    def fail_on_second(*args, **call_kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise ValueError("injected asset authority evidence failure")
        return original(*args, **call_kwargs)

    with patch("tehm.assets.authority._insert_evidence_row",
               side_effect=fail_on_second):
        with pytest.raises(ValueError, match="injected asset authority evidence failure"):
            record_asset_authority(conn, **kwargs)
    assert calls["count"] == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_asset_authority_evidence").fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_asset_authority_receipts").fetchone()[0] == 0


def test_gap_detector_requires_repeated_lineage_evidence(tmp_tehm):
    conn, store, _ = tmp_tehm
    oracle = IcarusOracle()
    if not oracle.available:
        pytest.skip("Icarus unavailable")
    for name in ("req_ack_bug", "req_ack_bug2"):
        capture_rtl_causal_fragment(
            conn, store, PROJECTS / name, oracle=oracle,
            campaign_id="gap-campaign")
    gaps = detect_capability_gaps(
        conn, campaign_id="gap-campaign", min_lineages=2, min_failures=99)
    assert gaps
    gap = next(item for item in gaps if item.mechanism_family == "HANDSHAKE_COMPLETION")
    assert "RTL_REWRITE_TEMPLATE" in gap.missing_asset_types
    assert len(gap.evidence_lineages) == 2
    assert "repeated_unsupported_mechanism" in gap.reason


def test_asset_registry_writers_preserve_outer_transaction(tmp_tehm):
    conn, _, _ = tmp_tehm
    proposal = _proposal()
    conn.execute(
        "INSERT INTO tehm_meta(key, value) VALUES (?, ?)",
        ("asset-caller-sentinel", "pending"),
    )
    registered = register_asset_proposal(conn, proposal)
    assert conn.in_transaction is True
    assert get_asset(conn, registered.asset_id) is not None
    assert conn.execute(
        "SELECT status FROM tehm_asset_status WHERE asset_id=? AND target_scope=?",
        (registered.asset_id, registered.target_scope)).fetchone()[0] == "draft"
    conn.rollback()
    assert conn.execute(
        "SELECT 1 FROM tehm_meta WHERE key='asset-caller-sentinel'"
    ).fetchone() is None
    assert get_asset(conn, registered.asset_id) is None
    assert conn.execute(
        "SELECT 1 FROM tehm_asset_status WHERE asset_id=? AND target_scope=?",
        (registered.asset_id, registered.target_scope)).fetchone() is None

    registered = register_asset_proposal(conn, proposal)
    conn.execute(
        "INSERT INTO tehm_meta(key, value) VALUES (?, ?)",
        ("asset-status-sentinel", "pending"),
    )
    set_asset_status(conn, asset_id=registered.asset_id,
                     target_scope=registered.target_scope, status="shadow")
    assert conn.in_transaction is True
    conn.rollback()
    assert get_asset(conn, registered.asset_id) is not None
    assert conn.execute(
        "SELECT status FROM tehm_asset_status WHERE asset_id=? AND target_scope=?",
        (registered.asset_id, registered.target_scope)).fetchone()[0] == "draft"
    assert conn.execute(
        "SELECT 1 FROM tehm_meta WHERE key='asset-status-sentinel'"
    ).fetchone() is None
