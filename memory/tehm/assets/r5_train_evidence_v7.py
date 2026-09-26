"""Strict three-source v7 TRAIN Asset evidence, bounded to reused DEV tasks.

Two frozen acquisitions and the new LibSV six-arm run are cold replayed. This
does not establish held-out transfer, source-origin independence, or Mremove.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tehm.adapters import research_r5_rtl_scoped_v3 as old_train
from tehm.evaluation import research_r5_train_lineage_audit as old_lineage
from tehm.evaluation import research_r5_train_asset_rollback_v7 as rollback
from tehm.evaluation import research_r5_skid_binding_v7 as binder
from tehm.ids import stable_dumps
from tehm.rtl.skid_payload_action_v7 import (
    PROFILE, apply_skid_payload_action_v7, payload_from_source_v7,
)

from .r5_libsv_train_raw_v7 import (
    PILOT, REPOSITORY as LIBSV_REPOSITORY, RUN as LIBSV_RUN,
    SOURCE_SHA as LIBSV_SHA, verify as verify_libsv_raw,
)
from .skid_binding_v7 import is_skid_v7_asset
from .structural_binding import bind_rtl_asset_to_source
from .validation import validate_rtl_rewrite_asset

EVIDENCE_VERSION = "tehm-r5-rtl-train-asset-evidence-v7-r1"
ROLLBACK_VERSION = "tehm-r5-rtl-train-asset-rollback-binding-v7-r1"
ROLLBACK_WORK = rollback.runner.ROOT / rollback.WORK_NAME
CASE_ORDER = rollback.CASE_ORDER
REPOSITORIES = {"axis_register": "alexforencich/verilog-axis",
                "zipcpu_skidbuffer": "ZipCPU/wb2axip",
                "libsv_train": LIBSV_REPOSITORY}
MEASUREMENT_CONTRACT = {
    **old_train.MEASUREMENT_CONTRACT,
    "version": "tehm-r5-skid-payload-measurement-v7",
    "scope": PROFILE,
}


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


@dataclass(frozen=True)
class R5V7AssetEvidence:
    valid: bool
    lineages: tuple[str, ...]
    rollback_verified: bool
    validation_verified: bool
    binding_verified: bool
    reasons: tuple[str, ...]
    lineage_audit_digest: str
    rollback_digest: str
    shared_contract_digest: str


def _normalized_lines(source: str) -> list[str]:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    source = re.sub(r"//[^\n]*", "", source)
    return [clean for line in source.splitlines()
            if (clean := re.sub(r"\s+", "", line))]


def _windows(lines: list[str]) -> set[tuple[str, ...]]:
    return {tuple(lines[index:index + 8]) for index in range(max(0, len(lines) - 7))}


def _lineage_audit() -> dict:
    old = old_lineage.audit()
    saved_old = json.loads(old_train.v1.LINEAGE_RECEIPT.read_bytes())
    if (old != saved_old or old.get("audit_digest") != old_train.v1.LINEAGE_DIGEST or
            old.get("distinct_source_lineages_for_bounded_pilot") is not True):
        raise ValueError("AXIS/ZipCPU source-lineage audit drift")
    roots = {"libsv_target": (PILOT.parent / LIBSV_REPOSITORY /
                              "libsv/fifos/skid_buffer.sv", LIBSV_SHA["clean"]),
             "axis_train": (PILOT.parent / REPOSITORIES["axis_register"] /
                            "rtl/axis_register.v",
                            "599fde2d6c2d806643bbffb7c444297e69a71871f962d4b741ec1914342e0d39"),
             "zipcpu_train": (PILOT.parent / REPOSITORIES["zipcpu_skidbuffer"] /
                              "rtl/skidbuffer.v",
                              "ed1fda9192563fd7fd7033b564e8cf47ad6cb63ba4f578ed2c302037d47a5389")}
    loaded = {}
    for name, (path, expected) in roots.items():
        if (path.is_symlink() or not path.is_file() or
                hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            raise ValueError(name + ":pinned source copy drift")
        loaded[name] = {"sha": expected, "lines": _normalized_lines(path.read_text())}
    saved_libsv = json.loads((PILOT / "source-locks/libsv-train-lineage-v1.json").read_bytes())
    comparisons = []
    target = loaded["libsv_target"]
    for name in ("axis_train", "zipcpu_train"):
        source = loaded[name]
        comparisons.append({"train": name, "train_sha256": source["sha"],
                            "exact_file_duplicate": target["sha"] == source["sha"],
                            "shared_normalized_8_line_windows": len(
                                _windows(target["lines"]) & _windows(source["lines"])),
                            "target_substantive_lines": len(target["lines"]),
                            "train_substantive_lines": len(source["lines"])})
    if (saved_libsv.get("schema") != "r5-libsv-bounded-source-copy-screen-v1" or
            saved_libsv.get("scope") !=
            "only_pinned_gen4_train_source_files_vs_libsv_target" or
            saved_libsv.get("limit") !=
            "not proof against hidden rewrites, external generators, or shared concepts" or
            saved_libsv.get("target_sha256") != LIBSV_SHA["clean"] or
            saved_libsv.get("comparisons") != comparisons or
            any(row["exact_file_duplicate"] or row["shared_normalized_8_line_windows"]
                for row in comparisons)):
        raise ValueError("LibSV bounded source-copy screen drift")
    return {"old_digest": old["audit_digest"],
            "libsv_digest": _digest(saved_libsv),
            "bounded_source_copy_only": True}


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
        raise ValueError("v7 TRAIN source rollback receipt does not cold replay")
    return {"version": ROLLBACK_VERSION, "verified": True,
            "role": rollback.ROLE, "receipt_digest": fresh["digest"],
            "receipt_sha256": rollback.runner._sha(saved),
            "cases": list(CASE_ORDER),
            "shared_contract_digest": _digest(MEASUREMENT_CONTRACT),
            "source_restored_before_native_execution": True,
            "memory_mremove": False}


def _oracle_for_libsv(raw: Mapping, arm: str):
    if arm not in {"target", "preservation"} or raw.get("raw_six_arm_valid") is not True:
        raise ValueError("LibSV raw oracle unavailable")
    expected = raw["observed"]["private-clean-" + arm]
    if expected["verdict"] != "PASS":
        raise ValueError("LibSV raw clean oracle not PASS")

    def check(candidate: str, _asset: Mapping) -> dict:
        return {"verdict": "PASS" if hashlib.sha256(candidate.encode()).hexdigest() ==
                LIBSV_SHA["clean"] else "FAIL"}

    return check


def verify_train_asset_bundle(
    asset: Mapping, validation_receipts: Sequence[Mapping],
    bindings: Sequence[Mapping], rollback_receipt: Mapping | None,
) -> R5V7AssetEvidence:
    def empty(reason: str) -> R5V7AssetEvidence:
        return R5V7AssetEvidence(False, (), False, False, False,
                                 (reason,), "", "", "")

    if not is_skid_v7_asset(asset):
        return empty("not_r5_skid_v7_asset")
    if len(validation_receipts) != 3 or len(bindings) != 3:
        return empty("r5_v7_asset_evidence_cardinality")
    if asset.get("compatibility", {}).get("compatibility_profile") != PROFILE:
        return empty("r5_v7_asset_scope_mismatch")
    reasons = []
    audit = _lineage_audit()
    raw = verify_libsv_raw()
    lineages = []
    witnesses = set()
    contract_digest = _digest(MEASUREMENT_CONTRACT)
    for index, case in enumerate(CASE_ORDER):
        if case == "libsv_train":
            source = (LIBSV_RUN / "evaluator-inputs/source/fault/skid_buffer.sv").read_text()
            context = binder.LIBSV_CONTEXT
            design = case
            expected_sha = LIBSV_SHA["clean"]
            target_oracle = _oracle_for_libsv(raw, "target")
            preservation_oracle = _oracle_for_libsv(raw, "preservation")
            source_receipt = raw["receipt_sha256"]
        else:
            checked = old_train.verify_acquisition(old_train.acquisition(case, "treatment"))
            record = old_train.build_record(old_train.acquisition(case, "treatment"))
            if old_train.replay_record(record) != record.verification[
                    "scoped_execution"]["pair_receipt"]:
                reasons.append(case + ":canonical_record_drift")
            old_witness = record.verification["scoped_execution"]["pair_receipt"][
                "after"]["oracle_instance_witness"]
            if (old_witness["shared_contract_digest"] !=
                    old_train._digest(old_train.MEASUREMENT_CONTRACT) or
                    old_witness["repository"] != checked["repository"] or
                    old_witness["train_receipt_digest"] != checked["train_receipt_digest"]):
                reasons.append(case + ":raw_oracle_witness_drift")
            source = old_train._source(checked)
            context = checked["public_context"]
            design = checked["design"]
            expected_sha = checked["after_source_sha256"].removeprefix("sha256:")
            source_receipt = checked["train_receipt_digest"]
            target_oracle = old_train.oracle_for(checked, record, "target")
            preservation_oracle = old_train.oracle_for(checked, record, "preservation")
        witness = _digest({"case": case, "repository": REPOSITORIES[case],
                           "source_receipt": source_receipt,
                           "contract_digest": contract_digest})
        witnesses.add(witness)
        try:
            payload = payload_from_source_v7(source, context)
            edited, action = apply_skid_payload_action_v7(source, payload)
            if (hashlib.sha256(edited.encode()).hexdigest() != expected_sha or
                    action.get("rewritten") != 1 or
                    action.get("source_binding_rederived") is not True):
                raise ValueError("v7 candidate drift")
            expected_bound = bind_rtl_asset_to_source(
                asset, source, design_id=design, public_context=context)
            bound_ok = stable_dumps(dict(bindings[index])) == stable_dumps(expected_bound)
        except (TypeError, ValueError, KeyError):
            bound_ok = False
            expected_bound = None
        if not bound_ok:
            reasons.append(case + ":binding_not_v7_source_replayed")
        if expected_bound is not None:
            expected_validation = validate_rtl_rewrite_asset(
                expected_bound, source, verifier=target_oracle,
                regression_verifier=preservation_oracle).to_dict()
            validation_ok = (stable_dumps(dict(validation_receipts[index])) ==
                             stable_dumps(expected_validation) and
                             expected_validation["independent_verifier"] is True and
                             expected_validation["oracle_verdict"] == "PASS" and
                             expected_validation["regression_verdict"] == "PASS" and
                             not expected_validation["errors"])
        else:
            validation_ok = False
        if not validation_ok:
            reasons.append(case + ":validation_not_raw_oracle_replayed")
        if bound_ok and validation_ok:
            lineages.append(REPOSITORIES[case])
    if len(witnesses) != 3:
        reasons.append("r5_v7_oracle_instance_witnesses_not_distinct")
    rollback_digest = ""
    rollback_ok = False
    try:
        expected = expected_rollback_binding()
        rollback_digest = expected["receipt_digest"]
        rollback_ok = (isinstance(rollback_receipt, Mapping) and
                       stable_dumps(dict(rollback_receipt)) == stable_dumps(expected))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        reasons.append("r5_v7_rollback_raw_replay_failed")
    if not rollback_ok:
        reasons.append("r5_v7_rollback_binding_mismatch")
    valid = not reasons and len(set(lineages)) == 3 and rollback_ok
    return R5V7AssetEvidence(valid, tuple(sorted(set(lineages))) if valid else (),
                             rollback_ok, all("validation_not" not in item for item in reasons),
                             all("binding_not" not in item for item in reasons),
                             tuple(sorted(set(reasons))), _digest(audit),
                             rollback_digest, contract_digest)


def verify_train_row_metadata(validation_entries: Sequence[Mapping],
                              binding_entries: Sequence[Mapping]) -> bool:
    if len(validation_entries) != 3 or len(binding_entries) != 3:
        return False
    return all(row.get("split") == "training" and
               row.get("lineage_id") == REPOSITORIES[case] and
               row.get("source_id") == case
               for index, case in enumerate(CASE_ORDER)
               for row in (validation_entries[index], binding_entries[index]))


__all__ = ["EVIDENCE_VERSION", "ROLLBACK_VERSION", "CASE_ORDER", "REPOSITORIES",
           "MEASUREMENT_CONTRACT", "R5V7AssetEvidence", "expected_rollback_binding",
           "verify_train_asset_bundle", "verify_train_row_metadata"]
