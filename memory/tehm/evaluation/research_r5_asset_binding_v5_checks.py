"""RAM-only v5 Asset/action/source replay conformance, never TRAIN authority."""
from __future__ import annotations

import copy
import json
import sqlite3

from tehm import db
from tehm.adapters import research_r5_rtl_scoped_v2 as train
from tehm.assets.lifecycle import evaluate_asset_authority
from tehm.assets.registry import get_asset, get_asset_status, set_asset_status
from tehm.assets.skid_binding_v5 import CONTRACT, with_skid_payload_binding_v5
from tehm.assets.source_selection import (
    source_contract, verify_candidate_source_replay, verify_source_copy,
)
from tehm.assets.structural_binding import bind_rtl_asset_to_source
from tehm.assets.synthesis import build_rtl_asset_proposal, register_asset_proposal
from tehm.assets.validation import validate_rtl_rewrite_asset
from tehm.evaluation.research_r5_skid_binding_v5 import AXIS_SKID_CONTEXT
from tehm.evaluation.research_r5_skid_binding_v5_checks import FAULT, FAULT_SHA, sha
from tehm.rtl.rtl_actions import apply_rtl_action
from tehm.rtl.skid_payload_action_v5 import DOMAIN, PROFILE, payload_from_source_v5


def _reject(fn) -> bool:
    try:
        fn()
    except (ValueError, TypeError, KeyError):
        return True
    return False


def check() -> dict:
    train_rows = {case: train.verify_acquisition(train.acquisition(case, "treatment"))
                  for case in train.CASES}
    sources = {case: train._source(row) for case, row in train_rows.items()}
    contexts = {case: row["public_context"] for case, row in train_rows.items()}
    raw = FAULT.read_bytes()
    if FAULT.is_symlink() or sha(raw) != FAULT_SHA:
        raise ValueError("observed DEV input drift")
    sources["axis_skid_dev_observed"] = raw.decode("utf-8")
    contexts["axis_skid_dev_observed"] = AXIS_SKID_CONTEXT
    first = next(iter(train.CASES))
    proposal = build_rtl_asset_proposal(
        {}, name="r5-skid-v5-ram-binding-negative-only",
        transformation_family="skid_payload_restore_shadow_v5",
        action_payload_template=payload_from_source_v5(sources[first], contexts[first]),
        compatibility_profile=PROFILE,
        verifier_obligations=("TRAIN target", "TRAIN preservation"),
        creator="researcher_assisted_ram_conformance_not_authority")
    proposal = with_skid_payload_binding_v5(proposal, sources[first], contexts[first])
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        db.ensure_schema(conn)
        registration = register_asset_proposal(conn, proposal)
        asset = get_asset(conn, registration.asset_id)
        if asset is None:
            raise ValueError("RAM-only v5 Asset registration missing")
        cases = {
            "draft_only": registration.status == "draft" and
                get_asset_status(conn, asset_id=registration.asset_id,
                                 target_scope=registration.target_scope)["status"] == "draft",
            "v5_source_contract": source_contract(asset) == CONTRACT,
            "empty_memory": db.count_rows(conn, "tehm_mechanism_knowledge") == 0,
        }
        bounds, validations = [], []
        for name, source in sources.items():
            context = contexts[name]
            bound = bind_rtl_asset_to_source(
                asset, source, design_id=name, public_context=context)
            payload = bound["definition"]["action"]["payload"]
            edited, action = apply_rtl_action(source, payload)
            validation = validate_rtl_rewrite_asset(bound, source)
            forged = copy.deepcopy(bound)
            forged["provenance"]["binding_evidence"]["source"] += "\n"
            bad_template = copy.deepcopy(asset)
            bad_template["definition"]["binding_template"]["spec_digest"] = "sha256:bad"
            cases.update({
                f"{name}_source_copy": verify_source_copy(bound, asset),
                f"{name}_one_edit": edited != source and action["rewritten"] == 1 and
                    action["domain"] == DOMAIN and action["source_binding_rederived"] is True,
                f"{name}_static_only": validation.status == "SHADOW_STATIC_PASS" and
                    validation.independent_verifier is False,
                f"{name}_forged_copy_rejected": not verify_source_copy(forged, asset),
                f"{name}_template_tamper_rejected": _reject(lambda:
                    bind_rtl_asset_to_source(bad_template, source,
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
        synthetic = [{**row, "independent_verifier": True,
                      "oracle_verdict": "PASS", "regression_verdict": "PASS",
                      "errors": []} for row in validations]
        gate = evaluate_asset_authority(
            asset, validation_receipts=synthetic, bindings=bounds,
            rollback_receipt={"verified": True, "version": "forged"},
            target_scope=PROFILE, min_lineages=2)
        cases.update({
            "v5_raw_train_gate_closed": not gate.eligible and
                gate.checks["cross_lineage_verified"] is False and
                gate.checks["rollback_verified"] is False and
                gate.evidence["lineage_gate_reason"] == "audited_v5_train_bundle_missing",
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
                "cases": cases, "role": "RAM_ONLY_V5_DEV_NOT_TRAIN_OR_HELDOUT",
                "gate_missing": list(gate.missing), "memory_persistent": False}
    finally:
        conn.close()


def main() -> int:
    result = check()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
