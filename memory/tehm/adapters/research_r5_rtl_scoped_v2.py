"""R5 generation-2 TRAIN replay with shared obligation and distinct oracle witnesses.

This adapter only replays the two already-registered, researcher-assisted TRAIN
executions. It does not certify another oracle instance, a held-out target, or
production eligibility. A new target needs its own evaluator-side witness.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path

from tehm.adapters import research_r5_rtl_scoped as v1
from tehm.canonical.capture import ExecutionRecord
from tehm.ids import stable_dumps
from tehm.rtl.skid_payload_action_v4 import (
    DOMAIN, PROFILE, apply_skid_payload_action_v4, payload_from_source_v4,
)


ACQUISITION_VERSION = "tehm-r5-rtl-train-acquisition-v2"
SCOPED_VERSION = "tehm-r5-rtl-train-scoped-v2"
CAMPAIGN = "r5-rtl-skid-train-generation-2"
FAMILY = v1.FAMILY
CASES = v1.CASES
MEASUREMENT_CONTRACT = {
    "version": "tehm-r5-skid-payload-measurement-v2",
    "scope": PROFILE,
    "mechanism_family": FAMILY,
    "common_obligation": "one_buffered_payload_is_delivered_intact_after_backpressure",
    "preservation_required": True,
    "per_task_oracle_instance_witness_required": True,
    "native_oracle_equivalence": False,
    "heldout_target_claim": False,
    "production_authority": False,
}


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def acquisition(case_id: str, role: str) -> dict:
    original = v1.acquisition(case_id, role)
    return {**original, "version": ACQUISITION_VERSION,
            "campaign_id": CAMPAIGN}


def verify_acquisition(raw: Mapping) -> dict:
    if not isinstance(raw, Mapping):
        raise ValueError("R5 v2 acquisition must be a mapping")
    case_id, role = raw.get("case_id"), raw.get("role")
    if (case_id not in CASES or role not in {"control", "treatment"} or
            dict(raw) != acquisition(case_id, role)):
        raise ValueError("R5 v2 TRAIN acquisition identity or lock drift")
    return v1.verify_acquisition(v1.acquisition(case_id, role))


def _source(checked: Mapping) -> str:
    work = Path(checked["work"])
    source_file = checked["source_file"]
    case_id = checked["case_id"]
    path = (work / "fault" / "stage" / source_file if case_id == "axis_register"
            else work / "fault" / "backpressure" / "stage" / source_file)
    data = path.read_bytes()
    if _sha(data) != checked["before_source_sha256"]:
        raise ValueError("R5 v2 TRAIN source identity drift")
    return data.decode("utf-8")


def _instance_witness(checked: Mapping) -> dict:
    """Construct a source-specific witness only after cold raw TRAIN replay."""
    work = Path(checked["work"])
    prereg_data = (work / "preregistration.json").read_bytes()
    if _sha(prereg_data) != checked["preregistration_sha256"]:
        raise ValueError("R5 v2 TRAIN preregistration drift")
    prereg = json.loads(prereg_data)
    case_id = checked["case_id"]
    if case_id == "axis_register":
        from tehm.evaluation import research_r5_train_axis as axis
        target_ids = list(axis.TARGET_IDS)
        preservation_ids = list(axis.PRESERVATION_IDS)
        if (prereg.get("target_ids") != target_ids or
                prereg.get("preservation_ids") != preservation_ids or
                prereg.get("oracle") != checked["oracle_kind"] or
                not prereg.get("native_test_sha256")):
            raise ValueError("R5 v2 axis oracle instance drift")
        oracle_source_sha256 = prereg["native_test_sha256"]
        oracle_origin = "upstream_native_cocotb_junit"
        oracle_source_path = axis.CORPUS / prereg["source_repository"] / prereg["native_test"]
        mapping_anchors = {
            "backpressure_hook": "tb.set_backpressure_generator(backpressure_inserter)",
            "generated_backpressure_case": 'factory.add_option("backpressure_inserter", [None, cycle_pause])',
            "ordered_payload_assertion": "assert rx_frame.tdata == test_frame.tdata",
            "ordered_metadata_assertion": "assert rx_frame.tid == test_frame.tid",
        }
    else:
        target_ids = ["backpressure"]
        preservation_ids = ["direct"]
        if (prereg.get("target_obligation") != "backpressure_payload_order_five_beats" or
                prereg.get("preservation_obligation") != "direct_payload_order_five_beats" or
                prereg.get("oracle_kind") != checked["oracle_kind"] or
                not prereg.get("private_testbench_sha256")):
            raise ValueError("R5 v2 ZipCPU oracle instance drift")
        oracle_source_sha256 = prereg["private_testbench_sha256"]
        oracle_origin = "research_augmented_evaluator_private_not_native"
        oracle_source_path = Path(prereg["private_testbench"])
        mapping_anchors = {
            "backpressure_schedule": "i_ready = (BACKPRESSURE == 0) || (cycle == 0) || (cycle >= 3);",
            "ordered_payload_assertion": "if (o_data !== expected[received]) begin",
            "ordered_beat_index": "expected[sent] = i_data;",
            "completion_count": "if (received == N) begin",
        }
    if oracle_source_path.is_symlink() or not oracle_source_path.is_file():
        raise ValueError("R5 v2 TRAIN oracle source missing or linked")
    oracle_source_bytes = oracle_source_path.read_bytes()
    if _sha(oracle_source_bytes) != oracle_source_sha256:
        raise ValueError("R5 v2 TRAIN oracle source hash drift")
    oracle_source_text = oracle_source_bytes.decode("utf-8")
    if any(anchor not in oracle_source_text for anchor in mapping_anchors.values()):
        raise ValueError("R5 v2 TRAIN semantic mapping anchor missing")
    witness = {
        "version": "tehm-r5-oracle-instance-witness-v2",
        "role": "TRAIN_REUSED_DEV_RESEARCHER_ASSISTED",
        "case_id": case_id,
        "repository": checked["repository"],
        "source_git_sha": checked["source_git_sha"],
        "source_file": checked["source_file"],
        "source_before_sha256": checked["before_source_sha256"],
        "source_after_sha256": checked["after_source_sha256"],
        "oracle_kind": checked["oracle_kind"],
        "oracle_origin": oracle_origin,
        "oracle_source_sha256": oracle_source_sha256,
        "target_ids": target_ids,
        "preservation_ids": preservation_ids,
        "preregistered_target_obligation": prereg.get(
            "target_obligation", checked["target_obligation"]),
        "preregistered_preservation_obligation": prereg.get(
            "preservation_obligation", checked["preservation_obligation"]),
        "canonical_target_summary": checked["target_obligation"],
        "canonical_preservation_summary": checked["preservation_obligation"],
        "semantic_mapping": "buffered_payload_value_after_backpressure",
        "semantic_mapping_anchors": mapping_anchors,
        "preregistration_sha256": checked["preregistration_sha256"],
        "train_receipt_digest": checked["train_receipt_digest"],
        "train_receipt_sha256": checked["train_receipt_sha256"],
        "lineage_receipt_digest": checked["lineage_receipt_digest"],
        "shared_contract_digest": _digest(MEASUREMENT_CONTRACT),
        "native_oracle_equivalence": False,
        "unseen_transfer": False,
    }
    witness["witness_digest"] = _digest(witness)
    return witness


def build_record(raw: Mapping) -> ExecutionRecord:
    checked = verify_acquisition(raw)
    original = v1.build_record(v1.acquisition(checked["case_id"], checked["role"]))
    record = copy.deepcopy(original)
    witness = _instance_witness(checked)
    source = _source(checked)
    payload = payload_from_source_v4(source, checked["public_context"])
    candidate, action_receipt = apply_skid_payload_action_v4(source, payload)
    if (_sha(candidate.encode("utf-8")) != checked["after_source_sha256"] or
            action_receipt.get("rewritten") != 1):
        raise ValueError("R5 v2 v4 action does not replay TRAIN candidate")

    contract = copy.deepcopy(MEASUREMENT_CONTRACT)
    digest = _digest(contract)
    scoped = record.verification["scoped_execution"]
    pair = scoped["pair_receipt"]
    pair["contract"] = contract
    pair["contract_digest"] = digest
    for side in ("before", "after"):
        pair[side]["contract"] = contract
        pair[side]["oracle_instance_witness"] = witness
    pair["receipt_digest"] = _digest({key: value for key, value in pair.items()
                                      if key != "receipt_digest"})
    scoped["version"] = SCOPED_VERSION
    scoped["acquisition_digest"] = _digest(dict(raw))
    scoped["oracle_instance_witness_digest"] = witness["witness_digest"]
    record.record_id = "r5-rtl-train-v2:" + _digest(scoped).split(":", 1)[1]
    for state in (record.before, record.after):
        if isinstance(state.get("config"), dict):
            state["config"]["compatibility_profile"] = PROFILE
        state["reports"]["target_oracle"]["scope"] = PROFILE
    if checked["role"] == "control":
        record.action["payload"]["compatibility_profile"] = PROFILE
        record.action["payload"]["measurement_contract_digest"] = digest
    else:
        record.action = {"domain": DOMAIN,
                         "transformation_family": FAMILY,
                         "payload": payload}
    record.verification["scope"] = PROFILE
    record.verification["extractor_version"] = SCOPED_VERSION
    record.verification["evidence_refs"].append(witness["witness_digest"])
    record.validate()
    return record


def replay_record(record: ExecutionRecord) -> dict:
    record.validate()
    scoped = record.verification.get("scoped_execution")
    if not isinstance(scoped, Mapping) or scoped.get("version") != SCOPED_VERSION:
        raise ValueError("unsupported R5 v2 scoped record")
    case_id, role = scoped.get("case_id"), scoped.get("role")
    raw = acquisition(case_id, "control" if role == "before" else "treatment")
    expected = build_record(raw)
    if asdict(record) != asdict(expected):
        raise ValueError("R5 v2 canonical record differs from raw TRAIN acquisition")
    return expected.verification["scoped_execution"]["pair_receipt"]


def replay_persisted_rtl_train_v2(conn: sqlite3.Connection, transition_id: str,
                                  *, acquisition_data: Mapping) -> dict:
    from tehm.adapters.orfs_scoped import _compare_persisted_flow_record
    from tehm.causal.mechanism import load_transition_facts

    facts = load_transition_facts(conn, transition_id)
    scoped = facts.verifier.get("scoped_execution")
    if not isinstance(scoped, dict) or scoped.get("version") != SCOPED_VERSION:
        raise ValueError("transition lacks R5 v2 RTL scoped execution")
    record = build_record(acquisition_data)
    _compare_persisted_flow_record(conn, transition_id, record)
    return {"version": SCOPED_VERSION, "transition_id": transition_id,
            "acquisition_digest": _digest(dict(acquisition_data)),
            "persisted_binding_verified": True, "learner_admission": False,
            "promotion_attempted": False}


__all__ = ["ACQUISITION_VERSION", "SCOPED_VERSION", "CAMPAIGN", "FAMILY",
           "CASES", "MEASUREMENT_CONTRACT", "acquisition", "verify_acquisition",
           "build_record", "replay_record", "replay_persisted_rtl_train_v2"]
