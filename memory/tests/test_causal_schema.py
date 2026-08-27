"""Schema and evidence-firewall checks for the v4 causal shadow lane."""
from __future__ import annotations

import sqlite3

from tehm import db


V4_TABLES = {
    "tehm_causal_nodes", "tehm_causal_edges", "tehm_causal_paths",
    "tehm_intervention_pairs", "tehm_memory_events", "tehm_rule_revisions",
    "tehm_assets", "tehm_asset_status", "tehm_capabilities",
    "tehm_capability_evidence", "tehm_policy_snapshots",
    "tehm_policy_load_receipts",
}


def test_fresh_schema_is_v4_and_has_shadow_tables(tmp_path):
    conn = db.connect(tmp_path / "fresh.sqlite")
    db.ensure_schema(conn)
    tables = {
        row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert V4_TABLES <= tables
    assert conn.execute(
        "SELECT value FROM tehm_meta WHERE key='schema_version'").fetchone()[0] == "tehm-v4"


def test_v3_store_migrates_forward_to_v4(tmp_path):
    conn = sqlite3.connect(tmp_path / "v3.sqlite")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE tehm_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO tehm_meta VALUES ('schema_version', 'tehm-v3');
    """)
    db.ensure_schema(conn)
    tables = {
        row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert V4_TABLES <= tables
    assert conn.execute(
        "SELECT value FROM tehm_meta WHERE key='schema_version'").fetchone()[0] == "tehm-v4"


def test_causal_evidence_and_status_checks_are_fail_closed(tmp_path):
    conn = db.connect(tmp_path / "checks.sqlite")
    db.ensure_schema(conn)
    with __import__("pytest").raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO tehm_causal_edges
               (causal_edge_id, source_node_id, relation_type, target_node_id,
                evidence_level, support_json, confidence_json, evidence_refs_json,
                campaign_id, learner_eligible, created_at)
               VALUES ('e', 's', 'CHANGES', 't', 'L9', '{}', '{}', '[\"t1\"]',
                       'live', 0, 'now')""")
    with __import__("pytest").raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO tehm_causal_paths
               (path_id, mechanism_family, compatibility_profile,
                ordered_nodes_json, ordered_edges_json, evidence_level,
                support_json, source_transitions_json, path_digest, status,
                created_at, updated_at)
               VALUES ('p', 'm', NULL, '[]', '[]', 'L1_EXECUTED_INTERVENTION',
                       '{}', '[]', 'd', 'production', 'now', 'now')""")
