"""Capture roundtrip + idempotency (design doc 21.2, 27.3 H11 spirit).

Re-capturing identical evidence yields identical content-addressed IDs and no
duplicate rows. The experience graph edges are created exactly once.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from tehm.canonical.capture import ExecutionRecord, ExecutionRecordError, capture


def deepcopy_dict(obj: dict) -> dict:
    return json.loads(json.dumps(obj))


def _counts(conn):
    return {
        "states": conn.execute("SELECT COUNT(*) FROM tehm_states").fetchone()[0],
        "transitions": conn.execute("SELECT COUNT(*) FROM tehm_transitions").fetchone()[0],
        "episodes": conn.execute("SELECT COUNT(*) FROM tehm_episodes").fetchone()[0],
        "views": conn.execute("SELECT COUNT(*) FROM tehm_views").fetchone()[0],
        "edges": conn.execute("SELECT COUNT(*) FROM tehm_edges").fetchone()[0],
    }


def test_capture_roundtrip_deterministic(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    r1 = capture(conn, store, ExecutionRecord.from_dict(sample_record_dict))
    r2 = capture(conn, store, ExecutionRecord.from_dict(deepcopy_dict(sample_record_dict)))
    assert r1.transition_id == r2.transition_id
    assert r1.state_ids == r2.state_ids
    assert r1.episode_id == r2.episode_id
    assert r1.primary_effect_key == r2.primary_effect_key
    assert _counts(conn) == {"states": 2, "transitions": 1, "episodes": 1,
                             "views": 6, "edges": 3}


def test_capture_identical_ids_are_deduped(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    capture(conn, store, ExecutionRecord.from_dict(sample_record_dict))
    capture(conn, store, ExecutionRecord.from_dict(sample_record_dict))
    assert _counts(conn)["states"] == 2  # still exactly 2 states


def test_scoped_measurement_persists_and_binds_identity(tmp_tehm, sample_record_dict):
    from tehm.canonical.verifier import VerifierSnapshot
    from tehm.causal.mechanism import load_transition_facts
    from tehm.verified_execution import require_verified_execution

    conn, store, _ = tmp_tehm
    plain = deepcopy_dict(sample_record_dict)
    legacy = VerifierSnapshot.from_dict(plain["verification"]).content()
    assert "scope" not in legacy and "scoped_execution" not in legacy
    assert "scoped_execution" not in VerifierSnapshot.from_dict(plain["verification"]).to_dict()
    plain["verification"]["scoped_execution"] = None
    assert VerifierSnapshot.from_dict(plain["verification"]).content() == legacy
    plain_id = capture(conn, store, ExecutionRecord.from_dict(plain)).transition_id

    bound = deepcopy_dict(plain)
    bound["verification"].update(
        scope="flow_feasibility", oracle_type="TARGET_TEST", verdict="PASS",
        oracle_complete=True, obligation_coverage=1.0,
        scoped_execution={"contract_version": "orfs-flow-feasibility-v2",
                          "receipt_digest": "fixture-receipt", "role": "after",
                          "learner_admission": True})
    snapshot = VerifierSnapshot.from_dict(bound["verification"])
    assert VerifierSnapshot.from_oracle_result(bound["verification"]).scoped_execution == (
        snapshot.scoped_execution)
    first = capture(conn, store, ExecutionRecord.from_dict(bound))
    assert first.transition_id != plain_id
    replay = capture(conn, store, ExecutionRecord.from_dict(bound))
    assert replay.transition_id == first.transition_id
    facts = load_transition_facts(conn, first.transition_id)
    assert facts.verifier["scoped_execution"] == snapshot.scoped_execution
    with pytest.raises(ValueError, match="scoped_execution_replay_required"):
        require_verified_execution(facts)

    ids = {plain_id, first.transition_id}
    for key, value in (("scope", "full_signoff"), ("receipt_digest", "other-receipt"),
                       ("role", "before")):
        variant = deepcopy_dict(bound)
        target = (variant["verification"] if key == "scope" else
                  variant["verification"]["scoped_execution"])
        target[key] = value
        changed = capture(conn, store, ExecutionRecord.from_dict(variant))
        assert changed.transition_id not in ids
        ids.add(changed.transition_id)

    # A consumer must recompute this identity, not merely roundtrip the JSON.
    tampered = deepcopy_dict(facts.verifier)
    tampered["scoped_execution"]["receipt_digest"] = "substituted-after-capture"
    conn.execute("UPDATE tehm_transitions SET verifier_json=? WHERE transition_id=?",
                 (json.dumps(tampered), first.transition_id))
    with pytest.raises(ValueError, match="content-addressed transition_id mismatch"):
        load_transition_facts(conn, first.transition_id)


@pytest.mark.parametrize("payload", [False, 1, "approved", []])
def test_scoped_measurement_rejects_malformed_receipt(sample_record_dict, payload):
    record = deepcopy_dict(sample_record_dict)
    record["verification"]["scoped_execution"] = payload
    with pytest.raises(ValueError, match="scoped_execution must be a mapping"):
        ExecutionRecord.from_dict(record)


def test_typed_contract_verifier_is_persisted_without_changing_transition_id(
        tmp_tehm, tmp_path, sample_record_dict):
    """Contract provenance survives canonical normalization, not just a manifest."""
    conn, store, _ = tmp_tehm
    bound = deepcopy_dict(sample_record_dict)
    bound["verification"]["utility_contract"] = {
        "contract_id": "ROUTING_CAPACITY_RECOVERY_NONREGRESSION_005",
        "status": "FAIL",
        "contract_eligible": False,
        "promotion_eligible": False,
        "canonical_memory_mutation": "none",
    }
    plain_receipt = capture(conn, store, ExecutionRecord.from_dict(sample_record_dict))

    # A second isolated store represents a fresh replay of the same action;
    # utility provenance is deliberately outside VerifierSnapshot.content().
    from tehm import db
    from tehm.artifact_store import ArtifactStore
    conn2 = db.connect(tmp_path / "bound.sqlite")
    db.ensure_schema(conn2)
    store2 = ArtifactStore(tmp_path / "bound-artifacts")
    bound_receipt = capture(conn2, store2, ExecutionRecord.from_dict(bound))
    assert bound_receipt.transition_id == plain_receipt.transition_id
    persisted = json.loads(conn2.execute(
        "SELECT verifier_json FROM tehm_transitions WHERE transition_id=?",
        (bound_receipt.transition_id,)).fetchone()[0])
    assert persisted["utility_contract"]["contract_id"] == (
        "ROUTING_CAPACITY_RECOVERY_NONREGRESSION_005")


def test_capture_replay_rejects_tampered_canonical_content(tmp_tehm,
                                                           sample_record_dict):
    """A deterministic ID cannot be reused to overwrite raw content."""
    conn, store, _ = tmp_tehm
    receipt = capture(conn, store, ExecutionRecord.from_dict(sample_record_dict))
    before = _counts(conn)
    # Simulate a stale/corrupted canonical row while keeping the deterministic
    # source record unchanged.  The replay must detect the content mismatch
    # instead of silently replacing the row.
    conn.execute(
        "UPDATE tehm_transitions SET action_json='{}' WHERE transition_id=?",
        (receipt.transition_id,))
    conn.commit()
    with pytest.raises(ExecutionRecordError, match="immutable and conflicts"):
        capture(conn, store, ExecutionRecord.from_dict(sample_record_dict))
    assert _counts(conn) == before


def test_capture_replay_cannot_reset_dataset_membership(tmp_tehm,
                                                        sample_record_dict):
    """A replay cannot silently move evidence between learner roles."""
    conn, store, _ = tmp_tehm
    record = ExecutionRecord.from_dict(sample_record_dict)
    receipt = capture(conn, store, record,
                      dataset_campaign_id="role-campaign",
                      dataset_split="heldout", dataset_learner_eligible=False)
    with pytest.raises(ExecutionRecordError, match="immutable and conflicts"):
        capture(conn, store, ExecutionRecord.from_dict(sample_record_dict),
                dataset_campaign_id="role-campaign", dataset_split="training",
                dataset_learner_eligible=True)
    row = conn.execute(
        "SELECT split, learner_eligible FROM tehm_dataset_membership "
        "WHERE transition_id=? AND campaign_id='role-campaign'",
        (receipt.transition_id,),
    ).fetchone()
    assert tuple(row) == ("heldout", 0)


def test_new_action_creates_new_transition(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    capture(conn, store, ExecutionRecord.from_dict(sample_record_dict))
    variant = deepcopy_dict(sample_record_dict)
    variant["record_id"] = "sample_drc_antenna_fix_002"
    variant["action"]["transformation_family"] = "DENSITY_RELIEF"
    variant["episode"]["episode_id"] = "episode_sample_002"
    capture(conn, store, ExecutionRecord.from_dict(variant))
    assert _counts(conn)["transitions"] == 2


def test_experience_graph_edges_exactly_once(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    capture(conn, store, ExecutionRecord.from_dict(sample_record_dict))
    edge_rows = conn.execute(
        "SELECT source_id, relation_type, target_id FROM tehm_edges").fetchall()
    kinds = sorted(e["relation_type"] for e in edge_rows)
    assert kinds == ["EXECUTED_FROM", "PART_OF_EPISODE", "PRODUCED_STATE"]
    assert len(edge_rows) == len(set((e["source_id"], e["relation_type"],
                                      e["target_id"]) for e in edge_rows))


def test_capture_rolls_back_canonical_rows_when_view_materialization_fails(
        tmp_tehm, sample_record_dict):
    """Canonical evidence and typed views form one atomic capture unit."""
    conn, store, _ = tmp_tehm
    before = _counts(conn)
    with patch(
            "tehm.canonical.capture.views_materialize.materialize_all",
            side_effect=RuntimeError("injected view failure")):
        import pytest
        with pytest.raises(RuntimeError, match="injected view failure"):
            capture(conn, store, ExecutionRecord.from_dict(sample_record_dict))
    assert _counts(conn) == before
    assert not conn.in_transaction


def test_capture_rolls_back_partial_view_materialization(
        tmp_tehm, sample_record_dict):
    """A failure after the first view write cannot leak a partial view set."""
    conn, store, _ = tmp_tehm
    import tehm.views.materialize as view_materialize

    original = view_materialize.materialize_semantic

    def write_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected late view failure")

    import pytest
    with patch.object(view_materialize, "materialize_semantic",
                      side_effect=write_then_fail):
        with pytest.raises(RuntimeError, match="injected late view failure"):
            capture(conn, store, ExecutionRecord.from_dict(sample_record_dict))
    assert _counts(conn) == {
        "states": 0, "transitions": 0, "episodes": 0, "views": 0,
        "edges": 0,
    }
    assert not conn.in_transaction


def test_capture_savepoint_preserves_outer_transaction(
        tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    conn.execute("CREATE TEMP TABLE capture_outer_marker (value TEXT)")
    conn.execute("INSERT INTO capture_outer_marker VALUES ('keep')")
    assert conn.in_transaction

    capture(conn, store, ExecutionRecord.from_dict(sample_record_dict))

    assert conn.in_transaction
    assert conn.execute(
        "SELECT value FROM capture_outer_marker").fetchone()[0] == "keep"
    conn.rollback()
    assert _counts(conn) == {
        "states": 0, "transitions": 0, "episodes": 0, "views": 0,
        "edges": 0,
    }


def test_invalid_verification_rejected(sample_record_dict):
    bad = deepcopy_dict(sample_record_dict)
    bad["verification"]["verdict"] = "MAYBE"
    import pytest
    with pytest.raises(ValueError):
        ExecutionRecord.from_dict(bad)


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("action", "domain", 7),
        ("action", "transformation_family", ["not", "a", "string"]),
        ("action", "payload", ["not", "a", "mapping"]),
        ("observation_delta", "original_failure", 1),
        ("observation_delta", "created_regressions", "not-a-list"),
        ("observation_delta", "experiment_kind", 3),
        ("verification", "oracle_type", 9),
        ("verification", "scope", {"not": "a string"}),
        ("verification", "evidence_refs", "not-a-list"),
        ("verification", "tool_versions", ["not", "a", "mapping"]),
        ("__record__", "domain", 7),
        ("__record__", "record_id", 7),
    ],
)
def test_canonical_capture_rejects_malformed_typed_fields(
        sample_record_dict, section, key, value):
    """Capture must not coerce malformed evidence into canonical memory."""
    bad = deepcopy_dict(sample_record_dict)
    if section == "__record__":
        bad[key] = value
    else:
        bad[section][key] = value
    with pytest.raises(ValueError):
        ExecutionRecord.from_dict(bad)


@pytest.mark.parametrize(
    ("section", "key"),
    [
        ("action", "domain"),
        ("action", "transformation_family"),
        ("action", "payload"),
        ("observation_delta", "original_failure"),
    ],
)
def test_canonical_capture_rejects_missing_required_typed_fields(
        sample_record_dict, section, key):
    bad = deepcopy_dict(sample_record_dict)
    del bad[section][key]
    with pytest.raises(ValueError):
        ExecutionRecord.from_dict(bad)


@pytest.mark.parametrize("section", [
    "before", "action", "after", "observation_delta", "verification",
])
def test_canonical_capture_rejects_non_mapping_sections(sample_record_dict, section):
    bad = deepcopy_dict(sample_record_dict)
    bad[section] = [("not", "a mapping")]
    with pytest.raises(ValueError):
        ExecutionRecord.from_dict(bad)
