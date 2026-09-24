"""RAM-only R5 v3 lifecycle negatives; synthetic PASS fields are adversarial.

No input is TRAIN. No receipt or synthetic verdict leaves the in-memory DB.
This proves fail-closed gate mechanics, never empirical oracle validity.
"""
from __future__ import annotations

import argparse
import copy
import json
import sqlite3
from pathlib import Path

from tehm import db
from tehm.assets.authority import (
    promote_asset, record_asset_authority, verify_asset_authority,
)
from tehm.assets.lifecycle import evaluate_asset_authority
from tehm.assets.registry import get_asset, get_asset_status
from tehm.assets.skid_binding_v3 import with_skid_payload_binding_v3
from tehm.assets.structural_binding import bind_rtl_asset_to_source
from tehm.assets.synthesis import build_rtl_asset_proposal, register_asset_proposal
from tehm.assets.validation import validate_rtl_rewrite_asset
from tehm.rtl.skid_payload_action_v3 import PROFILE, payload_from_source_v3
from .research_r5_skid_binding_v3 import CONTEXTS


def _reject(call) -> bool:
    try:
        call()
    except (ValueError, TypeError, KeyError):
        return True
    return False


def check(*, register_fault: Path, broadcast_fault: Path,
          zipcpu_fault: Path) -> dict:
    paths = {"register": register_fault, "broadcast": broadcast_fault,
             "zipcpu_registered": zipcpu_fault}
    sources = {name: path.read_text(encoding="utf-8") for name, path in paths.items()}
    proposal = build_rtl_asset_proposal(
        {}, name="r5-skid-v3-ram-authority-negative-only",
        transformation_family="skid_payload_restore_shadow_v3",
        action_payload_template=payload_from_source_v3(
            sources["register"], CONTEXTS["register"]),
        compatibility_profile=PROFILE,
        verifier_obligations=("synthetic target negative", "synthetic preservation negative"),
        creator="adversarial_dev_check_not_training")
    proposal = with_skid_payload_binding_v3(
        proposal, sources["register"], CONTEXTS["register"])
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        db.ensure_schema(conn)
        registration = register_asset_proposal(conn, proposal)
        asset = get_asset(conn, registration.asset_id)
        if asset is None:
            raise ValueError("RAM Asset registration missing")
        names = ("register", "broadcast", "zipcpu_registered")
        bounds = [bind_rtl_asset_to_source(
            asset, sources[name], design_id=f"DEV_{name}",
            public_context=CONTEXTS[name]) for name in names]
        static = [validate_rtl_rewrite_asset(bounds[i], sources[name]).to_dict()
                  for i, name in enumerate(names)]
        gate = evaluate_asset_authority(
            asset, validation_receipts=static, bindings=bounds,
            rollback_receipt=None, target_scope=PROFILE, min_lineages=2)
        same_owner = evaluate_asset_authority(
            asset, validation_receipts=static[:2], bindings=bounds[:2],
            rollback_receipt=None, target_scope=PROFILE, min_lineages=2)
        forged = copy.deepcopy(bounds[1])
        forged["provenance"]["binding_evidence"]["public_context"] = {
            "gold_path": "/private/answer.v"}
        forged_gate = evaluate_asset_authority(
            asset, validation_receipts=static[:2],
            bindings=[bounds[0], forged], rollback_receipt=None,
            target_scope=PROFILE, min_lineages=2)
        missing_template = copy.deepcopy(asset)
        del missing_template["definition"]["binding_template"]
        missing_template_gate = evaluate_asset_authority(
            missing_template, validation_receipts=static[:2],
            bindings=bounds[:2], rollback_receipt=None,
            target_scope=PROFILE, min_lineages=2)

        # These are deliberately forged all-green non-lineage inputs, to test
        # that the strict RAM ledger cannot use them to bypass the v3 lineage gate.
        synthetic = [{**receipt, "independent_verifier": True,
                      "oracle_verdict": "PASS", "regression_verdict": "PASS",
                      "errors": []} for receipt in static]
        rollback = {"verified": True, "kind": "synthetic_adversarial_only"}
        adversarial_gate = evaluate_asset_authority(
            asset, validation_receipts=synthetic, bindings=bounds,
            rollback_receipt=rollback, target_scope=PROFILE, min_lineages=2)
        strict = record_asset_authority(
            conn, asset_id=registration.asset_id,
            target_scope=registration.target_scope,
            validation_receipts=[{"receipt": item, "split": "training",
                                  "lineage_id": f"synthetic_{i}"}
                                 for i, item in enumerate(synthetic)],
            bindings=[{"asset": item, "split": "training",
                       "lineage_id": f"synthetic_{i}", "source_id": f"fake_{i}"}
                      for i, item in enumerate(bounds)],
            rollback_receipt={"receipt": rollback, "split": "ab"},
            min_lineages=2)
        verified = verify_asset_authority(conn, strict)
        status = get_asset_status(
            conn, asset_id=registration.asset_id,
            target_scope=registration.target_scope)
        cases = {
            "v3_exact_source_replay_compatible": gate.checks["compatibility_verified"] is True,
            "v3_three_design_lineage_rejected": gate.checks["cross_lineage_verified"] is False,
            "same_owner_two_design_lineage_rejected": same_owner.checks[
                "cross_lineage_verified"] is False,
            "no_verified_lineages_reported": gate.evidence["lineages"] == [] and
                len(gate.evidence["design_ids_seen"]) == 3,
            "forged_bound_copy_incompatible": forged_gate.checks[
                "compatibility_verified"] is False,
            "missing_template_incompatible_and_lineage_blocked":
                missing_template_gate.checks["compatibility_verified"] is False and
                missing_template_gate.checks["cross_lineage_verified"] is False,
            "real_dev_validation_not_authority": gate.eligible is False and
                gate.checks["independent_verifier"] is False,
            "synthetic_all_other_gates_do_not_bypass_lineage":
                adversarial_gate.eligible is False and
                tuple(adversarial_gate.missing) == ("cross_lineage_verified",),
            "strict_ram_receipt_ineligible": strict.eligible is False and
                strict.checks["cross_lineage_verified"] is False,
            "strict_ram_replay_ineligible": verified["eligible"] is False and
                "authority_receipt_not_eligible" in verified["reasons"],
            "promotion_rejected": _reject(lambda: promote_asset(conn, strict)),
            "draft_status_unchanged": status["status"] == "draft",
        }
        return {"valid": all(cases.values()), "case_count": len(cases),
                "failed": sorted(name for name, ok in cases.items() if not ok),
                "cases": cases, "role": "SYNTHETIC_RAM_GATE_CHECK_NOT_TRAIN",
                "persistent_or_eligible_authority": False,
                "gate_missing": list(gate.missing),
                "adversarial_gate_missing": list(adversarial_gate.missing),
                "strict_receipt_eligible": strict.eligible,
                "strict_replay_eligible": verified["eligible"]}
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in ("register_fault", "broadcast_fault", "zipcpu_fault"):
        parser.add_argument("--" + name.replace("_", "-"),
                            type=Path, required=True)
    args = parser.parse_args()
    result = check(**{name: getattr(args, name) for name in (
        "register_fault", "broadcast_fault", "zipcpu_fault")})
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
