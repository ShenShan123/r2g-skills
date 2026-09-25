"""RAM-only positive and adversarial strict v4 TRAIN Asset authority checks.

This does not promote a production Asset, construct M+, or run transfer.
"""
from __future__ import annotations

import argparse
import copy
import json
import sqlite3
from pathlib import Path

from tehm import db
from tehm.adapters import research_r5_rtl_scoped_v2 as train
from tehm.assets.authority import record_asset_authority, verify_asset_authority
from tehm.assets.lifecycle import evaluate_asset_authority
from tehm.assets.r5_train_evidence_v4 import (
    CASE_ORDER, expected_rollback_binding, verify_train_row_metadata,
)
from tehm.assets.registry import get_asset, get_asset_status, set_asset_status
from tehm.assets.skid_binding_v4 import with_skid_payload_binding_v4
from tehm.assets.structural_binding import bind_rtl_asset_to_source
from tehm.assets.synthesis import build_rtl_asset_proposal, register_asset_proposal
from tehm.assets.validation import validate_rtl_rewrite_asset
from tehm.evaluation.research_r5_train_asset_preflight_v2 import _oracle
from tehm.rtl.skid_payload_action_v4 import PROFILE, payload_from_source_v4


ROLE = "RAM_ONLY_R5_GEN2_TRAIN_ASSET_AUTHORITY_NOT_M_PLUS"


def check() -> dict:
    checked = {case: train.verify_acquisition(train.acquisition(case, "treatment"))
               for case in CASE_ORDER}
    records = {case: train.build_record(train.acquisition(case, "treatment"))
               for case in CASE_ORDER}
    first = checked[CASE_ORDER[0]]
    source = train._source(first)
    proposal = build_rtl_asset_proposal(
        {}, name="r5-skid-v4-train-validation-draft",
        transformation_family="skid_payload_restore_shadow_v4",
        action_payload_template=payload_from_source_v4(source, first["public_context"]),
        compatibility_profile=PROFILE,
        verifier_obligations=("TRAIN target", "TRAIN preservation"),
        creator="researcher_assisted_reused_dev_train_generation_2")
    proposal = with_skid_payload_binding_v4(proposal, source, first["public_context"])
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        db.ensure_schema(conn)
        registered = register_asset_proposal(conn, proposal)
        conn.execute("UPDATE tehm_assets SET created_at=? WHERE asset_id=?",
                     ("2000-01-01T00:00:00+00:00", registered.asset_id))
        asset = get_asset(conn, registered.asset_id)
        if asset is None:
            raise ValueError("v4 TRAIN Asset missing from RAM registry")
        bounds, validations, binding_rows, validation_rows = [], [], [], []
        for case in CASE_ORDER:
            item = checked[case]
            before = train._source(item)
            bound = bind_rtl_asset_to_source(
                asset, before, design_id=item["design"],
                public_context=item["public_context"])
            validation = validate_rtl_rewrite_asset(
                bound, before,
                verifier=_oracle(item, records[case], "target"),
                regression_verifier=_oracle(item, records[case], "preservation")).to_dict()
            metadata = {"split": "training", "lineage_id": item["repository"],
                        "source_id": case}
            bounds.append(bound)
            validations.append(validation)
            binding_rows.append({"asset": bound, **metadata})
            validation_rows.append({"receipt": validation, **metadata})
        rollback = expected_rollback_binding()
        pure = evaluate_asset_authority(
            asset, validation_receipts=validations, bindings=bounds,
            rollback_receipt=rollback, target_scope=PROFILE, min_lineages=2)
        strict = record_asset_authority(
            conn, asset_id=registered.asset_id, target_scope=registered.target_scope,
            validation_receipts=validation_rows, bindings=binding_rows,
            rollback_receipt={"receipt": rollback, "split": "ab",
                              "source_id": "asset-rollback-v4-r1"}, min_lineages=2)
        cold = verify_asset_authority(conn, strict)
        forged_rollback = {**rollback, "receipt_digest": "sha256:forged"}
        bad_rollback = evaluate_asset_authority(
            asset, validation_receipts=validations, bindings=bounds,
            rollback_receipt=forged_rollback, target_scope=PROFILE, min_lineages=2)
        bad_rows = copy.deepcopy(validation_rows)
        bad_rows[1]["lineage_id"] = "forged_third_lineage"
        bad_strict = record_asset_authority(
            conn, asset_id=registered.asset_id, target_scope=registered.target_scope,
            validation_receipts=bad_rows, bindings=binding_rows,
            rollback_receipt={"receipt": rollback, "split": "ab",
                              "source_id": "asset-rollback-v4-r1"}, min_lineages=2)
        bad_cold = verify_asset_authority(conn, bad_strict)
        wrong_scope = evaluate_asset_authority(
            asset, validation_receipts=validations, bindings=bounds,
            rollback_receipt=rollback, target_scope=PROFILE + ".other", min_lineages=2)
        conn.execute("SAVEPOINT v4_non_strict_negative")
        try:
            set_asset_status(conn, asset_id=registered.asset_id,
                             target_scope=registered.target_scope,
                             status="shadow", commit=False)
            set_asset_status(conn, asset_id=registered.asset_id,
                             target_scope=registered.target_scope,
                             status="candidate", commit=False)
            try:
                set_asset_status(conn, asset_id=registered.asset_id,
                                 target_scope=registered.target_scope,
                                 status="promoted", gates={key: True for key in pure.checks},
                                 strict_asset_authority=False, commit=False)
                non_strict_rejected = False
            except ValueError as exc:
                non_strict_rejected = "strict raw-replayed authority" in str(exc)
        finally:
            conn.execute("ROLLBACK TO SAVEPOINT v4_non_strict_negative")
            conn.execute("RELEASE SAVEPOINT v4_non_strict_negative")
        cases = {
            "two_audited_train_lineages": pure.evidence["lineages"] == [
                "ZipCPU/wb2axip", "alexforencich/verilog-axis"],
            "pure_v4_train_gates_eligible": pure.eligible is True and not pure.missing,
            "strict_row_metadata_exact": verify_train_row_metadata(
                validation_rows, binding_rows),
            "strict_ram_ledger_eligible": strict.eligible is True and cold["eligible"] is True,
            "forged_rollback_rejected": bad_rollback.eligible is False and
                bad_rollback.checks["rollback_verified"] is False,
            "forged_lineage_row_rejected": bad_strict.eligible is False and
                bad_cold["eligible"] is False,
            "wrong_scope_rejected": wrong_scope.eligible is False and
                wrong_scope.evidence["lineage_gate_reason"] ==
                "r5_v4_target_scope_mismatch",
            "non_strict_promotion_rejected": non_strict_rejected,
            "asset_remains_draft": get_asset_status(
                conn, asset_id=registered.asset_id,
                target_scope=registered.target_scope)["status"] == "draft",
        }
        return {"valid": all(cases.values()), "case_count": len(cases),
                "failed": sorted(name for name, passed in cases.items() if not passed),
                "cases": cases, "role": ROLE, "asset_id": registered.asset_id,
                "asset_authority_receipt_digest": strict.receipt_digest,
                "asset_authority_eligible_in_ram": strict.eligible,
                "asset_promoted": False, "memory_m_plus_constructed": False,
                "heldout_transfer": False,
                "lineage_audit_digest": pure.evidence.get("r5_v4_lineage_audit_digest"),
                "rollback_digest": pure.evidence.get("r5_v4_rollback_digest")}
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify", type=Path)
    args = parser.parse_args()
    result = check()
    if args.output is not None:
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, sort_keys=True)
            handle.write("\n")
    if args.verify is not None and json.loads(args.verify.read_text(encoding="utf-8")) != result:
        raise ValueError("v4 TRAIN Asset authority RAM receipt drift")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
