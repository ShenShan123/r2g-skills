"""Research-only replay of the bounded R5 RTL TRAIN Asset evidence bundle.

Nothing here promotes an Asset. The helper is deliberately pinned to the two
reused-DEV TRAIN source files and the fresh source-rollback campaign. A later
RTL generation needs a new evidence contract, not caller-provided PASS flags.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from tehm.adapters.research_r5_rtl_scoped import (
    CASES, LINEAGE_DIGEST, LINEAGE_RECEIPT, acquisition, verify_acquisition,
)
from tehm.evaluation import research_r5_train_lineage_audit as lineage
from tehm.evaluation import research_r5_train_asset_rollback as rollback
from tehm.ids import stable_dumps
from tehm.rtl.skid_payload_action_v3 import DOMAIN

from .skid_binding_v3 import CONTRACT as SKID_V3_CONTRACT
from .structural_binding import bind_rtl_asset_to_source
from .validation import validate_rtl_rewrite_asset


EVIDENCE_VERSION = "tehm-r5-rtl-train-asset-evidence-v1"
ROLLBACK_VERSION = "tehm-r5-rtl-train-asset-rollback-binding-v1"
ROLLBACK_WORK = rollback.ROOT / "asset-rollback-r1"
CASE_ORDER = ("axis_register", "zipcpu_skidbuffer")


@dataclass(frozen=True)
class R5AssetEvidence:
    valid: bool
    lineages: tuple[str, ...]
    rollback_verified: bool
    validation_verified: bool
    binding_verified: bool
    reasons: tuple[str, ...]
    lineage_audit_digest: str
    rollback_digest: str


def is_r5_skid_asset(asset: Mapping) -> bool:
    if not isinstance(asset, Mapping):
        return False
    definition = asset.get("definition")
    if not isinstance(definition, Mapping):
        return False
    template = definition.get("binding_template")
    action = definition.get("action")
    return bool((isinstance(template, Mapping) and
                 template.get("contract") == SKID_V3_CONTRACT) or
                (isinstance(action, Mapping) and action.get("domain") == DOMAIN))


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _source(checked: Mapping, case: str) -> str:
    work = Path(checked["work"])
    source = checked["source_file"]
    path = (work / "fault" / "stage" / source if case == "axis_register"
            else work / "fault" / "backpressure" / "stage" / source)
    data = path.read_bytes()
    if _sha(data) != checked["before_source_sha256"]:
        raise ValueError("R5 Asset TRAIN source drift")
    return data.decode("utf-8")


def _oracle(checked: Mapping, *, obligation: str):
    raw = checked["raw"]

    def verify(candidate: str, _asset: Mapping) -> dict:
        if _sha(candidate.encode("utf-8")) != checked["after_source_sha256"]:
            return {"verdict": "FAIL"}
        if checked["case_id"] == "axis_register":
            from tehm.evaluation import research_r5_train_axis as axis
            cases = raw["arms"]["candidate"]["cases"]
            ids = axis.TARGET_IDS if obligation == "target" else axis.PRESERVATION_IDS
            passed = bool(ids) and all(cases[item] == "PASS" for item in ids)
        else:
            scenario = "backpressure" if obligation == "target" else "direct"
            passed = raw["arms"]["candidate/" + scenario]["verdict"]["verdict"] == "PASS"
        return {"verdict": "PASS" if passed else "FAIL"}

    return verify


def expected_rollback_binding() -> dict:
    fresh = rollback.verify(ROLLBACK_WORK)
    saved = (ROLLBACK_WORK / "receipt.json").read_bytes()
    if (fresh != json.loads(saved) or fresh.get("valid") is not True or
            fresh.get("role") != rollback.ROLE or
            fresh.get("fresh_oracle_execution") is not True or
            fresh.get("rollback_source_verified") is not True or
            fresh.get("asset_authority_recorded") is not False or
            fresh.get("memory_mremove") is not False or
            any(fresh["arms"][case]["target"] != "FAIL" or
                fresh["arms"][case]["preservation"] != "PASS"
                for case in CASE_ORDER)):
        raise ValueError("R5 Asset source rollback receipt does not cold replay")
    return {"version": ROLLBACK_VERSION, "verified": True,
            "role": rollback.ROLE, "receipt_digest": fresh["digest"],
            "receipt_sha256": _sha(saved),
            "cases": list(CASE_ORDER),
            "source_restored_before_native_execution": True,
            "memory_mremove": False}


def verify_train_asset_bundle(
    asset: Mapping, validation_receipts: Sequence[Mapping],
    bindings: Sequence[Mapping], rollback_receipt: Mapping | None,
) -> R5AssetEvidence:
    """Reconstruct all PASS fields from raw TRAIN, source, audit and rollback."""
    reasons: list[str] = []
    if not is_r5_skid_asset(asset):
        return R5AssetEvidence(False, (), False, False, False,
                               ("not_r5_skid_asset",), "", "")
    if (len(validation_receipts) != len(CASE_ORDER) or
            len(bindings) != len(CASE_ORDER)):
        return R5AssetEvidence(False, (), False, False, False,
                               ("r5_asset_evidence_cardinality",), "", "")
    audited = lineage.audit()
    saved_audit = json.loads(LINEAGE_RECEIPT.read_bytes())
    if (audited != saved_audit or audited.get("audit_digest") != LINEAGE_DIGEST or
            audited.get("distinct_source_lineages_for_bounded_pilot") is not True):
        reasons.append("r5_source_lineage_audit_drift")
    lineages: list[str] = []
    bindings_valid = True
    validations_valid = True
    for index, case in enumerate(CASE_ORDER):
        checked = verify_acquisition(acquisition(case, "treatment"))
        entry = audited["training"][case]
        if (entry["repository"] != checked["repository"] or
                entry["training_receipt_digest"] != checked["train_receipt_digest"]):
            reasons.append(case + ":lineage_train_receipt_drift")
        source = _source(checked, case)
        try:
            expected_bound = bind_rtl_asset_to_source(
                asset, source, design_id=checked["design"],
                public_context=checked["public_context"])
            bound_ok = stable_dumps(dict(bindings[index])) == stable_dumps(expected_bound)
        except (TypeError, ValueError, KeyError):
            bound_ok = False
            expected_bound = None
        if not bound_ok:
            bindings_valid = False
            reasons.append(case + ":binding_not_source_replayed")
        if expected_bound is not None:
            expected_validation = validate_rtl_rewrite_asset(
                expected_bound, source,
                verifier=_oracle(checked, obligation="target"),
                regression_verifier=_oracle(checked, obligation="preservation")).to_dict()
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
    rollback_digest = ""
    rollback_ok = False
    try:
        expected = expected_rollback_binding()
        rollback_digest = expected["receipt_digest"]
        rollback_ok = (isinstance(rollback_receipt, Mapping) and
                       stable_dumps(dict(rollback_receipt)) == stable_dumps(expected))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        reasons.append("r5_rollback_raw_replay_failed")
    if not rollback_ok:
        reasons.append("r5_rollback_binding_mismatch")
    valid = (not reasons and len(set(lineages)) == len(CASE_ORDER) and
             bindings_valid and validations_valid and rollback_ok)
    return R5AssetEvidence(valid, tuple(sorted(set(lineages))) if valid else (),
                           rollback_ok, validations_valid, bindings_valid,
                           tuple(sorted(set(reasons))),
                           audited.get("audit_digest", ""), rollback_digest)


def verify_train_row_metadata(validation_entries: Sequence[Mapping],
                              binding_entries: Sequence[Mapping]) -> bool:
    """Require exact TRAIN split and source lineage on strict ledger rows."""
    if (len(validation_entries) != len(CASE_ORDER) or
            len(binding_entries) != len(CASE_ORDER)):
        return False
    for index, case in enumerate(CASE_ORDER):
        repository = CASES[case]["repository"]
        for entries in (validation_entries, binding_entries):
            row = entries[index]
            if (row.get("split") != "training" or
                    row.get("lineage_id") != repository or
                    row.get("source_id") != case):
                return False
    return True


__all__ = ["EVIDENCE_VERSION", "ROLLBACK_VERSION", "R5AssetEvidence",
           "is_r5_skid_asset", "expected_rollback_binding",
           "verify_train_asset_bundle", "verify_train_row_metadata"]
