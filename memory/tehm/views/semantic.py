"""Semantic view: the design world model ``G_D`` (design doc 22.1).

v1 = RunContextGraph (flow/signoff domain). The graph itself is content-stored
as an artifact; the view payload carries the graph + its digest so the view is
self-contained and provenance-able (H2).
"""
from __future__ import annotations

import sqlite3

from tehm import SCHEMA_VERSION
from tehm.graph.local_design_graph import LocalDesignGraph
from tehm.views.base import ViewRecord, upsert_view

SEMANTIC_EXTRACTOR_VERSION = "semantic-v0.1"


def build_semantic_view(owner_type: str, owner_id: str, graph: LocalDesignGraph,
                        *, source_refs: list[str] | None = None,
                        materialized_at: str = "") -> ViewRecord:
    payload = {
        "graph": graph.to_dict(),
        "context_graph_digest": graph.digest(),
        "node_count": graph.node_count(),
        "edge_count": graph.edge_count(),
    }
    return ViewRecord(
        owner_type=owner_type,
        owner_id=owner_id,
        view_type="semantic",
        schema_version=SCHEMA_VERSION,
        extractor_version=SEMANTIC_EXTRACTOR_VERSION,
        payload=payload,
        source_refs=list(source_refs or []),
        materialized_at=materialized_at,
    )


def materialize_semantic(conn: sqlite3.Connection, owner_type: str, owner_id: str,
                         graph: LocalDesignGraph, *, source_refs: list[str] | None = None,
                         materialized_at: str = "", commit: bool = True) -> ViewRecord:
    record = build_semantic_view(owner_type, owner_id, graph,
                                 source_refs=source_refs,
                                 materialized_at=materialized_at)
    upsert_view(conn, record, commit=commit)
    return record
