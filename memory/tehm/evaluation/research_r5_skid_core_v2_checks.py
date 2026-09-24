"""RAM-only R5 v2 core conformance; deliberately no TRAIN or Memory authority."""
from __future__ import annotations

import argparse
import copy
import json
import sqlite3
from pathlib import Path

from contracts import MemoryQuery
from tehm import db
from tehm.assets.registry import get_asset, get_asset_status
from tehm.assets.skid_binding_v2 import with_skid_payload_binding_v2
from tehm.assets.source_selection import (
    source_contract, source_runtime_binding, verify_candidate_source_replay,
    verify_source_copy,
)
from tehm.assets.structural_binding import bind_rtl_asset_to_source
from tehm.assets.synthesis import build_rtl_asset_proposal, register_asset_proposal
from tehm.assets.validation import validate_rtl_rewrite_asset
from tehm.rtl.rtl_actions import apply_rtl_action
from tehm.rtl.skid_payload_action_v2 import (
    DOMAIN, PROFILE, apply_skid_payload_action_v2, payload_from_source_v2,
)
from tehm.retrieval.asset_selector import select_knowledge_grounded_assets
from tehm.retrieval.memory_router import route_memory
from .research_r5_skid_binding_v2 import CONTEXTS


def _reject(call) -> bool:
    try:
        call()
    except (ValueError, TypeError, KeyError):
        return True
    return False


def check(*, register_fault: Path, register_clean: Path,
          broadcast_fault: Path, broadcast_clean: Path) -> dict:
    train_source = register_fault.read_text(encoding="utf-8")
    train_clean = register_clean.read_text(encoding="utf-8")
    target_source = broadcast_fault.read_text(encoding="utf-8")
    target_clean = broadcast_clean.read_text(encoding="utf-8")
    train_context = CONTEXTS["register"]
    target_context = CONTEXTS["broadcast"]
    train_payload = payload_from_source_v2(train_source, train_context)
    proposal = build_rtl_asset_proposal(
        {}, name="r5-skid-v2-dev-conformance-only",
        transformation_family="skid_payload_restore_shadow_v2",
        action_payload_template=train_payload, compatibility_profile=PROFILE,
        verifier_obligations=("DEV native target", "DEV non-target preservation"),
        creator="researcher_assisted_dev_contract_fixture")
    proposal = with_skid_payload_binding_v2(proposal, train_source, train_context)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        db.ensure_schema(conn)
        registration = register_asset_proposal(conn, proposal)
        registered = get_asset(conn, registration.asset_id)
        if registered is None:
            raise ValueError("RAM draft Asset was not retrievable")
        bound = bind_rtl_asset_to_source(
            registered, target_source, design_id="DEV_broadcast",
            public_context=target_context)
        target_payload = bound["definition"]["action"]["payload"]
        edited, action_receipt = apply_rtl_action(target_source, target_payload)
        training_edited, _ = apply_rtl_action(train_source, train_payload)
        validation = validate_rtl_rewrite_asset(bound, target_source)
        status = get_asset_status(conn, asset_id=registration.asset_id,
                                  target_scope=registration.target_scope)
        forged = copy.deepcopy(bound)
        forged["provenance"]["binding_evidence"]["public_context"] = dict(train_context)
        query = MemoryQuery(query_plan={
            "mechanism_family": "SKID_TEMP_PAYLOAD_RESTORE",
            "compatibility_profile": PROFILE, "target_scope": PROFILE})
        routing = route_memory(conn, query, no_memory_budget=1, memory_budget=1)
        selection = select_knowledge_grounded_assets(
            conn, query, routing=routing, rtl_source_text=target_source,
            design_id="DEV_broadcast", rtl_public_context=target_context)

        altered_payload = {**target_payload, "gold_path": "/private/answer.v"}
        cases = {
            "draft_only": registration.status == "draft" and status["status"] == "draft",
            "source_contract_v2": source_contract(registered) ==
                "rtl_skid_payload_source_binding_shadow_v2",
            "registered_source_replay": verify_source_copy(bound, registered),
            "cross_shape_context_rebound": target_payload["public_context"] == target_context and
                train_payload["public_context"] == train_context,
            "target_core_action_matches_clean": edited == target_clean and
                action_receipt["rewritten"] == 1 and action_receipt["domain"] == DOMAIN and
                action_receipt["source_binding_rederived"] is True,
            "training_core_action_matches_clean": training_edited == train_clean,
            "static_only_not_oracle": validation.status == "SHADOW_STATIC_PASS" and
                validation.independent_verifier is False and validation.oracle_verdict is None,
            "missing_public_context_rejected": _reject(lambda: bind_rtl_asset_to_source(
                registered, target_source, design_id="DEV_broadcast")),
            "wrong_public_context_rejected": _reject(lambda: bind_rtl_asset_to_source(
                registered, target_source, design_id="DEV_broadcast",
                public_context=train_context)),
            "answer_field_rejected": _reject(lambda: apply_skid_payload_action_v2(
                target_source, altered_payload)),
            "forged_context_replay_rejected": not verify_source_copy(forged, registered),
            "stale_source_rejected": _reject(lambda: apply_rtl_action(
                "\n" + target_source, target_payload)),
            "healthy_source_rejected": _reject(lambda: apply_rtl_action(
                target_clean, target_payload)),
            "no_knowledge_no_runtime_binding": _reject(lambda: source_runtime_binding(
                bound, "nonexistent@1")),
            "no_source_replay_no_candidate": not verify_candidate_source_replay(
                type("Candidate", (), {"provenance": {},
                     "concrete_action": {"domain": DOMAIN}})(), target_source),
            "no_knowledge_persisted": db.count_rows(conn, "tehm_mechanism_knowledge") == 0,
            "router_and_selector_refuse_empty_memory":
                routing.decision == "NO_SKILL" and
                selection.receipt.decision == "NO_SKILL" and
                "no_validated_mechanism_knowledge" in selection.receipt.abstain_reasons,
            "selector_orphan_context_rejected": _reject(lambda:
                select_knowledge_grounded_assets(conn, query, rtl_public_context=target_context)),
            "source_unchanged": target_source == broadcast_fault.read_text(encoding="utf-8"),
        }
        return {"valid": all(cases.values()), "case_count": len(cases),
                "failed": sorted(key for key, ok in cases.items() if not ok),
                "cases": cases, "asset_status": status["status"],
                "static_validation_status": validation.status,
                "memory_knowledge_count": db.count_rows(conn, "tehm_mechanism_knowledge")}
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in ("register_fault", "register_clean", "broadcast_fault",
                 "broadcast_clean"):
        parser.add_argument("--" + name.replace("_", "-"), type=Path, required=True)
    args = parser.parse_args()
    result = check(**{name: getattr(args, name) for name in (
        "register_fault", "register_clean", "broadcast_fault", "broadcast_clean")})
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
