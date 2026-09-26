"""Generation-5 canonical TRAIN records for the three-source v7 action.

AXIS/ZipCPU reuse their frozen acquisitions. LibSV uses the explicitly
re-registered six-arm TRAIN run. Every consumer replays raw evidence; no
stored record or caller admission flag grants learner authority by itself.
"""
from __future__ import annotations

import copy
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import asdict

from tehm.adapters import research_r5_rtl_scoped_v3 as old
from tehm.assets import r5_libsv_train_raw_v7 as libsv
from tehm.assets import r5_train_evidence_v7 as evidence
from tehm.canonical.capture import ExecutionRecord
from tehm.evaluation.research_r5_skid_binding_v7 import LIBSV_CONTEXT
from tehm.rtl.skid_payload_action_v7 import (
    DOMAIN, PROFILE, apply_skid_payload_action_v7, payload_from_source_v7,
)

ACQUISITION_VERSION = "tehm-r5-rtl-train-acquisition-v5"
SCOPED_VERSION = "tehm-r5-rtl-train-scoped-v5"
CAMPAIGN = "r5-rtl-skid-train-generation-5"
FAMILY = old.FAMILY
CASES = {**old.CASES, "libsv_train": {
    "repository": libsv.REPOSITORY, "design": "libsv_train",
    "source": "libsv/fifos/skid_buffer.sv",
    "train_digest": "sha256:1ce5ca0b0a492a407b558f9a7dee19aaf45114d9785cd1f30f5b339a898119fb",
    "oracle_kind": "libsv_private_handshake_target_and_direct_preservation_v1",
    "target": "second_buffered_payload_delivered_on_poststall_handshake",
    "preservation": "three_direct_payload_handshakes_without_buffer_entry",
}}
MEASUREMENT_CONTRACT = copy.deepcopy(evidence.MEASUREMENT_CONTRACT)
LINEAGE_DIGEST = "sha256:f0b2ff2ea3d805c65f947c739dc8c5dcff13c4547454edfe3e50d9f8a470845d"
_digest = old._digest
_sha = old._sha


def acquisition(case_id: str, role: str) -> dict:
    if case_id not in CASES or role not in {"control", "treatment"}:
        raise ValueError("unsupported generation-5 TRAIN case or role")
    return {"version": ACQUISITION_VERSION, "case_id": case_id,
            "role": role, "campaign_id": CAMPAIGN,
            "expected_train_digest": CASES[case_id]["train_digest"],
            "expected_lineage_digest": LINEAGE_DIGEST}


def _source(checked: Mapping) -> str:
    if checked["case_id"] != "libsv_train":
        return old._source(checked)
    path = libsv.RUN / "evaluator-inputs/source/fault/skid_buffer.sv"
    data = path.read_bytes()
    if path.is_symlink() or _sha(data) != checked["before_source_sha256"]:
        raise ValueError("generation-5 LibSV fault source drift")
    return data.decode("utf-8")


def verify_acquisition(raw: Mapping) -> dict:
    if not isinstance(raw, Mapping):
        raise ValueError("generation-5 acquisition must be a mapping")
    case_id, role = raw.get("case_id"), raw.get("role")
    if (case_id not in CASES or role not in {"control", "treatment"} or
            dict(raw) != acquisition(case_id, role)):
        raise ValueError("generation-5 acquisition identity or role drift")
    audit = evidence._lineage_audit()
    if _digest(audit) != LINEAGE_DIGEST:
        raise ValueError("generation-5 bounded source-group audit drift")
    if case_id != "libsv_train":
        checked = old.verify_acquisition(old.acquisition(case_id, role))
    else:
        result = libsv.verify()
        case = CASES[case_id]
        if "sha256:" + result["receipt_sha256"] != case["train_digest"]:
            raise ValueError("generation-5 LibSV frozen TRAIN receipt drift")
        checked = {
            "case_id": case_id, "role": role, "work": str(libsv.TRAIN),
            "repository": case["repository"], "design": case["design"],
            "source_file": case["source"],
            "source_git_sha": "c5aff5decba04e7290db6babb122cc1a5d9460ed",
            "public_context": dict(LIBSV_CONTEXT),
            "before_source_sha256": "sha256:" + result["source_sha256"]["fault"],
            "after_source_sha256": "sha256:" + result["source_sha256"]["clean"],
            "train_receipt_digest": case["train_digest"],
            "train_receipt_sha256": "sha256:" + result["receipt_sha256"],
            "preregistration_sha256": "sha256:" + result["preregistration_sha256"],
            "oracle_kind": case["oracle_kind"], "target_obligation": case["target"],
            "preservation_obligation": case["preservation"], "raw": result,
        }
    source = _source(checked)
    payload = payload_from_source_v7(source, checked["public_context"])
    candidate, action = apply_skid_payload_action_v7(source, payload)
    if (_sha(candidate.encode()) != checked["after_source_sha256"] or
            action.get("rewritten") != 1 or
            action.get("source_binding_rederived") is not True):
        raise ValueError("generation-5 v7 candidate replay mismatch")
    return {**checked, "action_payload": payload, "action_receipt": action,
            "lineage_receipt_digest": LINEAGE_DIGEST,
            "measurement_contract_digest": _digest(MEASUREMENT_CONTRACT)}


def _instance_witness(checked: Mapping) -> dict:
    if checked["case_id"] != "libsv_train":
        witness = old.v2._instance_witness(checked)
    else:
        target = libsv.RUN / "evaluator-inputs/tb/tb_target.sv"
        preservation = libsv.RUN / "evaluator-inputs/tb/tb_preservation.sv"
        anchors = {"output_handshake": "if (valid_o && ready_i) begin",
                   "payload_check": "if (data_o !== expected[received])",
                   "completion": "if (accepted !== 2 || received !== 2)",
                   "second_payload": "expected[1] = 32'h000000B2;",
                   "backpressure_release": "ready_i = 1'b1;"}
        if (any(anchor not in target.read_text() for anchor in anchors.values()) or
                "PRESERVATION_ENTERED_BUFFERED_PATH" not in preservation.read_text()):
            raise ValueError("LibSV TRAIN semantic mapping anchor missing")
        witness = {
            "role": "TRAIN_REUSED_DEV_RESEARCHER_ASSISTED", "case_id": "libsv_train",
            "repository": checked["repository"], "source_git_sha": checked["source_git_sha"],
            "source_file": checked["source_file"],
            "source_before_sha256": checked["before_source_sha256"],
            "source_after_sha256": checked["after_source_sha256"],
            "oracle_kind": checked["oracle_kind"],
            "oracle_origin": "research_augmented_evaluator_private_with_native_sensitivity",
            "oracle_source_sha256": _sha(target.read_bytes()),
            "preservation_source_sha256": _sha(preservation.read_bytes()),
            "target_ids": ["private-target"], "preservation_ids": ["private-preservation"],
            "preregistered_target_obligation": checked["target_obligation"],
            "preregistered_preservation_obligation": checked["preservation_obligation"],
            "canonical_target_summary": checked["target_obligation"],
            "canonical_preservation_summary": checked["preservation_obligation"],
            "semantic_mapping": "buffered_payload_value_after_backpressure",
            "semantic_mapping_anchors": anchors,
            "preregistration_sha256": checked["preregistration_sha256"],
            "train_receipt_digest": checked["train_receipt_digest"],
            "train_receipt_sha256": checked["train_receipt_sha256"],
            "native_sensitivity": {name: checked["raw"]["observed"][name]
                                   for name in ("native-clean", "native-fault")},
            "native_oracle_equivalence": False, "unseen_transfer": False,
        }
    witness.update(version="tehm-r5-oracle-instance-witness-v5",
                   lineage_receipt_digest=LINEAGE_DIGEST,
                   source_group_claim="bounded_pinned_source_groups_only",
                   shared_contract_digest=_digest(MEASUREMENT_CONTRACT))
    witness.pop("witness_digest", None)
    witness["witness_digest"] = _digest(witness)
    return witness


def _arm_result(checked: dict, arm: str) -> dict:
    if checked["case_id"] != "libsv_train":
        return old.v1._arm_result(checked, arm)
    prefix = "private-fault-" if arm == "fault" else "private-clean-"
    target = checked["raw"]["observed"][prefix + "target"]["verdict"]
    preservation = checked["raw"]["observed"][prefix + "preservation"]["verdict"]
    return {"verdict": "PASS" if target == preservation == "PASS" else "FAIL",
            "target_verdict": target, "preservation_verdict": preservation,
            "test_count": 2, "failed_count": int(target == "FAIL") + int(preservation == "FAIL")}


def build_record(raw: Mapping) -> ExecutionRecord:
    checked = verify_acquisition(raw)
    case_id, role = checked["case_id"], checked["role"]
    baseline, repaired = _arm_result(checked, "fault"), _arm_result(checked, "candidate")
    if (baseline["target_verdict"] != "FAIL" or baseline["preservation_verdict"] != "PASS" or
            repaired["target_verdict"] != "PASS" or repaired["preservation_verdict"] != "PASS"):
        raise ValueError("generation-5 raw target/preservation pair incomplete")
    contract = copy.deepcopy(MEASUREMENT_CONTRACT)
    digest = _digest(contract)
    witness = _instance_witness(checked)
    pair = {"contract": contract, "contract_digest": digest,
            "controlled_measurement_valid": True, "native_oracle_equivalence": False}
    for side, result in (("before", baseline), ("after", repaired)):
        pair[side] = {"contract": contract, "oracle_instance": checked["oracle_kind"],
                      **{key: result[key] for key in (
                          "verdict", "target_verdict", "preservation_verdict")},
                      "source_sha256": checked[side + "_source_sha256"],
                      "train_receipt_digest": checked["train_receipt_digest"],
                      "oracle_instance_witness": copy.deepcopy(witness)}
    pair["receipt_digest"] = _digest(pair)
    scoped = {"version": SCOPED_VERSION, "role": "before" if role == "control" else "after",
              "case_id": case_id, "acquisition_digest": _digest(dict(raw)),
              "train_receipt_digest": checked["train_receipt_digest"],
              "lineage_receipt_digest": LINEAGE_DIGEST, "pair_receipt": pair,
              "oracle_instance_witness_digest": witness["witness_digest"]}
    run_tag = "r5-train-v5-" + case_id + "-fault-" + checked["train_receipt_digest"][-12:]
    config = {"public_context": checked["public_context"], "compatibility_profile": PROFILE,
              "source_file": checked["source_file"]}

    def state(side: str, result: dict) -> dict:
        return {"config": copy.deepcopy(config), "reports": {"target_oracle": {
            "scope": PROFILE, "oracle_kind": checked["oracle_kind"],
            **{key: result[key] for key in ("verdict", "target_verdict", "preservation_verdict")},
            "provenance": {"run_tag": run_tag + ("-candidate" if side == "after" else "")}}},
            "artifacts": {"rtl_source_sha256": checked[side + "_source_sha256"],
                          "train_receipt_digest": checked["train_receipt_digest"],
                          "preregistration_sha256": checked["preregistration_sha256"]}}

    before = state("before", baseline)
    before["failure_signature"] = {"mechanism_family": FAMILY,
                                   "failure": "buffered_payload_source_mismatch"}
    is_control = role == "control"
    after_result = baseline if is_control else repaired
    after = copy.deepcopy(before) if is_control else state("after", repaired)
    action = {"domain": "rtl.BASELINE_CONTROL" if is_control else DOMAIN,
              "transformation_family": FAMILY,
              "payload": {"control": True, "observation_only": True,
                          "compatibility_profile": PROFILE,
                          "measurement_contract_digest": digest}
              if is_control else checked["action_payload"]}
    record = ExecutionRecord(
        record_id="r5-rtl-train-v5:" + _digest(scoped).split(":", 1)[1],
        domain="rtl.functional", project_id=checked["repository"],
        design_id=checked["design"], lineage_id=checked["repository"],
        repository_ref=checked["repository"] + "@" + checked["source_git_sha"],
        before=before, after=after, action=action,
        observation_delta={"original_failure": "PRESENT" if is_control else "REMOVED",
                           "failing_tests": {"before": baseline["failed_count"],
                                             "after": after_result["failed_count"]},
                           "created_regressions": [], "newly_observed_failures": [],
                           "experiment_kind": "OBSERVATION" if is_control else "REPAIR",
                           "utility_verdict": "UNKNOWN"},
        verification={"verdict": after_result["verdict"], "oracle_type": "TARGET_TEST",
                      "scope": PROFILE, "confidence_tier": "T", "oracle_complete": True,
                      "obligation_coverage": 1.0, "extractor_version": SCOPED_VERSION,
                      "evidence_refs": [checked["train_receipt_digest"],
                                        checked["preregistration_sha256"], LINEAGE_DIGEST,
                                        witness["witness_digest"]],
                      "tool_versions": {"source_group": checked["repository"],
                                        "train_receipt_sha256": checked["train_receipt_sha256"]},
                      "full_oracle": {"before": {"complete": True, **baseline},
                                      "after": {"complete": True, **after_result}},
                      "scoped_execution": scoped})
    record.validate()
    return record


def replay_record(record: ExecutionRecord) -> dict:
    record.validate()
    scoped = record.verification.get("scoped_execution")
    if not isinstance(scoped, Mapping) or scoped.get("version") != SCOPED_VERSION:
        raise ValueError("unsupported generation-5 scoped record")
    expected = build_record(acquisition(scoped.get("case_id"),
                            "control" if scoped.get("role") == "before" else "treatment"))
    if asdict(record) != asdict(expected):
        raise ValueError("generation-5 canonical record differs from raw TRAIN evidence")
    return expected.verification["scoped_execution"]["pair_receipt"]


def replay_persisted_rtl_train_v5(conn: sqlite3.Connection, transition_id: str,
                                 *, acquisition_data: Mapping) -> dict:
    from tehm.adapters.orfs_scoped import _compare_persisted_flow_record
    from tehm.causal.mechanism import load_transition_facts

    facts = load_transition_facts(conn, transition_id)
    scoped = facts.verifier.get("scoped_execution")
    if not isinstance(scoped, dict) or scoped.get("version") != SCOPED_VERSION:
        raise ValueError("transition lacks generation-5 scoped execution")
    _compare_persisted_flow_record(conn, transition_id, build_record(acquisition_data))
    return {"version": SCOPED_VERSION, "transition_id": transition_id,
            "acquisition_digest": _digest(dict(acquisition_data)),
            "persisted_binding_verified": True, "learner_admission": False,
            "promotion_attempted": False}


def oracle_for(checked: Mapping, record: ExecutionRecord, arm: str):
    if arm not in {"target", "preservation"}:
        raise ValueError("unsupported generation-5 oracle arm")
    witness = record.verification["scoped_execution"]["pair_receipt"]["after"]["oracle_instance_witness"]
    if (witness["source_after_sha256"] != checked["after_source_sha256"] or
            witness["train_receipt_digest"] != checked["train_receipt_digest"] or
            witness["shared_contract_digest"] != _digest(MEASUREMENT_CONTRACT)):
        raise ValueError("generation-5 oracle instance mismatch")
    expected = record.verification["full_oracle"]["after"]

    def check(candidate: str, _asset: Mapping) -> dict:
        return {"verdict": "PASS" if _sha(candidate.encode()) == checked["after_source_sha256"] and
                expected["complete"] is True and expected[arm + "_verdict"] == "PASS" else "FAIL"}

    return check


__all__ = ["ACQUISITION_VERSION", "SCOPED_VERSION", "CAMPAIGN", "FAMILY", "PROFILE",
           "CASES", "MEASUREMENT_CONTRACT", "acquisition", "verify_acquisition",
           "build_record", "replay_record", "replay_persisted_rtl_train_v5", "oracle_for"]
