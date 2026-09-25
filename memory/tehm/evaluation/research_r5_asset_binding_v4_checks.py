"""RAM-only v4 Asset binding and authority negatives, not empirical evidence.

The two known TRAIN sources and the already-observed fetch DEV source exercise
source replay. No model calls, persistent Memory, or held-out claim is made.
"""
from __future__ import annotations

import copy
import json
import sqlite3

from contracts import MemoryQuery
from tehm import db
from tehm.adapters import research_r5_rtl_scoped_v2 as train
from tehm.assets.lifecycle import evaluate_asset_authority
from tehm.assets.registry import get_asset, get_asset_status, set_asset_status
from tehm.assets.skid_binding_v4 import with_skid_payload_binding_v4
from tehm.assets.source_selection import (
    source_contract, verify_candidate_source_replay, verify_source_copy,
)
from tehm.assets.structural_binding import bind_rtl_asset_to_source
from tehm.assets.synthesis import build_rtl_asset_proposal, register_asset_proposal
from tehm.assets.validation import validate_rtl_rewrite_asset
from tehm.evaluation.research_r5_skid_binding_v4 import FETCH_CONTEXT
from tehm.evaluation.research_r5_skid_binding_v4_checks import INPUT
from tehm.retrieval.asset_selector import select_knowledge_grounded_assets
from tehm.retrieval.memory_router import route_memory
from tehm.rtl.rtl_actions import apply_rtl_action
from tehm.rtl.skid_payload_action_v4 import DOMAIN, PROFILE, payload_from_source_v4


def _reject(fn) -> bool:
    try:
        fn()
    except (ValueError, TypeError, KeyError):
        return True
    return False


def check() -> dict:
    checked = {case: train.verify_acquisition(train.acquisition(case, "treatment"))
               for case in train.CASES}
    sources = {case: train._source(item) for case, item in checked.items()}
    contexts = {case: item["public_context"] for case, item in checked.items()}
    sources["fetch_dev_observed"] = INPUT.read_text(encoding="utf-8")
    contexts["fetch_dev_observed"] = FETCH_CONTEXT
    first = next(iter(train.CASES))
    proposal = build_rtl_asset_proposal(
        {}, name="r5-skid-v4-ram-binding-negative-only",
        transformation_family="skid_payload_restore_shadow_v4",
        action_payload_template=payload_from_source_v4(sources[first], contexts[first]),
        compatibility_profile=PROFILE,
        verifier_obligations=("TRAIN target", "TRAIN preservation"),
        creator="researcher_assisted_ram_conformance_not_authority")
    proposal = with_skid_payload_binding_v4(proposal, sources[first], contexts[first])
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        db.ensure_schema(conn)
        registration = register_asset_proposal(conn, proposal)
        asset = get_asset(conn, registration.asset_id)
        if asset is None:
            raise ValueError("v4 RAM Asset registration missing")
        query = MemoryQuery(query_plan={
            "mechanism_family": "SKID_TEMP_PAYLOAD_RESTORE",
            "compatibility_profile": PROFILE, "target_scope": PROFILE})
        routing = route_memory(conn, query, no_memory_budget=1, memory_budget=1)
        cases = {
            "draft_only": registration.status == "draft" and
                get_asset_status(conn, asset_id=registration.asset_id,
                                 target_scope=registration.target_scope)["status"] == "draft",
            "v4_source_contract": source_contract(asset) ==
                "rtl_skid_payload_source_binding_shadow_v4",
            "empty_memory_no_skill": routing.decision == "NO_SKILL" and
                db.count_rows(conn, "tehm_mechanism_knowledge") == 0,
            "orphan_context_rejected": _reject(lambda:
                select_knowledge_grounded_assets(
                    conn, query, rtl_public_context=FETCH_CONTEXT)),
        }
        bounds, validations = [], []
        for name, source in sources.items():
            context = contexts[name]
            bound = bind_rtl_asset_to_source(
                asset, source, design_id=name, public_context=context)
            payload = bound["definition"]["action"]["payload"]
            edited, action_receipt = apply_rtl_action(source, payload)
            validation = validate_rtl_rewrite_asset(bound, source)
            forged = copy.deepcopy(bound)
            forged["provenance"]["binding_evidence"]["source"] += "\n"
            corrupt = copy.deepcopy(asset)
            corrupt["definition"]["binding_template"]["spec_digest"] = "sha256:bad"
            cases.update({
                f"{name}_exact_source_copy": verify_source_copy(bound, asset),
                f"{name}_action_is_one_rederived_edit": edited != source and
                    action_receipt["domain"] == DOMAIN and
                    action_receipt["rewritten"] == 1 and
                    action_receipt["source_binding_rederived"] is True,
                f"{name}_static_not_oracle": validation.status == "SHADOW_STATIC_PASS" and
                    validation.independent_verifier is False,
                f"{name}_forged_copy_rejected": not verify_source_copy(forged, asset),
                f"{name}_corrupt_template_rejected": _reject(lambda:
                    bind_rtl_asset_to_source(corrupt, source,
                        design_id=name, public_context=context)),
                f"{name}_missing_context_rejected": _reject(lambda:
                    bind_rtl_asset_to_source(asset, source, design_id=name)),
                f"{name}_stale_source_rejected": _reject(lambda:
                    apply_rtl_action(source + "\n", payload)),
                f"{name}_proofless_candidate_rejected": not verify_candidate_source_replay(
                    type("Candidate", (), {"provenance": {},
                         "concrete_action": {"domain": DOMAIN}})(), source),
            })
            bounds.append(bound)
            validations.append(validation.to_dict())
        synthetic = [{**item, "independent_verifier": True,
                      "oracle_verdict": "PASS", "regression_verdict": "PASS",
                      "errors": []} for item in validations]
        gate = evaluate_asset_authority(
            asset, validation_receipts=synthetic, bindings=bounds,
            rollback_receipt={"verified": True, "version": "forged"},
            target_scope=PROFILE, min_lineages=2)
        cases.update({
            "three_design_ids_not_lineages": gate.evidence["lineages"] == [] and
                len(gate.evidence["design_ids_seen"]) == 3 and
                gate.checks["cross_lineage_verified"] is False,
            "synthetic_rollback_not_authority": gate.eligible is False and
                gate.checks["rollback_verified"] is False,
            "source_replay_compatible": gate.checks["compatibility_verified"] is True,
            "legacy_promotion_blocked": _reject(lambda: set_asset_status(
                conn, asset_id=registration.asset_id,
                target_scope=registration.target_scope, status="promoted",
                gates={name: True for name in gate.checks})),
            "draft_status_unchanged": get_asset_status(
                conn, asset_id=registration.asset_id,
                target_scope=registration.target_scope)["status"] == "draft",
        })
        return {"valid": all(cases.values()), "case_count": len(cases),
                "failed": sorted(key for key, ok in cases.items() if not ok),
                "cases": cases, "role": "RAM_ONLY_NOT_TRAIN_OR_HELDOUT",
                "gate_missing": list(gate.missing), "memory_persistent": False}
    finally:
        conn.close()


def main() -> int:
    result = check()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
