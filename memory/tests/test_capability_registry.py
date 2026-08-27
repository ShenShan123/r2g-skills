"""Capability registry keeps attribution evidence and policy immutable."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from tehm.capability import (
    record_capability_authority, verify_capability_authority,
    create_policy_snapshot, promote_capability, record_capability_evidence,
    register_capability, evaluate_capability_attribution,
    evaluate_capability_attribution_from_db, record_policy_load,
    evaluate_capability_retention,
    evaluate_capability_campaign,
)


def _full_attribution_and_authority(conn, capability_id, *, required_assets=(),
                                    asset_gate=True):
    baseline = create_policy_snapshot(
        conn, memory_snapshot_id="m0", promoted_rules=["r0"])
    candidate = create_policy_snapshot(
        conn, memory_snapshot_id="m1", promoted_rules=["r0", "r1"],
        promoted_assets=list(required_assets))
    record_policy_load(conn, policy_snapshot_id=candidate.policy_snapshot_id,
                       runtime_id="authority-runtime", loaded=True,
                       receipt={"execution_receipt_id": "exec-candidate"})
    attribution = evaluate_capability_attribution_from_db(
        conn, capability_id=capability_id,
        baseline_memory_digest="m0", candidate_memory_digest="m1",
        baseline_policy_snapshot_id=baseline.policy_snapshot_id,
        candidate_policy_snapshot_id=candidate.policy_snapshot_id,
        runtime_id="authority-runtime", baseline_behavior_digest="b0",
        candidate_behavior_digest="b1", target_gain=True, no_regression=True,
        heldout={"verdict": "PASS", "disjoint_lineage": True,
                 "evidence_id": "heldout-gain"},
        ablation={"gain_without_memory": False, "gain_with_memory": True})
    refs = {
        "C1": {"evidence_id": "memory-delta", "split": "ab", "verdict": "PASS"},
        "C2": {"evidence_id": "policy-delta", "split": "ab", "verdict": "PASS"},
        "C3": {"evidence_id": "runtime-load", "split": "ab", "verdict": "PASS"},
        "C4": {"evidence_id": "behavior-delta", "split": "training", "verdict": "PASS",
               "execution_receipt_id": "exec-candidate"},
        "C5": {"evidence_id": "target-gain", "split": "training", "verdict": "PASS"},
        "C6": {"evidence_id": "heldout-gain", "split": "heldout", "verdict": "PASS"},
        "C7": {"evidence_id": "no-regression", "split": "heldout", "verdict": "PASS"},
        "C8": {"evidence_id": "ablation-loss", "split": "ab", "verdict": "PASS"},
    }
    gates = {f"C{i}": True for i in range(1, 9)}
    if required_assets:
        refs["asset_authority_verified"] = {
            "evidence_id": "asset-authority", "split": "training",
            "verdict": "PASS" if asset_gate else "FAIL"}
        gates["asset_authority_verified"] = asset_gate
    authority = record_capability_authority(
        conn, capability_id=capability_id, attribution_receipt=attribution,
        evidence_refs=refs,
        candidate_policy_snapshot_id=candidate.policy_snapshot_id,
        runtime_id="authority-runtime", gates=gates)
    return attribution, authority, gates


def test_capability_and_policy_snapshot_are_content_addressed(tmp_tehm):
    conn, _, _ = tmp_tehm
    capability = register_capability(
        conn, mechanism_family="HANDSHAKE_COMPLETION",
        applicability={"compatibility_profile": "rtl.fsm.single_guard.v1"},
        required_rules=["rule-a"], obligations={"target": "PASS"},
        budget={"max_runs": 2})
    digest = record_capability_evidence(
        conn, capability_id=capability.capability_id, evidence_type="causal_path",
        evidence_id="path-1", split="training", verdict="PASS",
        lineage_id="lineage-a")
    assert digest.startswith("sha1:")
    policy = create_policy_snapshot(
        conn, memory_snapshot_id="tehm:db:test", promoted_rules=["rule-a"],
        retrieval_config={"causal": False})
    assert policy.policy_digest.startswith("sha256:")
    loaded = record_policy_load(
        conn, policy_snapshot_id=policy.policy_snapshot_id,
        runtime_id="tehm-runtime-test", loaded=True)
    assert loaded.loaded is True
    assert conn.execute("SELECT COUNT(*) FROM tehm_policy_load_receipts").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM tehm_capability_evidence").fetchone()[0] == 1
    with pytest.raises(ValueError, match="recorded authority"):
        promote_capability(conn, capability.capability_id, gates={})


def test_capability_registration_cannot_grant_authority_or_rewrite_evidence(
        tmp_tehm):
    conn, _, _ = tmp_tehm
    with pytest.raises(ValueError, match="cannot grant"):
        register_capability(
            conn, mechanism_family="M", applicability={}, status="promoted")
    capability = register_capability(
        conn, mechanism_family="M", applicability={}, status="observed_gap")
    first = record_capability_evidence(
        conn, capability_id=capability.capability_id, evidence_type="external",
        evidence_id="e1", split="training", verdict="PASS", lineage_id="l1")
    assert record_capability_evidence(
        conn, capability_id=capability.capability_id, evidence_type="external",
        evidence_id="e1", split="training", verdict="PASS", lineage_id="l1") == first
    with pytest.raises(ValueError, match="immutable"):
        record_capability_evidence(
            conn, capability_id=capability.capability_id, evidence_type="external",
            evidence_id="e1", split="training", verdict="FAIL", lineage_id="l1")


def test_capability_attribution_requires_all_eight_gates():
    receipt = evaluate_capability_attribution(
        capability_id="capability-x",
        baseline={"memory_digest": "m0", "policy_digest": "p0",
                  "behavior_digest": "b0"},
        candidate={"memory_digest": "m1", "policy_digest": "p1",
                   "behavior_digest": "b1", "target_gain": True,
                   "no_regression": True},
        runtime_receipt={"loaded": True, "policy_digest": "p1"},
        heldout={"verdict": "PASS", "disjoint_lineage": True,
                 "evidence_id": "heldout-1"},
        ablation={"gain_without_memory": False, "gain_with_memory": True})
    assert receipt.promotable is True
    assert receipt.missing_gates == ()


def test_capability_attribution_from_policy_and_runtime_receipts(tmp_tehm):
    conn, _, _ = tmp_tehm
    capability = register_capability(
        conn, mechanism_family="M", applicability={"profile": "p"})
    baseline = create_policy_snapshot(
        conn, memory_snapshot_id="m0", promoted_rules=["r0"])
    candidate = create_policy_snapshot(
        conn, memory_snapshot_id="m1", promoted_rules=["r0", "r1"])
    record_policy_load(conn, policy_snapshot_id=candidate.policy_snapshot_id,
                       runtime_id="runtime-a", loaded=True)
    receipt = evaluate_capability_attribution_from_db(
        conn, capability_id=capability.capability_id,
        baseline_memory_digest="memory-0", candidate_memory_digest="memory-1",
        baseline_policy_snapshot_id=baseline.policy_snapshot_id,
        candidate_policy_snapshot_id=candidate.policy_snapshot_id,
        runtime_id="runtime-a", baseline_behavior_digest="b0",
        candidate_behavior_digest="b1", target_gain=True, no_regression=True,
        heldout={"verdict": "PASS", "disjoint_lineage": True,
                 "evidence_id": "h1"},
        ablation={"gain_without_memory": False, "gain_with_memory": True})
    assert receipt.promotable is True


def test_capability_attribution_rejects_tampered_runtime_load_receipt(tmp_tehm):
    conn, _, _ = tmp_tehm
    capability = register_capability(
        conn, mechanism_family="M", applicability={"profile": "p"})
    baseline = create_policy_snapshot(
        conn, memory_snapshot_id="m0", promoted_rules=["r0"])
    candidate = create_policy_snapshot(
        conn, memory_snapshot_id="m1", promoted_rules=["r0", "r1"])
    load = record_policy_load(
        conn, policy_snapshot_id=candidate.policy_snapshot_id,
        runtime_id="runtime-tamper", loaded=True,
        receipt={"execution_receipt_id": "exec-1"})
    # Mutating the JSON without updating its content digest must turn C3 into
    # a failed runtime receipt.  Attribution must not trust the SQLite
    # ``loaded=1`` flag alone.
    conn.execute(
        "UPDATE tehm_policy_load_receipts SET receipt_json=? WHERE receipt_id=?",
        (json.dumps({"loaded": True}), load.receipt_id))
    conn.commit()
    receipt = evaluate_capability_attribution_from_db(
        conn, capability_id=capability.capability_id,
        baseline_memory_digest="memory-0", candidate_memory_digest="memory-1",
        baseline_policy_snapshot_id=baseline.policy_snapshot_id,
        candidate_policy_snapshot_id=candidate.policy_snapshot_id,
        runtime_id="runtime-tamper", baseline_behavior_digest="b0",
        candidate_behavior_digest="b1", target_gain=True, no_regression=True,
        heldout={"verdict": "PASS", "disjoint_lineage": True,
                 "evidence_id": "h1"},
        ablation={"gain_without_memory": False, "gain_with_memory": True})
    assert receipt.gates["C3"] is False
    assert receipt.promotable is False


def test_policy_load_replay_uses_latest_microsecond_order(tmp_tehm):
    conn, _, _ = tmp_tehm
    policy = create_policy_snapshot(
        conn, memory_snapshot_id="m0", promoted_rules=[])
    first = record_policy_load(
        conn, policy_snapshot_id=policy.policy_snapshot_id,
        runtime_id="runtime-order", loaded=True,
        receipt={"execution_receipt_id": "exec-1"})
    second = record_policy_load(
        conn, policy_snapshot_id=policy.policy_snapshot_id,
        runtime_id="runtime-order", loaded=True,
        receipt={"execution_receipt_id": "exec-2"})
    assert first.receipt_id != second.receipt_id
    row = conn.execute(
        "SELECT receipt_json FROM tehm_policy_load_receipts "
        "WHERE policy_snapshot_id=? AND runtime_id=? "
        "ORDER BY created_at DESC, receipt_id DESC LIMIT 1",
        (policy.policy_snapshot_id, "runtime-order")).fetchone()
    assert json.loads(row["receipt_json"])["receipt"]["execution_receipt_id"] == "exec-2"


def test_capability_promotion_requires_attribution_and_all_gates(tmp_tehm):
    conn, _, _ = tmp_tehm
    capability = register_capability(
        conn, mechanism_family="M", applicability={"profile": "p"},
        status="candidate")
    attribution, authority, gates = _full_attribution_and_authority(
        conn, capability.capability_id)
    promoted = promote_capability(
        conn, capability.capability_id, gates=gates,
        attribution_receipt=attribution, authority_receipt=authority)
    assert promoted.status == "promoted"
    assert conn.execute(
        "SELECT status FROM tehm_capabilities WHERE capability_id=?",
        (capability.capability_id,)).fetchone()[0] == "promoted"


def test_capability_with_asset_requires_independent_asset_gate(tmp_tehm):
    conn, _, _ = tmp_tehm
    capability = register_capability(
        conn, mechanism_family="M", applicability={"profile": "p"},
        required_assets=["asset-x"], status="candidate")
    attribution, authority, gates = _full_attribution_and_authority(
        conn, capability.capability_id, required_assets=("asset-x",),
        asset_gate=False)
    with pytest.raises(ValueError, match="authority receipt is not eligible"):
        promote_capability(conn, capability.capability_id, gates=gates,
                           attribution_receipt=attribution,
                           authority_receipt=authority)


def test_capability_authority_rechecks_immutable_evidence_rows(tmp_tehm):
    conn, _, _ = tmp_tehm
    capability = register_capability(
        conn, mechanism_family="M", applicability={}, status="candidate")
    attribution, authority, gates = _full_attribution_and_authority(
        conn, capability.capability_id)
    assert verify_capability_authority(
        conn, capability.capability_id, authority)["eligible"] is True

    conn.execute(
        "UPDATE tehm_capability_evidence SET verdict='FAIL' "
        "WHERE capability_id=? AND evidence_type='capability_gate:C5'",
        (capability.capability_id,))
    conn.commit()
    checked = verify_capability_authority(
        conn, capability.capability_id, authority)
    assert checked["eligible"] is False
    assert any("C5" in reason for reason in checked["reasons"])
    with pytest.raises(ValueError, match="authority receipt is not eligible"):
        promote_capability(conn, capability.capability_id, gates=gates,
                           attribution_receipt=attribution,
                           authority_receipt=authority)


def test_capability_authority_evidence_is_atomic_on_late_failure(tmp_tehm):
    conn, _, _ = tmp_tehm
    capability = register_capability(
        conn, mechanism_family="M", applicability={}, status="candidate")
    original = record_capability_evidence
    calls = {"count": 0}

    def fail_on_fifth(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 5:
            raise ValueError("injected authority evidence failure")
        return original(*args, **kwargs)

    with patch("tehm.capability.registry.record_capability_evidence",
               side_effect=fail_on_fifth):
        with pytest.raises(ValueError, match="injected authority evidence failure"):
            _full_attribution_and_authority(conn, capability.capability_id)
    assert calls["count"] == 5
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_capability_evidence "
        "WHERE capability_id=?", (capability.capability_id,)).fetchone()[0] == 0


def test_capability_authority_rechecks_authority_row_digest(tmp_tehm):
    conn, _, _ = tmp_tehm
    capability = register_capability(
        conn, mechanism_family="M", applicability={}, status="candidate")
    _, authority, _ = _full_attribution_and_authority(
        conn, capability.capability_id)
    conn.execute(
        "UPDATE tehm_capability_evidence SET evidence_digest='sha1:tampered' "
        "WHERE capability_id=? AND evidence_type='capability_authority' "
        "AND evidence_id=?",
        (capability.capability_id, authority.authority_receipt_id))
    conn.commit()
    checked = verify_capability_authority(
        conn, capability.capability_id, authority)
    assert checked["eligible"] is False
    assert "authority_evidence_row_digest_mismatch" in checked["reasons"]


def test_capability_authority_rechecks_runtime_execution_binding(tmp_tehm):
    conn, _, _ = tmp_tehm
    capability = register_capability(
        conn, mechanism_family="M", applicability={}, status="candidate")
    _, authority, _ = _full_attribution_and_authority(
        conn, capability.capability_id)
    load = conn.execute(
        "SELECT receipt_id, receipt_json FROM tehm_policy_load_receipts "
        "WHERE runtime_id='authority-runtime' ORDER BY created_at DESC "
        "LIMIT 1").fetchone()
    payload = json.loads(load["receipt_json"])
    payload["receipt"]["execution_receipt_id"] = "exec-tampered"
    conn.execute(
        "UPDATE tehm_policy_load_receipts SET receipt_json=? "
        "WHERE receipt_id=?", (json.dumps(payload), load["receipt_id"]))
    conn.commit()
    checked = verify_capability_authority(
        conn, capability.capability_id, authority)
    assert checked["eligible"] is False
    assert "candidate_runtime_execution_receipt_mismatch" in checked["reasons"]


def test_capability_authority_fails_closed_on_malformed_runtime_receipt(tmp_tehm):
    conn, _, _ = tmp_tehm
    capability = register_capability(
        conn, mechanism_family="M", applicability={}, status="candidate")
    _, authority, _ = _full_attribution_and_authority(
        conn, capability.capability_id)
    load = conn.execute(
        "SELECT receipt_id, receipt_json FROM tehm_policy_load_receipts "
        "WHERE runtime_id='authority-runtime' ORDER BY created_at DESC "
        "LIMIT 1").fetchone()
    payload = json.loads(load["receipt_json"])
    payload["receipt"] = ["malformed"]
    conn.execute(
        "UPDATE tehm_policy_load_receipts SET receipt_json=? "
        "WHERE receipt_id=?", (json.dumps(payload), load["receipt_id"]))
    conn.commit()
    checked = verify_capability_authority(
        conn, capability.capability_id, authority)
    assert checked["eligible"] is False
    assert "candidate_runtime_execution_receipt_mismatch" in checked["reasons"]


def test_capability_authority_rejects_wrong_gate_split(tmp_tehm):
    conn, _, _ = tmp_tehm
    capability = register_capability(
        conn, mechanism_family="M", applicability={}, status="candidate")
    baseline = create_policy_snapshot(conn, memory_snapshot_id="m0",
                                      promoted_rules=[])
    candidate = create_policy_snapshot(conn, memory_snapshot_id="m1",
                                       promoted_rules=["r1"])
    record_policy_load(conn, policy_snapshot_id=candidate.policy_snapshot_id,
                       runtime_id="authority-runtime", loaded=True)
    attribution = evaluate_capability_attribution_from_db(
        conn, capability_id=capability.capability_id,
        baseline_memory_digest="m0", candidate_memory_digest="m1",
        baseline_policy_snapshot_id=baseline.policy_snapshot_id,
        candidate_policy_snapshot_id=candidate.policy_snapshot_id,
        runtime_id="authority-runtime", baseline_behavior_digest="b0",
        candidate_behavior_digest="b1", target_gain=True, no_regression=True,
        heldout={"verdict": "PASS", "disjoint_lineage": True,
                 "evidence_id": "heldout"},
        ablation={"gain_without_memory": False, "gain_with_memory": True})
    refs = {
        f"C{i}": {"evidence_id": f"e{i}", "split": "ab",
                   "verdict": "PASS"} for i in range(1, 9)}
    refs["C5"]["split"] = "heldout"
    authority = record_capability_authority(
        conn, capability_id=capability.capability_id,
        attribution_receipt=attribution, evidence_refs=refs,
        candidate_policy_snapshot_id=candidate.policy_snapshot_id,
        runtime_id="authority-runtime")
    assert authority.eligible is False
    assert "C5:invalid_evidence_split" in authority.reasons


def test_capability_retention_replay_fails_closed():
    receipt = evaluate_capability_retention(
        capability_id="capability-x", replay_id="replay-1",
        replay={"verdict": "PASS", "disjoint_lineage": True,
                "non_target_regression_zero": True, "evidence_id": "r1"})
    assert receipt.retained is True
    failed = evaluate_capability_retention(
        capability_id="capability-x", replay_id="replay-2",
        replay={"verdict": "PASS", "disjoint_lineage": True,
                "non_target_regression_zero": False, "evidence_id": "r2"})
    assert failed.retained is False


def test_capability_campaign_binds_exact_frozen_controls(tmp_tehm):
    conn, _, _ = tmp_tehm
    capability = register_capability(
        conn, mechanism_family="M", applicability={"profile": "p"})
    baseline = create_policy_snapshot(
        conn, memory_snapshot_id="m0", promoted_rules=[])
    candidate = create_policy_snapshot(
        conn, memory_snapshot_id="m1", promoted_rules=["r1"])
    record_policy_load(conn, policy_snapshot_id=candidate.policy_snapshot_id,
                       runtime_id="runtime-campaign", loaded=True)
    controls = {"toolchain": "orfs-v1", "oracle": "route", "seed": 7}
    receipt = evaluate_capability_campaign(
        conn, capability_id=capability.capability_id,
        baseline_memory_digest="m0", candidate_memory_digest="m1",
        baseline_policy_snapshot_id=baseline.policy_snapshot_id,
        candidate_policy_snapshot_id=candidate.policy_snapshot_id,
        runtime_id="runtime-campaign", baseline_behavior_digest="b0",
        candidate_behavior_digest="b1", target_gain=True, no_regression=True,
        heldout={"verdict": "PASS", "disjoint_lineage": True,
                 "evidence_id": "h1"},
        ablation={"gain_without_memory": False, "gain_with_memory": True},
        baseline_controls=controls, candidate_controls=dict(controls))
    assert receipt.controls_match is True
    assert receipt.promotable is True
    mismatched = evaluate_capability_campaign(
        conn, capability_id=capability.capability_id,
        baseline_memory_digest="m0", candidate_memory_digest="m1",
        baseline_policy_snapshot_id=baseline.policy_snapshot_id,
        candidate_policy_snapshot_id=candidate.policy_snapshot_id,
        runtime_id="runtime-campaign", baseline_behavior_digest="b0",
        candidate_behavior_digest="b1", target_gain=True, no_regression=True,
        heldout={"verdict": "PASS", "disjoint_lineage": True,
                 "evidence_id": "h2"},
        ablation={"gain_without_memory": False, "gain_with_memory": True},
        baseline_controls=controls,
        candidate_controls={**controls, "seed": 8})
    assert mismatched.controls_match is False
    assert mismatched.promotable is False


def test_capability_and_policy_writers_preserve_outer_transaction(tmp_tehm):
    conn, _, _ = tmp_tehm
    conn.execute(
        "INSERT INTO tehm_meta(key, value) VALUES (?, ?)",
        ("capability-caller-sentinel", "pending"),
    )
    capability = register_capability(
        conn, mechanism_family="TRANSACTIONAL_CAPABILITY",
        applicability={"profile": "p"})
    record_capability_evidence(
        conn, capability_id=capability.capability_id,
        evidence_type="external", evidence_id="tx-evidence",
        split="training", verdict="PASS", lineage_id="lineage-tx")
    policy = create_policy_snapshot(
        conn, memory_snapshot_id="memory-tx", promoted_rules=["rule-tx"])
    load = record_policy_load(
        conn, policy_snapshot_id=policy.policy_snapshot_id,
        runtime_id="runtime-tx", loaded=True)
    assert conn.in_transaction is True
    assert conn.execute(
        "SELECT 1 FROM tehm_capabilities WHERE capability_id=?",
        (capability.capability_id,)).fetchone() is not None
    assert conn.execute(
        "SELECT 1 FROM tehm_policy_load_receipts WHERE receipt_id=?",
        (load.receipt_id,)).fetchone() is not None
    conn.rollback()
    assert conn.execute(
        "SELECT 1 FROM tehm_meta WHERE key='capability-caller-sentinel'"
    ).fetchone() is None
    assert conn.execute(
        "SELECT 1 FROM tehm_capabilities WHERE capability_id=?",
        (capability.capability_id,)).fetchone() is None
    assert conn.execute(
        "SELECT 1 FROM tehm_policy_snapshots WHERE policy_snapshot_id=?",
        (policy.policy_snapshot_id,)).fetchone() is None
    assert conn.execute(
        "SELECT 1 FROM tehm_policy_load_receipts WHERE receipt_id=?",
        (load.receipt_id,)).fetchone() is None


def test_capability_promotion_preserves_outer_transaction(tmp_tehm):
    conn, _, _ = tmp_tehm
    capability = register_capability(
        conn, mechanism_family="TRANSACTIONAL_PROMOTION",
        applicability={"profile": "p"}, status="candidate")
    attribution, authority, gates = _full_attribution_and_authority(
        conn, capability.capability_id)
    conn.execute(
        "INSERT INTO tehm_meta(key, value) VALUES (?, ?)",
        ("capability-promotion-sentinel", "pending"),
    )
    promoted = promote_capability(
        conn, capability.capability_id, gates=gates,
        attribution_receipt=attribution, authority_receipt=authority)
    assert promoted.status == "promoted"
    assert conn.in_transaction is True
    conn.rollback()
    assert conn.execute(
        "SELECT status FROM tehm_capabilities WHERE capability_id=?",
        (capability.capability_id,)).fetchone()[0] == "candidate"
    assert conn.execute(
        "SELECT 1 FROM tehm_meta WHERE key='capability-promotion-sentinel'"
    ).fetchone() is None
