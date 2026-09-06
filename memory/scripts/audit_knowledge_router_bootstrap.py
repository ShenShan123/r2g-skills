#!/usr/bin/env python3
"""Replay a frozen training path through Knowledge authority in RAM only.

This diagnostic is not P13 evolution, P14 execution attribution, or production
promotion. Query scope is derived from the path, not from a held-out outcome.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

from contracts import MemoryQuery
from tehm import db
from tehm.causal.replication import evaluate_replicated_effect
from tehm.knowledge import (
    build_knowledge_from_path, record_knowledge_authority, register_knowledge,
    set_knowledge_status,
)
from tehm.retrieval.memory_router import route_memory


def audit(source: Path, *, source_sha256: str, path_id: str, campaign_id: str,
          flow_config: dict | None = None, flow_design_id: str | None = None) -> dict:
    source = source.resolve(strict=True)
    if any(Path(str(source) + suffix).exists() for suffix in ("-wal", "-shm")):
        raise ValueError("source must be a frozen sidecar-free snapshot")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != source_sha256:
        raise ValueError("source snapshot digest mismatch")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        original = db.connect_read_only(source)
        try:
            original.backup(conn)
        finally:
            original.close()
        conn.execute("PRAGMA foreign_keys=ON")
        replication = evaluate_replicated_effect(
            conn, path_id, campaign_id=campaign_id, persist=False)
        if not replication.eligible:
            raise ValueError(f"training replication rejected: {replication.reason}")
        claim = build_knowledge_from_path(conn, path_id)
        query = MemoryQuery(query_plan={
            "mechanism_family": claim.mechanism_family,
            "compatibility_profile": claim.compatibility_profile,
            "target_scope": "global",
        })

        def route():
            return route_memory(conn, query, no_memory_budget=1, memory_budget=1,
                                persist_state=False, commit=False).to_dict()

        before = route()
        register_knowledge(conn, claim)
        candidate = route()
        authority = record_knowledge_authority(conn, claim)
        if not authority.eligible:
            raise ValueError(f"knowledge authority rejected: {authority.reason}")
        set_knowledge_status(
            conn, knowledge_id=claim.knowledge_id, version=claim.version,
            status="validated", authority_receipt=authority,
            provenance={"purpose": "in_memory_bootstrap_diagnostic_only"})
        after = route()
        flow = None
        if flow_config is not None:
            from tehm.assets.flow_config import build_flow_asset_proposal
            from tehm.assets import register_asset_proposal, set_asset_status
            from tehm.retrieval.asset_selector import select_knowledge_grounded_assets
            from tehm.retrieval.structured_candidate import build_structured_candidate

            flow_query = MemoryQuery(query_plan={**query.query_plan,
                "flow_config": flow_config, "flow_design_id": flow_design_id})

            def flow_route():
                return route_memory(conn, flow_query, no_memory_budget=1, memory_budget=1,
                                    persist_state=False, commit=False)

            flow_before = flow_route()
            asset = register_asset_proposal(conn, build_flow_asset_proposal(
                conn, claim.object_id, campaign_id=campaign_id))
            for status in ("shadow", "candidate"):
                set_asset_status(conn, asset_id=asset.asset_id,
                                 target_scope=asset.target_scope, status=status)
            flow_after = flow_route()
            selection = select_knowledge_grounded_assets(conn, flow_query, routing=flow_after)
            structured = None
            if selection.receipt.decision == "SELECT":
                structured = build_structured_candidate(
                    flow_query, flow_after, selection, selection.metadata["runtime_binding"])
            flow = {"query_plan": flow_query.query_plan,
                    "before": flow_before.to_dict(), "after": flow_after.to_dict(),
                    "selection": selection.receipt.to_dict(),
                    "candidate": structured.to_dict() if structured else None,
                    "target_config_source": "caller_supplied_binding_probe",
                    "execution_performed": False}
        return {
            "schema": "tehm-knowledge-router-bootstrap-audit-v1",
            "source_sha256": digest, "campaign_id": campaign_id,
            "replication": replication.to_dict(), "knowledge": claim.to_dict(),
            "authority": authority.to_dict(), "query_plan": query.query_plan,
            "before": before, "candidate": candidate, "after": after,
            "claim_scope": "isolated_training_knowledge_routing_only",
            "canonical_memory_mutation": "none", "production_mutation": "none",
            "p13_evolution_established": False,
            "execution_attribution_established": False,
            "heldout_gain_established": False,
            "flow_binding_probe": flow,
        }
    finally:
        conn.close()
        if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise RuntimeError("source changed during audit")
        if any(Path(str(source) + suffix).exists() for suffix in ("-wal", "-shm")):
            raise RuntimeError("source acquired sidecars during audit")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--path-id", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--flow-config-json", type=json.loads)
    parser.add_argument("--flow-design-id")
    args = parser.parse_args()
    print(json.dumps(audit(args.source, source_sha256=args.source_sha256,
                           path_id=args.path_id, campaign_id=args.campaign_id,
                           flow_config=args.flow_config_json,
                           flow_design_id=args.flow_design_id),
                     indent=2, sort_keys=True))
