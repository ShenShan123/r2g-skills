from __future__ import annotations

import csv
import json
import sqlite3

import pytest

from tehm import db
from tehm.physical.graph_context import load_defgraph_context
from tehm.physical.memory import PhysicalEffectMemory


FEATURES = (
    "metadata", "nodes_gate", "nodes_net", "nodes_iopin", "nodes_pin",
    "edges_gate_pin", "edges_pin_net", "edges_iopin_net",
)


def _context_project(root):
    (root / "features").mkdir(parents=True)
    (root / "reports").mkdir()
    def_path = root / "6_final.def"
    def_path.write_text("VERSION 5.8 ;\nDESIGN d ;\nEND DESIGN\n")
    metadata = {
        "graph_id": "d", "num_cells": "10", "num_nets": "12",
        "num_ios": "2", "avg_fanout": "1.5", "die_width": "100",
        "die_height": "80", "core_area": "7000", "dbu_unit": "1000",
        "CORE_UTILIZATION": "20", "tracks_per_layer": "50",
    }
    with (root / "features" / "metadata.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(metadata))
        writer.writeheader()
        writer.writerow(metadata)
    for name in FEATURES[1:]:
        (root / "features" / f"{name}.csv").write_text("graph_id,name\nd,x\n")
    stats = {"design": "d", "platform": "sky130hd", "features": {
        name: {"status": "ok", "rows": 1 if name != "metadata" else 1}
        for name in FEATURES}}
    (root / "reports" / "features_stats.json").write_text(json.dumps(stats))
    return def_path


def test_defgraph_context_is_content_addressed_and_attached(tmp_tehm, tmp_path):
    conn, _, _ = tmp_tehm
    project = tmp_path / "physical"
    def_path = _context_project(project)
    context = load_defgraph_context(project, def_path=def_path)
    assert context.status == "complete"
    assert context.dataset_tier == "research"
    assert context.signoff_health["status"] == "unknown"
    assert context.graph_features["num_cells"] == 10.0
    assert len(context.digest()) == 64

    mem = PhysicalEffectMemory(conn)
    mem.record(transition_id="t", action_domain="flow",
               transformation_family="DENSITY_RELIEF",
               before_ppa={}, after_ppa={})
    mem.attach_graph_context("t", context)
    profile = mem.predict(family="DENSITY_RELIEF",
                          graph_context_digest=context.digest())
    assert profile["support"] == 1
    assert profile["graph_context_support"] == 1
    assert profile["unique_graph_contexts"] == 1


def test_graph_context_tamper_is_rejected(tmp_tehm, tmp_path):
    conn, _, _ = tmp_tehm
    project = tmp_path / "physical"
    context = load_defgraph_context(project, def_path=_context_project(project))
    payload = context.to_dict()
    payload["graph_features"]["num_cells"] = 999
    mem = PhysicalEffectMemory(conn)
    mem.record(transition_id="t", action_domain="flow",
               transformation_family="DENSITY_RELIEF",
               before_ppa={}, after_ppa={})
    with pytest.raises(ValueError, match="digest"):
        mem.attach_graph_context("t", payload)


def test_v1_store_migrates_physical_graph_columns(tmp_path):
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE tehm_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO tehm_meta VALUES ('schema_version', 'tehm-v1');
        CREATE TABLE tehm_physical_effects (
          transition_id TEXT PRIMARY KEY, action_domain TEXT,
          transformation_family TEXT, effect_key TEXT, domain TEXT,
          before_ppa_json TEXT, after_ppa_json TEXT, deltas_json TEXT NOT NULL,
          evidence_refs_json TEXT, created_at TEXT NOT NULL);
    """)
    db.ensure_schema(conn)
    columns = {row["name"] for row in conn.execute(
        "PRAGMA table_info(tehm_physical_effects)")}
    assert {"graph_context_json", "graph_context_digest",
            "graph_extractor_version"} <= columns
    assert conn.execute(
        "SELECT value FROM tehm_meta WHERE key='schema_version'").fetchone()[0] == "tehm-v4"
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tehm_dataset_membership'"
    ).fetchone() is not None
    conn.close()
