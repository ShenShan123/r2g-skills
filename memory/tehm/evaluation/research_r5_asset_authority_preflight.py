"""Pure R5 DEV preflight of current Asset gates; never records authority.

Inputs are already observed DEV RTL, not TRAIN evidence. The result exposes
gate behavior and must not be promoted or interpreted as Memory eligibility.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from tehm import db
from tehm.assets.lifecycle import evaluate_asset_authority
from tehm.assets.registry import get_asset
from tehm.assets.skid_binding_v3 import with_skid_payload_binding_v3
from tehm.assets.structural_binding import bind_rtl_asset_to_source
from tehm.assets.synthesis import build_rtl_asset_proposal, register_asset_proposal
from tehm.assets.validation import validate_rtl_rewrite_asset
from tehm.rtl.skid_payload_action_v3 import PROFILE, payload_from_source_v3
from .research_r5_skid_binding_v3 import CONTEXTS


def check(*, register_fault: Path, broadcast_fault: Path,
          zipcpu_fault: Path) -> dict:
    sources = {
        "register": register_fault.read_text(encoding="utf-8"),
        "broadcast": broadcast_fault.read_text(encoding="utf-8"),
        "zipcpu_registered": zipcpu_fault.read_text(encoding="utf-8"),
    }
    proposal = build_rtl_asset_proposal(
        {}, name="r5-skid-v3-dev-gate-preflight-only",
        transformation_family="skid_payload_restore_shadow_v3",
        action_payload_template=payload_from_source_v3(
            sources["register"], CONTEXTS["register"]),
        compatibility_profile=PROFILE,
        verifier_obligations=("DEV target", "DEV preservation"),
        creator="researcher_assisted_dev_preflight")
    proposal = with_skid_payload_binding_v3(
        proposal, sources["register"], CONTEXTS["register"])
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        db.ensure_schema(conn)
        registered_receipt = register_asset_proposal(conn, proposal)
        asset = get_asset(conn, registered_receipt.asset_id)
        if asset is None:
            raise ValueError("RAM Asset registration missing")
        bindings = []
        validations = []
        for shape in ("register", "broadcast", "zipcpu_registered"):
            source = sources[shape]
            bound = bind_rtl_asset_to_source(
                asset, source, design_id=f"DEV_{shape}",
                public_context=CONTEXTS[shape])
            bindings.append(bound)
            validations.append(validate_rtl_rewrite_asset(
                bound, source).to_dict())
        gate = evaluate_asset_authority(
            asset, validation_receipts=validations, bindings=bindings,
            rollback_receipt=None, target_scope=PROFILE, min_lineages=2)
        same_owner_gate = evaluate_asset_authority(
            asset, validation_receipts=validations[:2], bindings=bindings[:2],
            rollback_receipt=None, target_scope=PROFILE, min_lineages=2)
        checks = dict(gate.checks)
        observed = {
            "role": "DEV_GATE_PREFLIGHT_NOT_TRAIN",
            "authority_recorded": False,
            "asset_registration_status": registered_receipt.status,
            "asset_eligible": gate.eligible,
            "gate_checks": checks,
            "missing": list(gate.missing),
            "same_owner_two_design_lineage_gate":
                same_owner_gate.checks["cross_lineage_verified"],
            "bound_designs": [item["provenance"]["bound_design"]
                              for item in bindings],
            "provisional_owner_groups_not_verified_lineages": [
                "alexforencich", "alexforencich", "ZipCPU_pending_relation_audit"],
            "validation_statuses": [item["status"] for item in validations],
            "validation_independent_verifier": [
                item["independent_verifier"] for item in validations],
        }
        observed["lineage_gate_counts_design_ids"] = (
            checks["cross_lineage_verified"] is True)
        observed["valid"] = (
            observed["authority_recorded"] is False and
            observed["asset_registration_status"] == "draft" and
            observed["asset_eligible"] is False and
            checks["schema_valid"] is True and
            checks["static_valid"] is True and
            checks["compatibility_verified"] is False and
            observed["lineage_gate_counts_design_ids"] is True and
            observed["same_owner_two_design_lineage_gate"] is True and
            checks["independent_verifier"] is False and
            checks["regression_zero"] is False and
            checks["rollback_verified"] is False)
        return observed
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
