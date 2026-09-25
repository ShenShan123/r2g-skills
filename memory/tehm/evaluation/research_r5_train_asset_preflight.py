"""RAM-only Asset validation against the two cold-replayed R5 RTL TRAIN runs.

This reuses already executed TRAIN oracles, not a fresh held-out test. It never
records Asset authority or changes status, and deliberately leaves audited
lineage binding and rollback gates closed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from tehm import db
from tehm.adapters.research_r5_rtl_scoped import (
    CASES, PROFILE, acquisition, build_record, verify_acquisition,
)
from tehm.assets.lifecycle import evaluate_asset_authority
from tehm.assets.registry import get_asset
from tehm.assets.skid_binding_v3 import with_skid_payload_binding_v3
from tehm.assets.structural_binding import bind_rtl_asset_to_source
from tehm.assets.synthesis import build_rtl_asset_proposal, register_asset_proposal
from tehm.assets.validation import validate_rtl_rewrite_asset
from tehm.rtl.skid_payload_action_v3 import payload_from_source_v3


ROLE = "TRAIN_REUSED_DEV_ASSET_VALIDATION_PREFLIGHT_ONLY"


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _fault_source(checked: dict) -> str:
    work = Path(checked["work"])
    source = checked["source_file"]
    path = (work / "fault" / "stage" / source if checked["case_id"] == "axis_register"
            else work / "fault" / "backpressure" / "stage" / source)
    data = path.read_bytes()
    if _sha(data) != checked["before_source_sha256"]:
        raise ValueError("TRAIN fault source drift")
    return data.decode("utf-8")


def _oracle_callback(checked: dict, expected: dict, arm: str):
    def replay(candidate: str, _asset: dict) -> dict:
        # The callback is downstream of a raw receipt cold replay. It cannot
        # award PASS to a different candidate or a missing obligation.
        if _sha(candidate.encode("utf-8")) != checked["after_source_sha256"]:
            return {"verdict": "FAIL"}
        result = expected["full_oracle"]["after"]
        key = "target_verdict" if arm == "target" else "preservation_verdict"
        return {"verdict": "PASS" if result["complete"] is True and
                result[key] == "PASS" else "FAIL"}
    return replay


def check() -> dict:
    checked = {case: verify_acquisition(acquisition(case, "treatment"))
               for case in CASES}
    records = {case: build_record(acquisition(case, "treatment"))
               for case in CASES}
    source0 = _fault_source(checked["axis_register"])
    proposal = build_rtl_asset_proposal(
        {}, name="r5-skid-v3-train-validation-draft",
        transformation_family="skid_payload_restore_shadow_v3",
        action_payload_template=payload_from_source_v3(
            source0, checked["axis_register"]["public_context"]),
        compatibility_profile=PROFILE,
        verifier_obligations=("TRAIN target", "TRAIN preservation"),
        creator="researcher_assisted_reused_dev_train")
    proposal = with_skid_payload_binding_v3(
        proposal, source0, checked["axis_register"]["public_context"])
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        db.ensure_schema(conn)
        registered = register_asset_proposal(conn, proposal)
        asset = get_asset(conn, registered.asset_id)
        if asset is None or registered.status != "draft":
            raise ValueError("R5 TRAIN Asset draft registration failed")
        bindings, validations, details = [], [], {}
        for case in CASES:
            item = checked[case]
            source = _fault_source(item)
            bound = bind_rtl_asset_to_source(
                asset, source, design_id=item["design"],
                public_context=item["public_context"])
            expected = records[case].verification
            validation = validate_rtl_rewrite_asset(
                bound, source,
                verifier=_oracle_callback(item, expected, "target"),
                regression_verifier=_oracle_callback(item, expected, "preservation"))
            bindings.append(bound)
            validations.append(validation.to_dict())
            details[case] = {
                "repository": item["repository"], "source_git_sha": item["source_git_sha"],
                "before_source_sha256": item["before_source_sha256"],
                "after_source_sha256": item["after_source_sha256"],
                "train_receipt_digest": item["train_receipt_digest"],
                "lineage_receipt_digest": item["lineage_receipt_digest"],
                "binding_contract": bound["provenance"]["binding_contract"],
                "binding_digest": bound["provenance"]["binding_digest"],
                "validation": validation.to_dict(),
            }
        gate = evaluate_asset_authority(
            asset, validation_receipts=validations, bindings=bindings,
            rollback_receipt=None, target_scope=PROFILE, min_lineages=2)
        wrong_candidate_rejected = all(
            _oracle_callback(checked[case], records[case].verification, "target")(
                _fault_source(checked[case]), {})["verdict"] == "FAIL"
            for case in CASES)
        checks = {
            "two_train_sources_bound": len(bindings) == 2,
            "actual_candidate_oracle_replayed": all(
                item["static_valid"] is True and
                item["independent_verifier"] is True and
                item["oracle_verdict"] == "PASS" and
                item["regression_verdict"] == "PASS" and
                not item["errors"] for item in validations),
            "wrong_candidate_rejected": wrong_candidate_rejected,
            "source_binding_compatible": gate.checks["compatibility_verified"] is True,
            "lineage_and_rollback_still_closed": (
                gate.checks["cross_lineage_verified"] is False and
                gate.checks["rollback_verified"] is False and
                set(gate.missing) == {"cross_lineage_verified", "rollback_verified"}),
            "asset_not_admitted": gate.eligible is False and registered.status == "draft",
        }
        return {
            "valid": all(checks.values()), "role": ROLE,
            "checks": checks, "asset_id": registered.asset_id,
            "gate_checks": gate.checks, "gate_missing": list(gate.missing),
            "sources": details, "authority_recorded": False,
            "m_plus_constructed": False, "heldout_transfer": False,
            "fresh_oracle_execution": False,
        }
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
        raise ValueError("R5 TRAIN Asset preflight receipt does not cold replay")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
