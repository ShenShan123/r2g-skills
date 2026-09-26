"""RAM-only three-source v7 raw-TRAIN Asset authority and negative checks.

An eligible strict receipt is an engineering preflight, not M+, Mremove,
held-out transfer, or a persistent/production promotion.
"""
from __future__ import annotations

import argparse
import copy
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

from tehm import db
from tehm.adapters import research_r5_rtl_scoped_v3 as old_train
from tehm.assets.authority import record_asset_authority, verify_asset_authority
from tehm.assets.lifecycle import evaluate_asset_authority
from tehm.assets.r5_libsv_train_raw_v7 import RUN as LIBSV_RUN, verify as verify_libsv_raw
from tehm.assets.r5_train_evidence_v7 import (
    CASE_ORDER, REPOSITORIES, expected_rollback_binding, verify_train_row_metadata,
)
from tehm.assets.registry import get_asset, get_asset_status
from tehm.assets.skid_binding_v7 import with_skid_payload_binding_v7
from tehm.assets.structural_binding import bind_rtl_asset_to_source
from tehm.assets.synthesis import build_rtl_asset_proposal, register_asset_proposal
from tehm.assets.validation import validate_rtl_rewrite_asset
from tehm.evaluation.research_r5_skid_binding_v7 import LIBSV_CONTEXT
from tehm.rtl.skid_payload_action_v7 import PROFILE, payload_from_source_v7

ROLE = "RAM_ONLY_R5_V7_TRAIN_ASSET_AUTHORITY_NOT_M_PLUS"
ROOT = Path("/data1/zhangdy/RTL/RTL_testbench/_r5_pilot")
RAM_CLOCK = "2026-09-25T00:00:00+00:00"


def _libsv_oracle(raw: dict, arm: str):
    if raw["raw_six_arm_valid"] is not True or raw["observed"]["private-clean-" + arm][
            "verdict"] != "PASS":
        raise ValueError("LibSV raw oracle not PASS")
    from tehm.assets.r5_libsv_train_raw_v7 import SOURCE_SHA
    from hashlib import sha256

    def check(candidate: str, _asset: dict) -> dict:
        return {"verdict": "PASS" if sha256(candidate.encode()).hexdigest() ==
                SOURCE_SHA["clean"] else "FAIL"}

    return check


def check() -> dict:
    with patch("tehm.db.now_local", return_value=RAM_CLOCK):
        return _check()


def _check() -> dict:
    raw = verify_libsv_raw()
    old = {case: old_train.verify_acquisition(old_train.acquisition(case, "treatment"))
           for case in CASE_ORDER[:2]}
    records = {case: old_train.build_record(old_train.acquisition(case, "treatment"))
               for case in CASE_ORDER[:2]}
    first = old[CASE_ORDER[0]]
    source = old_train._source(first)
    proposal = build_rtl_asset_proposal(
        {}, name="r5-skid-v7-three-source-train-validation-draft",
        transformation_family="skid_payload_restore_shadow_v7",
        action_payload_template=payload_from_source_v7(source, first["public_context"]),
        compatibility_profile=PROFILE,
        verifier_obligations=("TRAIN target", "TRAIN preservation"),
        creator="researcher_assisted_reused_dev_train_generation_5")
    proposal = with_skid_payload_binding_v7(proposal, source, first["public_context"])
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        db.ensure_schema(conn)
        registered = register_asset_proposal(conn, proposal)
        asset = get_asset(conn, registered.asset_id)
        if asset is None:
            raise ValueError("v7 TRAIN Asset missing from RAM registry")
        bounds, validations, binding_rows, validation_rows = [], [], [], []
        for case in CASE_ORDER:
            if case == "libsv_train":
                before = (LIBSV_RUN / "evaluator-inputs/source/fault/skid_buffer.sv").read_text()
                context, design = LIBSV_CONTEXT, case
                target_oracle = _libsv_oracle(raw, "target")
                preservation_oracle = _libsv_oracle(raw, "preservation")
            else:
                item = old[case]
                before = old_train._source(item)
                context, design = item["public_context"], item["design"]
                target_oracle = old_train.oracle_for(item, records[case], "target")
                preservation_oracle = old_train.oracle_for(item, records[case], "preservation")
            bound = bind_rtl_asset_to_source(
                asset, before, design_id=design, public_context=context)
            validation = validate_rtl_rewrite_asset(
                bound, before, verifier=target_oracle,
                regression_verifier=preservation_oracle).to_dict()
            metadata = {"split": "training", "lineage_id": REPOSITORIES[case],
                        "source_id": case}
            bounds.append(bound)
            validations.append(validation)
            binding_rows.append({"asset": bound, **metadata})
            validation_rows.append({"receipt": validation, **metadata})
        rollback = expected_rollback_binding()
        pure = evaluate_asset_authority(
            asset, validation_receipts=validations, bindings=bounds,
            rollback_receipt=rollback, target_scope=PROFILE, min_lineages=3)
        strict = record_asset_authority(
            conn, asset_id=registered.asset_id, target_scope=PROFILE,
            validation_receipts=validation_rows, bindings=binding_rows,
            rollback_receipt={"receipt": rollback, "split": "ab",
                              "source_id": "asset-rollback-v7-r1"}, min_lineages=3)
        cold = verify_asset_authority(conn, strict)
        forged = {**rollback, "receipt_digest": "sha256:forged"}
        bad_rollback = evaluate_asset_authority(
            asset, validation_receipts=validations, bindings=bounds,
            rollback_receipt=forged, target_scope=PROFILE, min_lineages=3)
        stale = {**rollback,
                 "version": "tehm-r5-rtl-train-asset-rollback-binding-v6-r1"}
        bad_generation = evaluate_asset_authority(
            asset, validation_receipts=validations, bindings=bounds,
            rollback_receipt=stale, target_scope=PROFILE, min_lineages=3)
        bad_rows = copy.deepcopy(validation_rows)
        bad_rows[2]["lineage_id"] = "forged/fourth-lineage"
        forged_metadata_rejected = not verify_train_row_metadata(bad_rows, binding_rows)
        bad_strict = record_asset_authority(
            conn, asset_id=registered.asset_id, target_scope=PROFILE,
            validation_receipts=bad_rows, bindings=binding_rows,
            rollback_receipt={"receipt": rollback, "split": "ab",
                              "source_id": "asset-rollback-v7-r1"}, min_lineages=3)
        bad_cold = verify_asset_authority(conn, bad_strict)
        wrong_scope = evaluate_asset_authority(
            asset, validation_receipts=validations, bindings=bounds,
            rollback_receipt=rollback, target_scope=PROFILE + ".other", min_lineages=3)
        cases = {
            "three_bounded_train_source_groups": pure.evidence["lineages"] ==
                sorted(REPOSITORIES.values()),
            "pure_raw_train_gates_eligible": pure.eligible is True and not pure.missing,
            "strict_row_metadata_exact": verify_train_row_metadata(
                validation_rows, binding_rows),
            "strict_ram_ledger_cold_eligible": strict.eligible is True and
                cold["eligible"] is True,
            "forged_rollback_rejected": bad_rollback.eligible is False and
                bad_rollback.checks["rollback_verified"] is False,
            "old_generation_rejected": bad_generation.eligible is False and
                bad_generation.checks["rollback_verified"] is False,
            "forged_lineage_row_rejected": forged_metadata_rejected and
                bad_strict.eligible is False and bad_cold["eligible"] is False,
            "wrong_scope_rejected": wrong_scope.eligible is False and
                wrong_scope.evidence["lineage_gate_reason"] ==
                "r5_v7_target_scope_mismatch",
            "asset_remains_draft": get_asset_status(
                conn, asset_id=registered.asset_id,
                target_scope=registered.target_scope)["status"] == "draft",
            "no_knowledge_memory": db.count_rows(conn, "tehm_mechanism_knowledge") == 0,
        }
        return {"valid": all(cases.values()), "case_count": len(cases),
                "failed": sorted(name for name, passed in cases.items() if not passed),
                "cases": cases, "role": ROLE, "asset_id": registered.asset_id,
                "asset_authority_receipt_digest": strict.receipt_digest,
                "asset_authority_eligible_in_ram": strict.eligible,
                "asset_promoted": False, "memory_m_plus_constructed": False,
                "heldout_transfer": False, "ram_fixture_clock": RAM_CLOCK,
                "lineage_audit_digest": pure.evidence.get("r5_v7_lineage_audit_digest"),
                "rollback_digest": pure.evidence.get("r5_v7_rollback_digest"),
                "pure_missing": list(pure.missing),
                "pure_evidence": dict(pure.evidence),
                "strict_missing": list(strict.missing),
                "cold_reasons": cold.get("reasons", [])}
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify", type=Path)
    args = parser.parse_args()
    result = check()
    if args.output is not None:
        if args.output.parent != ROOT / "memory" or args.output.exists() or args.output.is_symlink():
            raise ValueError("v7 RAM receipt must be fresh under pilot memory")
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
    elif json.loads(args.verify.read_bytes()) != result:
        raise ValueError("v7 TRAIN authority RAM receipt drift")
    print(json.dumps({"valid": result["valid"], "case_count": result["case_count"],
                      "failed": result["failed"]}, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
