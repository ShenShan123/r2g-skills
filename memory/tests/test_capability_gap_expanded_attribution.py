"""Reason-specific P14 units; mocked source facts are not empirical gains."""
from dataclasses import replace

import pytest

from tehm.capability import (
    create_policy_snapshot, evaluate_asset_delta, evaluate_knowledge_delta,
    record_policy_load, register_capability, record_capability_authority,
    verify_capability_authority,
)
from tehm.capability.attribution import (
    evaluate_capability_attribution, evaluate_capability_attribution_from_db,
    REASON_EXPANDED_ATTRIBUTION_VERSION,
)
from tehm.state import resolve_current_state, verify_resolution_snapshot
from test_capability_expanded_attribution import _expanded, _lineage
from test_capability_gap_source_witness import bound


@pytest.fixture
def trial(bound, tmp_tehm):
    source, _, gap = bound
    conn, _, _ = tmp_tehm
    capability = register_capability(conn, mechanism_family="UNIT_GAP", applicability={}, status="candidate")
    baseline_digest = gap.source_memory_digest
    candidate_digest = "sha256:" + "1" * 64
    baseline = create_policy_snapshot(conn, memory_snapshot_id=baseline_digest, promoted_rules=[])
    candidate = create_policy_snapshot(conn, memory_snapshot_id=candidate_digest, promoted_rules=[])
    base_load = record_policy_load(conn, policy_snapshot_id=baseline.policy_snapshot_id, runtime_id="gap-unit",
        receipt={"execution_receipt_id": "base-exec", "behavior_digest": "b0"})
    record_policy_load(conn, policy_snapshot_id=candidate.policy_snapshot_id, runtime_id="gap-unit",
        receipt={"execution_receipt_id": "candidate-exec", "behavior_digest": "b1"})
    current = resolve_current_state(conn, {"target_scope": "global"}, mode="shadow", persist=True, commit=True)
    state = verify_resolution_snapshot(conn, current.resolution_id)
    _, _, route, _, failure = _expanded()
    route = replace(route, resolved_state_id=state.resolution_id)
    manifest = {"version": "memory-delta-v1", "baseline_memory_digest": baseline_digest,
        "candidate_memory_digest": candidate_digest, "added_knowledge_ids": ["mk_selector@1"],
        "added_asset_ids": ["asset_selector"]}
    knowledge = evaluate_knowledge_delta(baseline_digest, candidate_digest, {
        "version": "knowledge-delta-v1", "baseline_memory_digest": baseline_digest,
        "candidate_memory_digest": candidate_digest, "added_knowledge_ids": ["mk_selector@1"],
        "removed_knowledge_ids": [], "revised_knowledge_ids": []})
    asset = evaluate_asset_delta(baseline_digest, candidate_digest, {
        "version": "asset-delta-v1", "baseline_memory_digest": baseline_digest,
        "candidate_memory_digest": candidate_digest, "added_asset_ids": ["asset_selector"],
        "removed_asset_ids": [], "revised_asset_ids": []})
    args = {"capability_id": capability.capability_id, "baseline_memory_digest": baseline_digest,
        "candidate_memory_digest": candidate_digest, "baseline_policy_snapshot_id": baseline.policy_snapshot_id,
        "candidate_policy_snapshot_id": candidate.policy_snapshot_id, "runtime_id": "gap-unit",
        "baseline_behavior_digest": "b0", "candidate_behavior_digest": "b1", "target_gain": True,
        "no_regression": True, "heldout": {"verdict": "PASS", "disjoint_lineage": True, "evidence_id": "h-unit"},
        "ablation": {"policy_snapshot_id": baseline.policy_snapshot_id, "policy_load_receipt_id": base_load.receipt_id,
            "runtime_receipt_id": "base-exec", "behavior_digest": "b0", "gain_with_memory": True,
            "gain_without_memory": False}, "memory_delta": manifest, "strict_memory_delta": True,
        "knowledge_delta": knowledge, "asset_delta": asset, "routing_receipts": [route],
        "state_resolution_receipt": state, "candidate_lineage": _lineage(route.routing_receipt_id),
        "evolution_source_receipt": gap, "evolution_source_conn": source, "strict_expanded": True}
    return conn, source, args, failure


def test_actual_db_replay_replaces_inapplicable_failure_placeholder(trial):
    conn, source, args, _ = trial
    before = "\n".join(conn.iterdump()), "\n".join(source.iterdump())
    result = evaluate_capability_attribution_from_db(conn, **args)
    assert result.expanded_eligible and result.promotable and result.missing_gates == ()
    expanded = result.detail["expanded_attribution"]
    assert expanded["version"] == REASON_EXPANDED_ATTRIBUTION_VERSION
    assert expanded["failure_attribution_receipts"] == []
    assert expanded["evolution_source_db_replay"]["verified"] is True
    assert before == ("\n".join(conn.iterdump()), "\n".join(source.iterdump()))


def test_pure_serialized_admission_cannot_authorize_gap(trial):
    _, _, args, _ = trial
    result = evaluate_capability_attribution(capability_id=args["capability_id"],
        baseline={"memory_digest": args["baseline_memory_digest"], "policy_digest": "p0", "behavior_digest": "b0"},
        candidate={"memory_digest": args["candidate_memory_digest"], "policy_digest": "p1", "behavior_digest": "b1",
            "target_gain": True, "no_regression": True}, runtime_receipt={"loaded": True, "policy_digest": "p1"},
        heldout=args["heldout"], ablation=args["ablation"], memory_delta=args["memory_delta"],
        **{k: args[k] for k in ("knowledge_delta", "asset_delta", "routing_receipts", "state_resolution_receipt",
            "candidate_lineage", "evolution_source_receipt", "strict_expanded")})
    assert not result.promotable and not result.expanded_eligible
    assert "P8:evolution_source_receipt_db_replay_required" in result.missing_gates


@pytest.mark.parametrize("kind", ["missing_source", "candidate_after_state", "source_modified", "receipt_rehashed",
    "admission_tamper", "fake_verified_flag", "mixed_failure", "state_tamper", "baseline_snapshot_tamper"])
def test_gap_source_or_policy_tamper_fails_closed(trial, kind):
    conn, source, args, failure = trial
    changed = dict(args)
    conn.execute("SAVEPOINT unit_negative");source.execute("SAVEPOINT unit_negative")
    try:
        if kind == "missing_source": changed["evolution_source_conn"] = None
        elif kind == "candidate_after_state": changed["evolution_source_conn"] = conn
        elif kind == "source_modified": source.execute("UPDATE tehm_dataset_membership SET split='heldout'")
        elif kind in {"receipt_rehashed", "admission_tamper", "fake_verified_flag"}:
            payload = args["evolution_source_receipt"].to_dict()
            if kind == "receipt_rehashed": payload["source_memory_digest"] = args["candidate_memory_digest"]
            elif kind == "admission_tamper": payload["admission"]["admitted"] = False
            else: payload["verified"] = True
            changed["evolution_source_receipt"] = payload
        elif kind == "mixed_failure": changed["failure_attribution_receipts"] = [failure]
        elif kind == "state_tamper":
            payload = args["state_resolution_receipt"].to_dict();payload["input_memory_digest"] = "sha256:fake"
            changed["state_resolution_receipt"] = payload
        else:
            conn.execute("UPDATE tehm_policy_snapshots SET memory_snapshot_id=? WHERE policy_snapshot_id=?",
                (args["candidate_memory_digest"], args["baseline_policy_snapshot_id"]))
        result = evaluate_capability_attribution_from_db(conn, **changed)
        assert not result.promotable and not result.expanded_eligible
    finally:
        conn.execute("ROLLBACK TO unit_negative");conn.execute("RELEASE unit_negative")
        source.execute("ROLLBACK TO unit_negative");source.execute("RELEASE unit_negative")
    assert evaluate_capability_attribution_from_db(conn, **args).expanded_eligible


@pytest.mark.parametrize("family", ["knowledge", "asset"])
@pytest.mark.parametrize("operation", ["removed", "revised", "empty"])
def test_gap_cannot_explain_non_add_delta(trial, family, operation):
    conn, _, args, _ = trial
    changed = dict(args)
    key = family + "_delta"
    payload = args[key].to_dict()
    identifier = payload["added_" + family + "_ids"][0]
    payload["added_" + family + "_ids"] = []
    if operation != "empty": payload[operation + "_" + family + "_ids"] = [identifier]
    builder = evaluate_knowledge_delta if family == "knowledge" else evaluate_asset_delta
    changed[key] = builder(args["baseline_memory_digest"], args["candidate_memory_digest"], payload)
    result = evaluate_capability_attribution_from_db(conn, **changed)
    assert not result.expanded_eligible
    assert "P8:capability_gap_" + family + "_add_required" in result.missing_gates


def test_legacy_failure_path_unchanged(trial):
    conn, _, args, failure = trial
    changed = {k: v for k, v in args.items() if not k.startswith("evolution_source")}
    missing = evaluate_capability_attribution_from_db(conn, **changed)
    assert "P8:failure_attribution_receipts_malformed" in missing.missing_gates
    changed["failure_attribution_receipts"] = [failure]
    checked = evaluate_capability_attribution_from_db(conn, **changed)
    assert checked.expanded_eligible
    assert checked.detail["expanded_attribution"]["version"] == "capability-expanded-attribution-v1"


def test_bounded_gap_attribution_cannot_enter_legacy_production_authority(trial):
    conn, _, args, _ = trial
    checked = evaluate_capability_attribution_from_db(conn, **args)
    refs = {f"C{i}": {"evidence_id": f"unit-{i}", "split": "ab" if i in {1, 2, 3, 8}
        else "training" if i in {4, 5} else "heldout", "verdict": "PASS"} for i in range(1, 9)}
    refs["C4"]["execution_receipt_id"] = "candidate-exec"
    authority = record_capability_authority(conn, capability_id=args["capability_id"],
        attribution_receipt=checked, evidence_refs=refs, candidate_policy_snapshot_id=args["candidate_policy_snapshot_id"],
        runtime_id=args["runtime_id"], gates={f"C{i}": True for i in range(1, 9)})
    assert not verify_capability_authority(conn, args["capability_id"], authority)["eligible"]
