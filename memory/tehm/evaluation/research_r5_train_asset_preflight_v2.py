"""Generation-2 TRAIN Asset validation preflight against raw oracle replay.

This is researcher-assisted, reused DEV-as-TRAIN evidence. It checks the
shared v2 measurement contract and v4 source binding, but cannot establish
Asset authority before a fresh v4 rollback and strict lineage bundle.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from tehm import db
from tehm.adapters import research_r5_rtl_scoped_v2 as train
from tehm.assets.lifecycle import evaluate_asset_authority
from tehm.assets.registry import get_asset
from tehm.assets.skid_binding_v4 import with_skid_payload_binding_v4
from tehm.assets.structural_binding import bind_rtl_asset_to_source
from tehm.assets.synthesis import build_rtl_asset_proposal, register_asset_proposal
from tehm.assets.validation import validate_rtl_rewrite_asset
from tehm.ids import stable_dumps
from tehm.rtl.skid_payload_action_v4 import PROFILE, payload_from_source_v4


ROLE = "TRAIN_REUSED_DEV_GEN2_ASSET_PREFLIGHT_NOT_AUTHORITY"


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _oracle(checked: dict, record, arm: str):
    expected = record.verification["full_oracle"]["after"]
    witness = record.verification["scoped_execution"]["pair_receipt"]["after"]["oracle_instance_witness"]
    if (witness["source_after_sha256"] != checked["after_source_sha256"] or
            witness["train_receipt_digest"] != checked["train_receipt_digest"] or
            witness["shared_contract_digest"] != train._digest(train.MEASUREMENT_CONTRACT)):
        raise ValueError("v2 TRAIN oracle instance witness does not bind raw source")

    def check(candidate: str, _asset: dict) -> dict:
        if _sha(candidate.encode("utf-8")) != checked["after_source_sha256"]:
            return {"verdict": "FAIL"}
        verdict = expected["target_verdict" if arm == "target" else "preservation_verdict"]
        return {"verdict": "PASS" if expected["complete"] is True and
                verdict == "PASS" else "FAIL"}

    return check


def check() -> dict:
    checked = {case: train.verify_acquisition(train.acquisition(case, "treatment"))
               for case in train.CASES}
    records = {case: train.build_record(train.acquisition(case, "treatment"))
               for case in train.CASES}
    first = next(iter(train.CASES))
    first_source = train._source(checked[first])
    proposal = build_rtl_asset_proposal(
        {}, name="r5-skid-v4-train-validation-draft",
        transformation_family="skid_payload_restore_shadow_v4",
        action_payload_template=payload_from_source_v4(
            first_source, checked[first]["public_context"]),
        compatibility_profile=PROFILE,
        verifier_obligations=("TRAIN target", "TRAIN preservation"),
        creator="researcher_assisted_reused_dev_train_generation_2")
    proposal = with_skid_payload_binding_v4(
        proposal, first_source, checked[first]["public_context"])
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        db.ensure_schema(conn)
        registration = register_asset_proposal(conn, proposal)
        asset = get_asset(conn, registration.asset_id)
        if asset is None or registration.status != "draft":
            raise ValueError("v4 TRAIN Asset draft registration failed")
        bindings, validations, details = [], [], {}
        for case, item in checked.items():
            source = train._source(item)
            bound = bind_rtl_asset_to_source(
                asset, source, design_id=item["design"],
                public_context=item["public_context"])
            record = records[case]
            if train.replay_record(record) != record.verification["scoped_execution"]["pair_receipt"]:
                raise ValueError(case + ":v2 canonical TRAIN replay mismatch")
            validation = validate_rtl_rewrite_asset(
                bound, source,
                verifier=_oracle(item, record, "target"),
                regression_verifier=_oracle(item, record, "preservation")).to_dict()
            witness = record.verification["scoped_execution"]["pair_receipt"]["after"]["oracle_instance_witness"]
            bindings.append(bound)
            validations.append(validation)
            details[case] = {
                "repository": item["repository"],
                "source_git_sha": item["source_git_sha"],
                "train_receipt_digest": item["train_receipt_digest"],
                "oracle_instance_witness_digest": witness["witness_digest"],
                "shared_contract_digest": witness["shared_contract_digest"],
                "binding_digest": bound["provenance"]["binding_digest"],
                "validation_digest": _sha(stable_dumps(validation).encode()),
                "validation": validation,
            }
        gate = evaluate_asset_authority(
            asset, validation_receipts=validations, bindings=bindings,
            rollback_receipt=None, target_scope=PROFILE, min_lineages=2)
        checks = {
            "two_distinct_train_repositories": len({item["repository"]
                for item in checked.values()}) == 2,
            "shared_v2_measurement_contract": len({item["shared_contract_digest"]
                for item in details.values()}) == 1 and
                next(iter(details.values()))["shared_contract_digest"] ==
                train._digest(train.MEASUREMENT_CONTRACT),
            "distinct_oracle_instance_witnesses": len({item["oracle_instance_witness_digest"]
                for item in details.values()}) == 2,
            "two_train_candidate_oracles_pass": all(
                item["static_valid"] is True and
                item["independent_verifier"] is True and
                item["oracle_verdict"] == "PASS" and
                item["regression_verdict"] == "PASS" and
                not item["errors"] for item in validations),
            "wrong_candidate_rejected": all(
                _oracle(checked[case], records[case], "target")(
                    train._source(checked[case]), {})["verdict"] == "FAIL"
                for case in train.CASES),
            "exact_source_binding_compatible": gate.checks["compatibility_verified"] is True,
            "authority_gates_still_closed": gate.eligible is False and
                set(gate.missing) == {"cross_lineage_verified", "rollback_verified"},
            "asset_remains_draft": registration.status == "draft",
        }
        return {"valid": all(checks.values()), "role": ROLE,
                "checks": checks, "asset_id": registration.asset_id,
                "gate_checks": gate.checks, "gate_missing": list(gate.missing),
                "sources": details, "authority_recorded": False,
                "m_plus_constructed": False, "heldout_transfer": False,
                "fresh_oracle_execution": False}
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
        raise ValueError("generation-2 TRAIN Asset preflight receipt drift")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
