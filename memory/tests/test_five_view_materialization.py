"""Five-view materialization (design doc 3.2, 19.3, 22; test list 27.1).

Every episode must materialize the implemented views (semantic, diagnostic,
episodic, procedural); parametric is explicitly NOT_IMPLEMENTED (22.5). Extractor
versions are stamped, and re-materialization is idempotent (exact replay).
"""
from __future__ import annotations

import pytest

from tehm.canonical.capture import ExecutionRecord, capture
from tehm.db import read_json
from tehm.views import parametric_stub


def _view_kinds(conn) -> set[str]:
    return {r["view_type"] for r in conn.execute(
        "SELECT DISTINCT view_type FROM tehm_views").fetchall()}


def test_capture_materializes_implemented_views(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    capture(conn, store, ExecutionRecord.from_dict(sample_record_dict))
    kinds = _view_kinds(conn)
    assert {"semantic", "diagnostic", "episodic", "procedural"} <= kinds
    assert "parametric" not in kinds  # never fabricated


def test_view_extractor_versions_stamped(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    capture(conn, store, ExecutionRecord.from_dict(sample_record_dict))
    rows = conn.execute("SELECT view_type, extractor_version FROM tehm_views").fetchall()
    assert rows
    for row in rows:
        assert row["extractor_version"], "every view must carry extractor_version (H2)"
    expected = {
        "semantic": "semantic-v0.1",
        "diagnostic": "diagnostic-v0.1",
        "episodic": "episodic-v0.1",
        "procedural": "procedural-instance-v0.1",
    }
    for row in rows:
        assert row["extractor_version"] == expected[row["view_type"]], row


def test_parametric_stub_refuses_to_fabricate():
    assert parametric_stub.PARAMETRIC_VIEW_STATUS == "NOT_IMPLEMENTED"
    with pytest.raises(NotImplementedError):
        parametric_stub.build_parametric_view("state", "state_x")


def test_rematerialization_idempotent(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    capture(conn, store, ExecutionRecord.from_dict(sample_record_dict))
    before = [tuple(r) for r in conn.execute(
        "SELECT owner_type, owner_id, view_type, payload_digest FROM tehm_views")]

    # Re-running the same capture must not change any view digest or add rows.
    capture(conn, store, ExecutionRecord.from_dict(sample_record_dict))
    after = [tuple(r) for r in conn.execute(
        "SELECT owner_type, owner_id, view_type, payload_digest FROM tehm_views")]
    assert sorted(before) == sorted(after)


def test_view_replay_conflict_is_rejected(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    capture(conn, store, ExecutionRecord.from_dict(sample_record_dict))
    row = conn.execute(
        "SELECT owner_type, owner_id, view_type, schema_version, "
        "extractor_version, payload_json, source_refs_json "
        "FROM tehm_views LIMIT 1").fetchone()

    from tehm.views import ViewRecord, upsert_view
    record = ViewRecord(
        owner_type=row["owner_type"], owner_id=row["owner_id"],
        view_type=row["view_type"], schema_version=row["schema_version"],
        extractor_version=row["extractor_version"],
        payload={**read_json(row["payload_json"]), "tampered": True},
        source_refs=read_json(row["source_refs_json"]),
        materialized_at="replay")
    with pytest.raises(ValueError, match="view replay conflicts"):
        upsert_view(conn, record)
    stored = conn.execute(
        "SELECT payload_json FROM tehm_views WHERE owner_type=? AND owner_id=? "
        "AND view_type=? AND schema_version=? AND extractor_version=?",
        tuple(row[key] for key in (
            "owner_type", "owner_id", "view_type", "schema_version",
            "extractor_version"))).fetchone()
    assert "tampered" not in stored["payload_json"]


def test_semantic_view_carries_graph_and_digest(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    capture(conn, store, ExecutionRecord.from_dict(sample_record_dict))
    from tehm.db import read_json
    row = conn.execute(
        "SELECT payload_json FROM tehm_views WHERE view_type='semantic' LIMIT 1").fetchone()
    payload = read_json(row["payload_json"])
    assert payload["context_graph_digest"].startswith("ctx_")
    assert payload["node_count"] > 0
    assert payload["edge_count"] > 0
