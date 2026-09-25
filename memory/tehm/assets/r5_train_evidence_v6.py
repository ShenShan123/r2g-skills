"""Strict v6 TRAIN Asset evidence from raw acquisitions and fresh v6 rollback.

The existing two TRAIN sources are researcher-assisted reused DEV. No v5
rollback identity, DEV target, held-out result, or caller-supplied PASS bit
is accepted as v6 Asset authority.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tehm.adapters import research_r5_rtl_scoped_v3 as train
from tehm.evaluation import research_r5_train_lineage_audit as lineage
from tehm.evaluation import research_r5_train_asset_rollback_v6 as rollback
from tehm.ids import stable_dumps
from tehm.rtl.skid_payload_action_v6 import (
    PROFILE, apply_skid_payload_action_v6, payload_from_source_v6,
)

from .skid_binding_v6 import is_skid_v6_asset
from .structural_binding import bind_rtl_asset_to_source
from .validation import validate_rtl_rewrite_asset

EVIDENCE_VERSION = "tehm-r5-rtl-train-asset-evidence-v6-r1"
ROLLBACK_VERSION = "tehm-r5-rtl-train-asset-rollback-binding-v6-r1"
ROLLBACK_WORK = rollback.runner.ROOT / rollback.WORK_NAME
CASE_ORDER = rollback.CASE_ORDER


@dataclass(frozen=True)
class R5V6AssetEvidence:
    valid: bool
    lineages: tuple[str, ...]
    rollback_verified: bool
    validation_verified: bool
    binding_verified: bool
    reasons: tuple[str, ...]
    lineage_audit_digest: str
    rollback_digest: str
    shared_contract_digest: str


def expected_rollback_binding() -> dict:
    fresh = rollback.verify(ROLLBACK_WORK)
    saved = (ROLLBACK_WORK / "receipt.json").read_bytes()
    if (fresh != json.loads(saved) or fresh.get("valid") is not True or
            fresh.get("schema") != rollback.SCHEMA or
            fresh.get("role") != rollback.ROLE or
            fresh.get("fresh_oracle_execution") is not True or
            fresh.get("rollback_source_verified") is not True or
            fresh.get("asset_authority_recorded") is not False or
            fresh.get("memory_mremove") is not False or
            fresh.get("heldout_transfer") is not False or
            any(fresh["arms"][case]["target"] != "FAIL" or
                fresh["arms"][case]["preservation"] != "PASS"
                for case in CASE_ORDER)):
        raise ValueError("v6 TRAIN source rollback receipt does not cold replay")
    return {"version": ROLLBACK_VERSION, "verified": True,
            "role": rollback.ROLE, "receipt_digest": fresh["digest"],
            "receipt_sha256": rollback.runner._sha(saved),
            "cases": list(CASE_ORDER),
            "shared_contract_digest": train._digest(train.MEASUREMENT_CONTRACT),
            "source_restored_before_native_execution": True,
            "memory_mremove": False}


def verify_train_asset_bundle(
    asset: Mapping, validation_receipts: Sequence[Mapping],
    bindings: Sequence[Mapping], rollback_receipt: Mapping | None,
) -> R5V6AssetEvidence:
    reasons: list[str] = []

    def empty(reason: str) -> R5V6AssetEvidence:
        return R5V6AssetEvidence(False, (), False, False, False,
                                 (reason,), "", "", "")

    if not is_skid_v6_asset(asset):
        return empty("not_r5_skid_v6_asset")
    if len(validation_receipts) != len(CASE_ORDER) or len(bindings) != len(CASE_ORDER):
        return empty("r5_v6_asset_evidence_cardinality")
    if asset.get("compatibility", {}).get("compatibility_profile") != PROFILE:
        return empty("r5_v6_asset_scope_mismatch")
    audited = lineage.audit()
    saved_audit = json.loads(train.v1.LINEAGE_RECEIPT.read_bytes())
    if (audited != saved_audit or
            audited.get("audit_digest") != train.v1.LINEAGE_DIGEST or
            audited.get("distinct_source_lineages_for_bounded_pilot") is not True):
        reasons.append("r5_v6_source_lineage_audit_drift")
    from tehm.adapters.research_r5_rtl_scoped_v3 import oracle_for
    lineages: list[str] = []
    bindings_valid = True
    validations_valid = True
    witness_digests: set[str] = set()
    contract_digest = train._digest(train.MEASUREMENT_CONTRACT)
    for index, case in enumerate(CASE_ORDER):
        checked = train.verify_acquisition(train.acquisition(case, "treatment"))
        record = train.build_record(train.acquisition(case, "treatment"))
        if train.replay_record(record) != record.verification["scoped_execution"]["pair_receipt"]:
            reasons.append(case + ":canonical_v3_record_drift")
        witness = record.verification["scoped_execution"]["pair_receipt"]["after"]["oracle_instance_witness"]
        if (witness["shared_contract_digest"] != contract_digest or
                witness["repository"] != checked["repository"] or
                witness["train_receipt_digest"] != checked["train_receipt_digest"]):
            reasons.append(case + ":oracle_instance_witness_mismatch")
        witness_digests.add(witness["witness_digest"])
        entry = audited["training"][case]
        if (entry["repository"] != checked["repository"] or
                entry["training_receipt_digest"] != checked["train_receipt_digest"]):
            reasons.append(case + ":lineage_train_receipt_drift")
        source = train._source(checked)
        try:
            payload = payload_from_source_v6(source, checked["public_context"])
            candidate, action = apply_skid_payload_action_v6(source, payload)
            if (train._sha(candidate.encode()) != checked["after_source_sha256"] or
                    action.get("rewritten") != 1 or
                    action.get("source_binding_rederived") is not True or
                    action.get("delegated_from") is None):
                raise ValueError("v6 TRAIN candidate drift")
            expected_bound = bind_rtl_asset_to_source(
                asset, source, design_id=checked["design"],
                public_context=checked["public_context"])
            bound_ok = stable_dumps(dict(bindings[index])) == stable_dumps(expected_bound)
        except (TypeError, ValueError, KeyError):
            bound_ok = False
            expected_bound = None
        if not bound_ok:
            bindings_valid = False
            reasons.append(case + ":binding_not_v6_source_replayed")
        if expected_bound is not None:
            expected_validation = validate_rtl_rewrite_asset(
                expected_bound, source,
                verifier=oracle_for(checked, record, "target"),
                regression_verifier=oracle_for(checked, record, "preservation")).to_dict()
            valid_ok = (stable_dumps(dict(validation_receipts[index])) ==
                        stable_dumps(expected_validation) and
                        expected_validation["independent_verifier"] is True and
                        expected_validation["oracle_verdict"] == "PASS" and
                        expected_validation["regression_verdict"] == "PASS" and
                        not expected_validation["errors"])
        else:
            valid_ok = False
        if not valid_ok:
            validations_valid = False
            reasons.append(case + ":validation_not_raw_oracle_replayed")
        if bound_ok and valid_ok and not reasons:
            lineages.append(checked["repository"])
    if len(witness_digests) != len(CASE_ORDER):
        reasons.append("r5_v6_oracle_instance_witnesses_not_distinct")
    rollback_digest = ""
    rollback_ok = False
    try:
        expected = expected_rollback_binding()
        rollback_digest = expected["receipt_digest"]
        rollback_ok = (isinstance(rollback_receipt, Mapping) and
                       stable_dumps(dict(rollback_receipt)) == stable_dumps(expected))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        reasons.append("r5_v6_rollback_raw_replay_failed")
    if not rollback_ok:
        reasons.append("r5_v6_rollback_binding_mismatch")
    valid = (not reasons and len(set(lineages)) == len(CASE_ORDER) and
             bindings_valid and validations_valid and rollback_ok)
    return R5V6AssetEvidence(valid, tuple(sorted(set(lineages))) if valid else (),
                             rollback_ok, validations_valid, bindings_valid,
                             tuple(sorted(set(reasons))),
                             audited.get("audit_digest", ""), rollback_digest,
                             contract_digest)


def verify_train_row_metadata(validation_entries: Sequence[Mapping],
                              binding_entries: Sequence[Mapping]) -> bool:
    if len(validation_entries) != len(CASE_ORDER) or len(binding_entries) != len(CASE_ORDER):
        return False
    for index, case in enumerate(CASE_ORDER):
        repository = train.CASES[case]["repository"]
        for entries in (validation_entries, binding_entries):
            row = entries[index]
            if (row.get("split") != "training" or
                    row.get("lineage_id") != repository or
                    row.get("source_id") != case):
                return False
    return True


__all__ = ["EVIDENCE_VERSION", "ROLLBACK_VERSION", "R5V6AssetEvidence",
           "expected_rollback_binding", "verify_train_asset_bundle",
           "verify_train_row_metadata"]
