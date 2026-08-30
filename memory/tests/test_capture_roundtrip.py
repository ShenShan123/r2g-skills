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
