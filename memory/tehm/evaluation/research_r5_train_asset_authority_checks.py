"""RAM-only positive and adversarial R5 TRAIN Asset strict-authority checks.

The registered Asset remains draft. This checks legal core gate derivation but
does not export M+, promote production state, or run held-out transfer.
"""
from __future__ import annotations

import argparse
import copy
import json
import sqlite3
from pathlib import Path

from tehm import db
from tehm.adapters.research_r5_rtl_scoped import (
    PROFILE, acquisition, verify_acquisition,
)
from tehm.assets.authority import record_asset_authority, verify_asset_authority
from tehm.assets.lifecycle import evaluate_asset_authority
from tehm.assets.r5_train_evidence import (
    CASE_ORDER, expected_rollback_binding, verify_train_row_metadata,
)
from tehm.assets.registry import get_asset, get_asset_status, set_asset_status
from tehm.assets.skid_binding_v3 import with_skid_payload_binding_v3
from tehm.assets.structural_binding import bind_rtl_asset_to_source
from tehm.assets.synthesis import build_rtl_asset_proposal, register_asset_proposal
from tehm.assets.validation import validate_rtl_rewrite_asset
from tehm.rtl.skid_payload_action_v3 import payload_from_source_v3


ROLE = "RAM_ONLY_R5_TRAIN_ASSET_AUTHORITY_NOT_M_PLUS"


def _fault_source(checked: dict, case: str) -> str:
    work = Path(checked["work"])
    source = checked["source_file"]
    path = (work / "fault" / "stage" / source if case == "axis_register"
            else work / "fault" / "backpressure" / "stage" / source)
    return path.read_text(encoding="utf-8")


def _oracle(checked: dict, obligation: str):
    from tehm.assets.r5_train_evidence import _oracle as replay
    return replay(checked, obligation=obligation)


def check() -> dict:
    checked = {case: verify_acquisition(acquisition(case, "treatment"))
               for case in CASE_ORDER}
    first = checked[CASE_ORDER[0]]
    source = _fault_source(first, CASE_ORDER[0])
    proposal = build_rtl_asset_proposal(
        {}, name="r5-skid-v3-train-validation-draft",
        transformation_family="skid_payload_restore_shadow_v3",
        action_payload_template=payload_from_source_v3(source, first["public_context"]),
        compatibility_profile=PROFILE,
        verifier_obligations=("TRAIN target", "TRAIN preservation"),
        creator="researcher_assisted_reused_dev_train")
    proposal = with_skid_payload_binding_v3(proposal, source, first["public_context"])
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        db.ensure_schema(conn)
        registered = register_asset_proposal(conn, proposal)
        # RAM-only fixture normalization: bound copies include this registry
        # bookkeeping timestamp even though it is not part of Asset content.
        conn.execute("UPDATE tehm_assets SET created_at=? WHERE asset_id=?",
                     ("2000-01-01T00:00:00+00:00", registered.asset_id))

        asset = get_asset(conn, registered.asset_id)
        if asset is None:
            raise ValueError("R5 TRAIN Asset registration missing")
        bounds, validations, binding_rows, validation_rows = [], [], [], []
        for case in CASE_ORDER:
            item = checked[case]
            before = _fault_source(item, case)
            bound = bind_rtl_asset_to_source(
                asset, before, design_id=item["design"],
                public_context=item["public_context"])
            validation = validate_rtl_rewrite_asset(
                bound, before, verifier=_oracle(item, "target"),
                regression_verifier=_oracle(item, "preservation")).to_dict()
            bounds.append(bound)
            validations.append(validation)
            metadata = {"split": "training", "lineage_id": item["repository"],
                        "source_id": case}
            binding_rows.append({"asset": bound, **metadata})
            validation_rows.append({"receipt": validation, **metadata})
        rollback_binding = expected_rollback_binding()
        pure = evaluate_asset_authority(
            asset, validation_receipts=validations, bindings=bounds,
            rollback_receipt=rollback_binding, target_scope=PROFILE,
            min_lineages=2)
        wrong_scope = evaluate_asset_authority(
            asset, validation_receipts=validations, bindings=bounds,
            rollback_receipt=rollback_binding, target_scope=PROFILE + ".other",
            min_lineages=2)
        strict = record_asset_authority(
            conn, asset_id=registered.asset_id, target_scope=registered.target_scope,
            validation_receipts=validation_rows, bindings=binding_rows,
            rollback_receipt={"receipt": rollback_binding, "split": "ab",
                              "source_id": "asset-rollback-r1"},
            min_lineages=2)
        cold = verify_asset_authority(conn, strict)
        forged = dict(rollback_binding)
        forged["receipt_digest"] = "sha256:forged"
        forged_gate = evaluate_asset_authority(
            asset, validation_receipts=validations, bindings=bounds,
            rollback_receipt=forged, target_scope=PROFILE, min_lineages=2)
        forged_rows = copy.deepcopy(validation_rows)
        forged_rows[1]["lineage_id"] = "invented_lineage"
        bad_strict = record_asset_authority(
            conn, asset_id=registered.asset_id, target_scope=registered.target_scope,
            validation_receipts=forged_rows, bindings=binding_rows,
            rollback_receipt={"receipt": rollback_binding, "split": "ab",
                              "source_id": "asset-rollback-r1"},
            min_lineages=2)
        bad_cold = verify_asset_authority(conn, bad_strict)
        conn.execute("SAVEPOINT r5_non_strict_promotion_negative")
        try:
            set_asset_status(
                conn, asset_id=registered.asset_id, target_scope=registered.target_scope,
                status="shadow", commit=False)
            set_asset_status(
                conn, asset_id=registered.asset_id, target_scope=registered.target_scope,
                status="candidate", commit=False)
            try:
                set_asset_status(
                    conn, asset_id=registered.asset_id,
                    target_scope=registered.target_scope, status="promoted",
                    gates={key: True for key in pure.checks},
                    strict_asset_authority=False, commit=False)
                non_strict_rejected = False
            except ValueError as exc:
                non_strict_rejected = "strict raw-replayed authority" in str(exc)
        finally:
            conn.execute("ROLLBACK TO SAVEPOINT r5_non_strict_promotion_negative")
            conn.execute("RELEASE SAVEPOINT r5_non_strict_promotion_negative")


        status = get_asset_status(
            conn, asset_id=registered.asset_id, target_scope=registered.target_scope)
        cases = {
            "two_exact_train_source_lineages": pure.evidence["lineages"] == [
                "ZipCPU/wb2axip", "alexforencich/verilog-axis"],
            "pure_core_gates_eligible": pure.eligible is True and not pure.missing and
                all(pure.checks.values()),
            "strict_evidence_rows_exact": verify_train_row_metadata(
                [{k: row[k] for k in ("split", "lineage_id", "source_id")}
                 for row in validation_rows],
                [{k: row[k] for k in ("split", "lineage_id", "source_id")}
                 for row in binding_rows]),
            "strict_ledger_eligible": strict.eligible is True and cold["eligible"] is True,
            "forged_rollback_rejected": forged_gate.eligible is False and
                forged_gate.checks["rollback_verified"] is False,
            "forged_lineage_row_rejected": not verify_train_row_metadata(
                [{k: row[k] for k in ("split", "lineage_id", "source_id")}
                 for row in forged_rows],
                [{k: row[k] for k in ("split", "lineage_id", "source_id")}
                 for row in binding_rows]) and
                bad_strict.eligible is False and bad_cold["eligible"] is False,
            "asset_remains_draft": status["status"] == "draft",
            "non_strict_promotion_rejected": non_strict_rejected,
            "wrong_target_scope_rejected": wrong_scope.eligible is False and
                wrong_scope.evidence.get("lineage_gate_reason") ==
                "r5_target_scope_mismatch",
        }
        return {"valid": all(cases.values()), "case_count": len(cases),
                "failed": sorted(key for key, ok in cases.items() if not ok),
                "cases": cases, "role": ROLE,
                "asset_id": registered.asset_id,
                "asset_authority_receipt_digest": strict.receipt_digest,
                "asset_authority_eligible_in_ram": strict.eligible,
                "asset_promoted": False, "memory_m_plus_constructed": False,
                "heldout_transfer": False,
                "lineage_audit_digest": pure.evidence.get("r5_lineage_audit_digest"),
                "rollback_digest": pure.evidence.get("r5_rollback_digest")}
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
        raise ValueError("R5 TRAIN Asset authority receipt does not cold replay")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
