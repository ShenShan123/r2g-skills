"""RAM-only R5 v3 core conformance; no TRAIN or Memory authority."""
from __future__ import annotations

import argparse
import copy
import json
import sqlite3
from pathlib import Path

from contracts import MemoryQuery
from tehm import db
from tehm.assets.registry import get_asset, get_asset_status
from tehm.assets.skid_binding_v3 import with_skid_payload_binding_v3
from tehm.assets.source_selection import (
    source_contract, source_runtime_binding, verify_candidate_source_replay,
    verify_source_copy,
)
from tehm.assets.structural_binding import bind_rtl_asset_to_source
from tehm.assets.synthesis import build_rtl_asset_proposal, register_asset_proposal
from tehm.assets.validation import validate_rtl_rewrite_asset
from tehm.rtl.rtl_actions import apply_rtl_action
from tehm.rtl.skid_payload_action_v3 import (
    DOMAIN, PROFILE, apply_skid_payload_action_v3, payload_from_source_v3,
)
from tehm.retrieval.asset_selector import select_knowledge_grounded_assets
from tehm.retrieval.memory_router import route_memory
from .research_r5_skid_binding_v3 import CONTEXTS


def _reject(call) -> bool:
    try:
        call()
    except (ValueError, TypeError, KeyError):
        return True
    return False


def check(*, register_fault: Path, register_clean: Path,
          broadcast_fault: Path, broadcast_clean: Path,
          zipcpu_fault: Path, zipcpu_clean: Path) -> dict:
    sources = {
        "register": register_fault.read_text(encoding="utf-8"),
        "broadcast": broadcast_fault.read_text(encoding="utf-8"),
        "zipcpu_registered": zipcpu_fault.read_text(encoding="utf-8"),
    }
    clean = {
        "register": register_clean.read_text(encoding="utf-8"),
        "broadcast": broadcast_clean.read_text(encoding="utf-8"),
        "zipcpu_registered": zipcpu_clean.read_text(encoding="utf-8"),
    }
    train_context = CONTEXTS["register"]
    train_payload = payload_from_source_v3(sources["register"], train_context)
    proposal = build_rtl_asset_proposal(
        {}, name="r5-skid-v3-dev-conformance-only",
        transformation_family="skid_payload_restore_shadow_v3",
        action_payload_template=train_payload, compatibility_profile=PROFILE,
        verifier_obligations=("DEV target", "DEV non-target preservation"),
        creator="researcher_assisted_dev_contract_fixture")
    proposal = with_skid_payload_binding_v3(
        proposal, sources["register"], train_context)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        db.ensure_schema(conn)
        registration = register_asset_proposal(conn, proposal)
        registered = get_asset(conn, registration.asset_id)
        if registered is None:
            raise ValueError("RAM draft Asset was not retrievable")
        status = get_asset_status(conn, asset_id=registration.asset_id,
                                  target_scope=registration.target_scope)
        query = MemoryQuery(query_plan={
            "mechanism_family": "SKID_TEMP_PAYLOAD_RESTORE",
            "compatibility_profile": PROFILE, "target_scope": PROFILE})
        routing = route_memory(conn, query, no_memory_budget=1, memory_budget=1)
        cases = {
            "draft_only": registration.status == "draft" and status["status"] == "draft",
            "source_contract_v3": source_contract(registered) ==
                "rtl_skid_payload_source_binding_shadow_v3",
            "no_knowledge_persisted": db.count_rows(conn, "tehm_mechanism_knowledge") == 0,
            "router_refuses_empty_memory": routing.decision == "NO_SKILL",
            "selector_orphan_context_rejected": _reject(lambda:
                select_knowledge_grounded_assets(
                    conn, query, rtl_public_context=CONTEXTS["zipcpu_registered"])),
        }
        for shape in ("register", "broadcast", "zipcpu_registered"):
            source = sources[shape]
            context = CONTEXTS[shape]
            bound = bind_rtl_asset_to_source(
                registered, source, design_id=f"DEV_{shape}",
                public_context=context)
            payload = bound["definition"]["action"]["payload"]
            edited, receipt = apply_rtl_action(source, payload)
            validation = validate_rtl_rewrite_asset(bound, source)
            selection = select_knowledge_grounded_assets(
                conn, query, routing=routing, rtl_source_text=source,
                design_id=f"DEV_{shape}", rtl_public_context=context)
            forged = copy.deepcopy(bound)
            forged["provenance"]["binding_evidence"]["public_context"] = {
                "gold_path": "/private/answer.v"}
            altered_payload = {**payload, "gold_path": "/private/answer.v"}
            missing_template = copy.deepcopy(registered)
            del missing_template["definition"]["binding_template"]
            corrupt_template = copy.deepcopy(registered)
            corrupt_template["definition"]["binding_template"]["spec_digest"] = "sha256:bad"
            wrong_context = CONTEXTS[
                "zipcpu_registered" if shape != "zipcpu_registered" else "register"]
            forged_payload = copy.deepcopy(bound)
            forged_payload["definition"]["action"]["payload"]["source_sha256"] = "sha256:bad"
            forged_registered = {**registered, "content_digest": "sha256:bad"}
            cases.update({
                f"{shape}_registered_source_replay": verify_source_copy(bound, registered),
                f"{shape}_missing_template_rejected":
                    source_contract(missing_template) is None and _reject(lambda:
                        bind_rtl_asset_to_source(missing_template, source,
                            design_id=f"DEV_{shape}", public_context=context)),
                f"{shape}_corrupt_template_rejected": _reject(lambda:
                    bind_rtl_asset_to_source(corrupt_template, source,
                        design_id=f"DEV_{shape}", public_context=context)),
                f"{shape}_wrong_context_rejected": _reject(lambda:
                    bind_rtl_asset_to_source(registered, source,
                        design_id=f"DEV_{shape}", public_context=wrong_context)),
                f"{shape}_forged_payload_replay_rejected": not verify_source_copy(
                    forged_payload, registered),
                f"{shape}_forged_registry_digest_rejected": not verify_source_copy(
                    bound, forged_registered),
                f"{shape}_core_action_matches_clean": edited == clean[shape] and
                    receipt["rewritten"] == 1 and receipt["domain"] == DOMAIN and
                    receipt["source_binding_rederived"] is True,
                f"{shape}_target_context_rebound": payload["public_context"] == context,
                f"{shape}_static_only_not_oracle": validation.status ==
                    "SHADOW_STATIC_PASS" and validation.independent_verifier is False and
                    validation.oracle_verdict is None,
                f"{shape}_missing_context_rejected": _reject(lambda:
                    bind_rtl_asset_to_source(registered, source,
                                             design_id=f"DEV_{shape}")),
                f"{shape}_answer_field_rejected": _reject(lambda:
                    apply_skid_payload_action_v3(source, altered_payload)),
                f"{shape}_forged_source_replay_rejected": not verify_source_copy(
                    forged, registered),
                f"{shape}_stale_source_rejected": _reject(lambda: apply_rtl_action(
                    "\n" + source, payload)),
                f"{shape}_healthy_source_rejected": _reject(lambda: apply_rtl_action(
                    clean[shape], payload)),
                f"{shape}_no_knowledge_no_runtime_binding": _reject(lambda:
                    source_runtime_binding(bound, "nonexistent@1")),
                f"{shape}_no_source_replay_no_candidate": not
                    verify_candidate_source_replay(
                        type("Candidate", (), {"provenance": {},
                             "concrete_action": {"domain": DOMAIN}})(), source),
                f"{shape}_selector_refuses_empty_memory":
                    selection.receipt.decision == "NO_SKILL" and
                    "no_validated_mechanism_knowledge" in
                    selection.receipt.abstain_reasons,
                f"{shape}_source_unchanged": source == (
                    register_fault if shape == "register" else
                    broadcast_fault if shape == "broadcast" else
                    zipcpu_fault).read_text(encoding="utf-8"),
            })
        return {"valid": all(cases.values()), "case_count": len(cases),
                "failed": sorted(key for key, ok in cases.items() if not ok),
                "cases": cases, "asset_status": status["status"],
                "memory_knowledge_count": db.count_rows(conn, "tehm_mechanism_knowledge")}
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in ("register_fault", "register_clean", "broadcast_fault",
                 "broadcast_clean", "zipcpu_fault", "zipcpu_clean"):
        parser.add_argument("--" + name.replace("_", "-"), type=Path, required=True)
    args = parser.parse_args()
    result = check(**{name: getattr(args, name) for name in (
        "register_fault", "register_clean", "broadcast_fault",
        "broadcast_clean", "zipcpu_fault", "zipcpu_clean")})
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
