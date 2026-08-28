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
    load_policy_snapshot, validate_policy_load_row,
    evaluate_capability_retention,
    record_capability_retention, verify_capability_retention,
    evaluate_capability_campaign,
    evaluate_memory_delta,
)


def _full_attribution_and_authority(conn, capability_id, *, required_assets=(),
                                    asset_gate=True, c6_extra=None,
                                    memory_delta=None):
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
        ablation={"gain_without_memory": False, "gain_with_memory": True},
        memory_delta=memory_delta,
        strict_memory_delta=memory_delta is not None)
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
    if c6_extra:
        refs["C6"].update(c6_extra)
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


def test_capability_evidence_replay_checks_all_immutable_fields(tmp_tehm):
    conn, _, _ = tmp_tehm
    capability = register_capability(
        conn, mechanism_family="EVIDENCE_REPLAY", applicability={})
    record_capability_evidence(
        conn, capability_id=capability.capability_id, evidence_type="external",
        evidence_id="e1", split="training", verdict="PASS", lineage_id="l1")
    conn.execute(
        "UPDATE tehm_capability_evidence SET verdict='FAIL' "
        "WHERE capability_id=? AND evidence_type='external' AND evidence_id='e1'",
        (capability.capability_id,))
    conn.commit()
    with pytest.raises(ValueError, match="immutable and conflicts"):
        record_capability_evidence(
            conn, capability_id=capability.capability_id,
            evidence_type="external", evidence_id="e1", split="training",
            verdict="PASS", lineage_id="l1")


def test_capability_registry_replay_rejects_tampered_definition(tmp_tehm):
    conn, _, _ = tmp_tehm
    capability = register_capability(
        conn, mechanism_family="IMMUTABLE_CAPABILITY",
        applicability={"profile": "p"}, required_rules=["r0"],
        obligations={"target": "PASS"})
    conn.execute(
        "UPDATE tehm_capabilities SET required_rules_json=? "
        "WHERE capability_id=?",
        (json.dumps(["tampered-rule"]), capability.capability_id))
    conn.commit()

    with pytest.raises(ValueError, match="content digest mismatch"):
        register_capability(
            conn, mechanism_family="IMMUTABLE_CAPABILITY",
            applicability={"profile": "p"}, required_rules=["r0"],
            obligations={"target": "PASS"})
    with pytest.raises(ValueError, match="content digest mismatch"):
        record_capability_evidence(
            conn, capability_id=capability.capability_id,
            evidence_type="external", evidence_id="e1", split="training",
            verdict="PASS")


def test_capability_registry_lifecycle_reader_fails_closed(tmp_tehm):
    conn, _, _ = tmp_tehm
    capability = register_capability(
        conn, mechanism_family="LIFECYCLE_REPLAY", applicability={})
    conn.execute(
        "UPDATE tehm_capabilities SET provenance_json=? "
        "WHERE capability_id=?",
        (json.dumps([]), capability.capability_id))
    conn.commit()
    with pytest.raises(ValueError, match="provenance is malformed"):
        register_capability(
            conn, mechanism_family="LIFECYCLE_REPLAY", applicability={})


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


def test_memory_delta_requires_content_bound_changed_objects():
    valid = evaluate_memory_delta(
        "sha256:baseline", "sha256:candidate", {
            "version": "memory-delta-v1",
            "baseline_memory_digest": "sha256:baseline",
            "candidate_memory_digest": "sha256:candidate",
            "added_asset_ids": ["asset_new"],
        })
    assert valid.eligible is True
    assert valid.changed_ids == ("asset_new",)
    assert valid.to_dict()["delta"]["added_asset_ids"] == ["asset_new"]

    unchanged = evaluate_memory_delta(
        "sha256:same", "sha256:same", {
            "version": "memory-delta-v1",
            "added_rule_ids": ["rule_new"],
        })
    assert unchanged.eligible is False
    assert "memory_digest_unchanged" in unchanged.reasons

    malformed = evaluate_memory_delta(
        "sha256:baseline", "sha256:candidate", {
            "version": "memory-delta-v1",
            "added_rule_ids": ["rule_new", "rule_new"],
        })
    assert malformed.eligible is False
    assert "added_rule_ids:duplicate_id" in malformed.reasons

    overlap = evaluate_memory_delta(
        "sha256:baseline", "sha256:candidate", {
            "version": "memory-delta-v1",
            "baseline_memory_digest": "sha256:wrong",
            "added_rule_ids": ["rule_new"],
            "revised_rule_ids": ["rule_new"],
        })
    assert overlap.eligible is False
    assert "baseline_memory_digest_mismatch" in overlap.reasons
    assert "rule:delta_sets_overlap" in overlap.reasons


def test_strict_capability_attribution_rejects_digest_only_c1():
    receipt = evaluate_capability_attribution(
        capability_id="capability-strict-c1",
        baseline={"memory_digest": "m0", "policy_digest": "p0",
                  "behavior_digest": "b0"},
        candidate={"memory_digest": "m1", "policy_digest": "p1",
                   "behavior_digest": "b1", "target_gain": True,
                   "no_regression": True},
        runtime_receipt={"loaded": True, "policy_digest": "p1"},
        heldout={"verdict": "PASS", "disjoint_lineage": True,
                 "evidence_id": "heldout-1"},
        ablation={"gain_without_memory": False, "gain_with_memory": True},
        strict_memory_delta=True)
    assert receipt.gates["C1"] is False
    assert receipt.promotable is False
    assert receipt.detail["memory_delta"]["reasons"] == [
        "memory_delta_required"]


def test_strict_capability_attribution_binds_policy_memory_snapshots(tmp_tehm):
    conn, _, _ = tmp_tehm
    capability = register_capability(
        conn, mechanism_family="STRICT_MEMORY_BINDING", applicability={},
        status="candidate")
    baseline = create_policy_snapshot(
        conn, memory_snapshot_id="snapshot-m0", promoted_rules=[])
    candidate = create_policy_snapshot(
        conn, memory_snapshot_id="snapshot-m1", promoted_rules=["r1"])
    record_policy_load(
        conn, policy_snapshot_id=candidate.policy_snapshot_id,
        runtime_id="strict-binding-runtime", loaded=True,
        receipt={"execution_receipt_id": "exec-strict-binding"})
    receipt = evaluate_capability_attribution_from_db(
        conn, capability_id=capability.capability_id,
        # The delta itself is well-formed, but these labels are not the
        # memory states bound to the two evaluated policy snapshots.
        baseline_memory_digest="caller-m0", candidate_memory_digest="caller-m1",
        baseline_policy_snapshot_id=baseline.policy_snapshot_id,
        candidate_policy_snapshot_id=candidate.policy_snapshot_id,
        runtime_id="strict-binding-runtime", baseline_behavior_digest="b0",
        candidate_behavior_digest="b1", target_gain=True, no_regression=True,
        heldout={"verdict": "PASS", "disjoint_lineage": True,
                 "evidence_id": "h-strict-binding"},
        ablation={"gain_without_memory": False, "gain_with_memory": True},
        memory_delta={
            "version": "memory-delta-v1",
            "baseline_memory_digest": "caller-m0",
            "candidate_memory_digest": "caller-m1",
            "added_rule_ids": ["r1"],
        }, strict_memory_delta=True)
    assert receipt.gates["C1"] is False
    assert receipt.promotable is False
    binding = receipt.detail["memory_snapshot_binding"]
    assert binding["eligible"] is False
    assert binding["baseline_memory_snapshot_id"] == "snapshot-m0"
    assert binding["candidate_memory_snapshot_id"] == "snapshot-m1"
    assert "baseline_memory_snapshot_mismatch" in binding["reasons"]
    assert "candidate_memory_snapshot_mismatch" in binding["reasons"]


def test_capability_authority_replays_content_bound_memory_delta(tmp_tehm):
    conn, _, _ = tmp_tehm
    capability = register_capability(
        conn, mechanism_family="STRICT_C1", applicability={}, status="candidate")
    attribution, authority, gates = _full_attribution_and_authority(
        conn, capability.capability_id,
        memory_delta={
            "version": "memory-delta-v1",
            "baseline_memory_digest": "m0",
            "candidate_memory_digest": "m1",
            "added_rule_ids": ["r1"],
        })
    assert authority.eligible is True
    assert authority.payload["memory_delta"]["changed_ids"] == ["r1"]
    assert verify_capability_authority(
        conn, capability.capability_id, authority)["eligible"] is True

    authority.payload["memory_delta"]["delta"]["added_rule_ids"] = []
    checked = verify_capability_authority(
        conn, capability.capability_id, authority)
    assert checked["eligible"] is False
    assert "authority_receipt_digest_mismatch" in checked["reasons"]
    assert "C1:memory_delta_changed_ids_mismatch" in checked["reasons"]


def test_capability_authority_replays_policy_memory_snapshot_binding(tmp_tehm):
    conn, _, _ = tmp_tehm
    capability = register_capability(
        conn, mechanism_family="STRICT_AUTHORITY_BINDING", applicability={},
        status="candidate")
    _, authority, _ = _full_attribution_and_authority(
        conn, capability.capability_id,
        memory_delta={
            "version": "memory-delta-v1",
            "baseline_memory_digest": "m0",
            "candidate_memory_digest": "m1",
            "added_rule_ids": ["r1"],
        })
    assert authority.eligible is True
    assert verify_capability_authority(
        conn, capability.capability_id, authority)["eligible"] is True

    authority.payload["memory_snapshot_binding"]["baseline_memory_digest"] = (
        "tampered-baseline")
    checked = verify_capability_authority(
        conn, capability.capability_id, authority)
    assert checked["eligible"] is False
    assert "authority_receipt_digest_mismatch" in checked["reasons"]
    assert "C1:baseline_memory_snapshot_mismatch" in checked["reasons"]


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


def test_policy_snapshot_replay_rejects_tampered_content(tmp_tehm):
    conn, _, _ = tmp_tehm
    policy = create_policy_snapshot(
        conn, memory_snapshot_id="m0", promoted_rules=["r0"])
    conn.execute(
        "UPDATE tehm_policy_snapshots SET promoted_rules_json=? "
        "WHERE policy_snapshot_id=?",
        (json.dumps(["tampered-rule"]), policy.policy_snapshot_id))
    conn.commit()

    with pytest.raises(ValueError, match="content digest mismatch"):
        load_policy_snapshot(conn, policy.policy_snapshot_id)
    with pytest.raises(ValueError, match="content digest mismatch"):
        create_policy_snapshot(
            conn, memory_snapshot_id="m0", promoted_rules=["r0"])


def test_policy_and_load_validators_reject_weakly_typed_identity_fields(tmp_tehm):
    conn, _, _ = tmp_tehm
    policy = create_policy_snapshot(
        conn, memory_snapshot_id="typed-memory", promoted_rules=[])
    conn.execute(
        "UPDATE tehm_policy_snapshots SET memory_snapshot_id='' "
        "WHERE policy_snapshot_id=?", (policy.policy_snapshot_id,))
    conn.commit()
    with pytest.raises(ValueError, match="memory_snapshot_id"):
        load_policy_snapshot(conn, policy.policy_snapshot_id)

    policy = create_policy_snapshot(
        conn, memory_snapshot_id="typed-memory-load", promoted_rules=[])
    load = record_policy_load(
        conn, policy_snapshot_id=policy.policy_snapshot_id,
        runtime_id="typed-runtime", loaded=True)
    row = conn.execute(
        "SELECT * FROM tehm_policy_load_receipts WHERE receipt_id=?",
        (load.receipt_id,)).fetchone()
    weak_storage_row = dict(row)
    weak_storage_row["loaded"] = "false"
    with pytest.raises(ValueError, match="loaded field"):
        validate_policy_load_row(weak_storage_row)
    weak_payload_row = dict(row)
    payload = json.loads(weak_payload_row["receipt_json"])
    payload["loaded"] = "false"
    weak_payload_row["receipt_json"] = json.dumps(payload)
    with pytest.raises(ValueError, match="payload loaded field"):
        validate_policy_load_row(weak_payload_row)


def test_policy_load_replay_rejects_tampered_receipt(tmp_tehm):
    conn, _, _ = tmp_tehm
    policy = create_policy_snapshot(
        conn, memory_snapshot_id="m0", promoted_rules=["r0"])
    load = record_policy_load(
        conn, policy_snapshot_id=policy.policy_snapshot_id,
        runtime_id="runtime-tamper", loaded=True,
        receipt={"execution_receipt_id": "exec-1"})
    conn.execute(
        "UPDATE tehm_policy_load_receipts SET receipt_json=? WHERE receipt_id=?",
        (json.dumps({"loaded": True}), load.receipt_id))
    conn.commit()

    with pytest.raises(ValueError, match="receipt digest mismatch"):
        record_policy_load(
            conn, policy_snapshot_id=policy.policy_snapshot_id,
            runtime_id="runtime-tamper", loaded=True,
            receipt={"execution_receipt_id": "exec-1"})


def test_capability_authority_rejects_tampered_policy_snapshot(tmp_tehm):
    conn, _, _ = tmp_tehm
    capability = register_capability(
        conn, mechanism_family="M", applicability={}, status="candidate")
    attribution, authority, gates = _full_attribution_and_authority(
        conn, capability.capability_id)
    conn.execute(
        "UPDATE tehm_policy_snapshots SET routing_config_json=? "
        "WHERE policy_snapshot_id=?",
        (json.dumps({"tampered": True}), authority.candidate_policy_snapshot_id))
    conn.commit()

    checked = verify_capability_authority(
        conn, capability.capability_id, authority)
    assert checked["eligible"] is False
    assert "candidate_policy_snapshot_digest_mismatch" in checked["reasons"]
    with pytest.raises(ValueError, match="authority receipt is not eligible"):
        promote_capability(conn, capability.capability_id, gates=gates,
                           attribution_receipt=attribution,
                           authority_receipt=authority)


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


def test_capability_promotion_replay_is_idempotent_and_provenance_bound(
        tmp_tehm):
    conn, _, _ = tmp_tehm
    capability = register_capability(
        conn, mechanism_family="PROMOTION_REPLAY", applicability={},
        status="candidate")
    attribution, authority, gates = _full_attribution_and_authority(
        conn, capability.capability_id)
    first = promote_capability(
        conn, capability.capability_id, gates=gates,
        attribution_receipt=attribution, authority_receipt=authority)
    replay = promote_capability(
        conn, capability.capability_id, gates=gates,
        attribution_receipt=attribution, authority_receipt=authority)
    assert first == replay

    conn.execute(
        "UPDATE tehm_capabilities SET provenance_json=? "
        "WHERE capability_id=?",
        (json.dumps({"tampered": True}), capability.capability_id))
    conn.commit()
    with pytest.raises(ValueError, match="replay conflicts"):
        promote_capability(
            conn, capability.capability_id, gates=gates,
            attribution_receipt=attribution, authority_receipt=authority)


def test_capability_registration_replay_reports_persisted_status(tmp_tehm):
    conn, _, _ = tmp_tehm
    capability = register_capability(
        conn, mechanism_family="REGISTRATION_REPLAY", applicability={},
        status="candidate")
    attribution, authority, gates = _full_attribution_and_authority(
        conn, capability.capability_id)
    promote_capability(
        conn, capability.capability_id, gates=gates,
        attribution_receipt=attribution, authority_receipt=authority)
    replay = register_capability(
        conn, mechanism_family="REGISTRATION_REPLAY", applicability={},
        status="candidate")
    assert replay.capability_id == capability.capability_id
    assert replay.status == "promoted"


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


def test_capability_authority_c6_transfer_binding_is_fail_closed(tmp_tehm):
    conn, _, _ = tmp_tehm
    capability = register_capability(
        conn, mechanism_family="CAUSAL_TRANSFER_AUTHORITY",
        applicability={"profile": "p"}, status="candidate")
    _, authority, _ = _full_attribution_and_authority(
        conn, capability.capability_id,
        c6_extra={"causal_transfer_receipt_id": "missing-transfer-receipt"})
    assert authority.eligible is False
    assert "C6:causal_transfer[0]:receipt_missing" in authority.reasons
    checked = verify_capability_authority(
        conn, capability.capability_id, authority)
    assert checked["eligible"] is False
    assert "C6:causal_transfer[0]:receipt_missing" in checked["reasons"]


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


def test_capability_retention_binds_policy_runtime_and_registry_evidence(tmp_tehm):
    conn, _, _ = tmp_tehm
    # Existing v4 snapshots may predate this additive table; first use must
    # lazily create it without changing the migration version.
    conn.execute("DROP TABLE tehm_capability_retention_receipts")
    capability = register_capability(
        conn, mechanism_family="RETENTION", applicability={"profile": "p"})
    policy = create_policy_snapshot(
        conn, memory_snapshot_id="retention-memory", promoted_rules=["r1"])
    load = record_policy_load(
        conn, policy_snapshot_id=policy.policy_snapshot_id,
        runtime_id="retention-runtime", loaded=True,
        receipt={"execution_receipt_id": "retention-exec"})
    replay = {
        "verdict": "PASS", "disjoint_lineage": True,
        "non_target_regression_zero": True, "evidence_id": "retention-e1",
        "split": "heldout", "lineage_id": "heldout:retention",
        "candidate_policy_digest": policy.policy_digest,
    }
    receipt = record_capability_retention(
        conn, capability_id=capability.capability_id, replay_id="replay-e1",
        replay=replay, candidate_policy_snapshot_id=policy.policy_snapshot_id,
        runtime_id="retention-runtime", policy_load_receipt_id=load.receipt_id)
    assert receipt.retained is True
    assert receipt.retention_receipt_id.startswith("capability_retention_")
    assert conn.execute(
        "SELECT status FROM tehm_capabilities WHERE capability_id=?",
        (capability.capability_id,)).fetchone()[0] == "observed_gap"
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_capability_retention_receipts").fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_capability_evidence "
        "WHERE evidence_type='capability_retention'").fetchone()[0] == 1
    checked = verify_capability_retention(
        conn, capability.capability_id, receipt)
    assert checked["eligible"] is True
    assert checked["reasons"] == []


def test_capability_retention_rejects_tampered_receipts_and_loads(tmp_tehm):
    conn, _, _ = tmp_tehm
    capability = register_capability(
        conn, mechanism_family="RETENTION_TAMPER", applicability={"profile": "p"})
    policy = create_policy_snapshot(
        conn, memory_snapshot_id="retention-memory", promoted_rules=[])
    load = record_policy_load(
        conn, policy_snapshot_id=policy.policy_snapshot_id,
        runtime_id="retention-runtime", loaded=True)
    replay = {
        "verdict": "PASS", "disjoint_lineage": True,
        "non_target_regression_zero": True, "evidence_id": "retention-e2",
        "split": "ab", "lineage_id": "ab:retention",
    }
    receipt = record_capability_retention(
        conn, capability_id=capability.capability_id, replay_id="replay-e2",
        replay=replay, candidate_policy_snapshot_id=policy.policy_snapshot_id,
        runtime_id="retention-runtime", policy_load_receipt_id=load.receipt_id)
    tampered = receipt.to_dict()
    tampered["payload"] = {**tampered["payload"], "retained": False}
    failed = verify_capability_retention(
        conn, capability.capability_id, tampered)
    assert failed["eligible"] is False
    assert "retention_receipt_digest_mismatch" in failed["reasons"]
    conn.execute(
        "UPDATE tehm_policy_load_receipts SET receipt_json=? WHERE receipt_id=?",
        ("{}", load.receipt_id))
    failed_load = verify_capability_retention(
        conn, capability.capability_id, receipt)
    assert failed_load["eligible"] is False
    assert any(reason in failed_load["reasons"] for reason in (
        "runtime_load_receipt_digest_mismatch",
        "runtime_load_snapshot_id_mismatch",
    ))


def test_capability_retention_training_split_is_recorded_but_not_eligible(tmp_tehm):
    conn, _, _ = tmp_tehm
    capability = register_capability(
        conn, mechanism_family="RETENTION_SPLIT", applicability={"profile": "p"})
    policy = create_policy_snapshot(
        conn, memory_snapshot_id="retention-memory", promoted_rules=[])
    load = record_policy_load(
        conn, policy_snapshot_id=policy.policy_snapshot_id,
        runtime_id="retention-runtime", loaded=True)
    receipt = record_capability_retention(
        conn, capability_id=capability.capability_id, replay_id="replay-training",
        replay={"verdict": "PASS", "disjoint_lineage": True,
                "non_target_regression_zero": True, "evidence_id": "e3",
                "split": "training", "lineage_id": "train:retention"},
        candidate_policy_snapshot_id=policy.policy_snapshot_id,
        runtime_id="retention-runtime", policy_load_receipt_id=load.receipt_id)
    assert receipt.retained is False
    checked = verify_capability_retention(
        conn, capability.capability_id, receipt)
    assert checked["eligible"] is False
    assert "retention_split_must_be_heldout_or_ab" in checked["reasons"]


def test_capability_authority_can_bind_c7_to_retention_receipt(tmp_tehm):
    conn, _, _ = tmp_tehm
    capability = register_capability(
        conn, mechanism_family="RETENTION_AUTHORITY", applicability={"profile": "p"},
        status="candidate")
    baseline = create_policy_snapshot(
        conn, memory_snapshot_id="m0", promoted_rules=[])
    candidate = create_policy_snapshot(
        conn, memory_snapshot_id="m1", promoted_rules=["r1"])
    load = record_policy_load(
        conn, policy_snapshot_id=candidate.policy_snapshot_id,
        runtime_id="authority-runtime", loaded=True,
        receipt={"execution_receipt_id": "exec-retention"})
    attribution = evaluate_capability_attribution_from_db(
        conn, capability_id=capability.capability_id,
        baseline_memory_digest="m0", candidate_memory_digest="m1",
        baseline_policy_snapshot_id=baseline.policy_snapshot_id,
        candidate_policy_snapshot_id=candidate.policy_snapshot_id,
        runtime_id="authority-runtime", baseline_behavior_digest="b0",
        candidate_behavior_digest="b1", target_gain=True, no_regression=True,
        heldout={"verdict": "PASS", "disjoint_lineage": True,
                 "evidence_id": "heldout-retention"},
        ablation={"gain_without_memory": False, "gain_with_memory": True})
    retention = record_capability_retention(
        conn, capability_id=capability.capability_id, replay_id="replay-auth",
        replay={"verdict": "PASS", "disjoint_lineage": True,
                "non_target_regression_zero": True, "evidence_id": "ret-auth",
                "split": "heldout", "lineage_id": "heldout:auth",
                "candidate_policy_digest": candidate.policy_digest},
        candidate_policy_snapshot_id=candidate.policy_snapshot_id,
        runtime_id="authority-runtime", policy_load_receipt_id=load.receipt_id)
    retention_second = record_capability_retention(
        conn, capability_id=capability.capability_id, replay_id="replay-auth-second",
        replay={"verdict": "PASS", "disjoint_lineage": True,
                "non_target_regression_zero": True, "evidence_id": "ret-auth-2",
                "split": "heldout", "lineage_id": "heldout:auth-second",
                "candidate_policy_digest": candidate.policy_digest},
        candidate_policy_snapshot_id=candidate.policy_snapshot_id,
        runtime_id="authority-runtime", policy_load_receipt_id=load.receipt_id)
    refs = {
        f"C{i}": {"evidence_id": f"auth-e{i}", "split": "ab",
                   "verdict": "PASS"} for i in range(1, 9)}
    refs["C4"]["split"] = "training"
    refs["C4"]["execution_receipt_id"] = "exec-retention"
    refs["C5"]["split"] = "training"
    refs["C6"]["split"] = "heldout"
    refs["C7"].update({"split": "heldout",
                        "retention_receipt_ids": [
                            retention.retention_receipt_id,
                            retention_second.retention_receipt_id,
                        ]})
    authority = record_capability_authority(
        conn, capability_id=capability.capability_id,
        attribution_receipt=attribution, evidence_refs=refs,
        candidate_policy_snapshot_id=candidate.policy_snapshot_id,
        runtime_id="authority-runtime", gates={f"C{i}": True for i in range(1, 9)})
    assert authority.eligible is True
    assert verify_capability_authority(
        conn, capability.capability_id, authority)["eligible"] is True

    conn.execute(
        "UPDATE tehm_capability_retention_receipts SET receipt_json=? "
        "WHERE retention_receipt_id=?", ("{}", retention_second.retention_receipt_id))
    checked = verify_capability_authority(
        conn, capability.capability_id, authority)
    assert checked["eligible"] is False
    assert any(reason.startswith("C7:retention:")
               for reason in checked["reasons"])


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
