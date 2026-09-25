"""Source-replayed, evaluator-only canonical records from two R5 RTL TRAIN runs.

The acquisition is frozen by the caller; no producer-controlled ``admitted``
field or persisted PASS can bypass raw TRAIN and lineage replay.  This module
does not grant Knowledge/Asset status or production authority.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Mapping

from tehm.canonical.capture import ExecutionRecord
from tehm.ids import stable_dumps
from tehm.evaluation import research_r5_train_axis as axis
from tehm.evaluation import research_r5_train_zipcpu as zipcpu
from tehm.evaluation import research_r5_train_lineage_audit as lineage
from tehm.rtl.skid_payload_action_v3 import (
    DOMAIN, PROFILE, apply_skid_payload_action_v3, payload_from_source_v3,
)


ACQUISITION_VERSION = "tehm-r5-rtl-train-acquisition-v1"
SCOPED_VERSION = "tehm-r5-rtl-train-scoped-v1"
FAMILY = "SKID_TEMP_PAYLOAD_RESTORE"
CAMPAIGN = "r5-rtl-skid-train-generation-1"
LINEAGE_RECEIPT = (axis.CORPUS / "_r5_pilot" / "source-locks" /
                   "train-lineage-v1.json")
LINEAGE_DIGEST = "sha256:ec0f9b1d9ac4e68a097e668f6a5245cd0504c5d4b13f4f58263a049396d1dfe2"
CASES = {
    "axis_register": {
        "module": axis, "work": "axis-register-skid-train-r1",
        "repository": "alexforencich/verilog-axis", "design": "axis_register",
        "source": "rtl/axis_register.v",
        "train_digest": "sha256:731625b13c1aba1a8fdd8450e9ce0c24e4274815b88d7bd5c39507576618d711",
        "oracle_kind": "native_cocotb_junit_v2",
        "target": "2_preregistered_payload_cases",
        "preservation": "7_preregistered_non_target_cases",
    },
    "zipcpu_skidbuffer": {
        "module": zipcpu, "work": "zipcpu-skid-payload-train-r1",
        "repository": "ZipCPU/wb2axip", "design": "skidbuffer",
        "source": "rtl/skidbuffer.v",
        "train_digest": "sha256:bc312de489ad101d93d630ea890a16069667369c68248019f4626e6e645564d6",
        "oracle_kind": "research_augmented_evaluator_private_not_native",
        "target": "backpressure_five_beat_payload_order",
        "preservation": "direct_five_beat_payload_order",
    },
}

# The common claim is a semantic obligation, not a claim that the two test
# implementations or denominator sizes are identical.  Each instance is
# replayed and reported separately in the pair receipt.
MEASUREMENT_CONTRACT = {
    "version": "tehm-r5-skid-payload-measurement-v1",
    "scope": "rtl.skid.temp_payload.v3.dev",
    "common_obligation": "buffered_payload_reappears_in_order_after_backpressure",
    "source_specific_oracles": {
        "axis_register": {
            "kind": CASES["axis_register"]["oracle_kind"],
            "target": CASES["axis_register"]["target"],
            "preservation": CASES["axis_register"]["preservation"],
        },
        "zipcpu_skidbuffer": {
            "kind": CASES["zipcpu_skidbuffer"]["oracle_kind"],
            "target": CASES["zipcpu_skidbuffer"]["target"],
            "preservation": CASES["zipcpu_skidbuffer"]["preservation"],
        },
    },
    "native_oracle_equivalence": False,
    "heldout_target_claim": False,
    "production_authority": False,
}


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def acquisition(case_id: str, role: str) -> dict:
    if case_id not in CASES or role not in {"control", "treatment"}:
        raise ValueError("unsupported RTL TRAIN acquisition case or role")
    return {"version": ACQUISITION_VERSION, "case_id": case_id,
            "role": role, "campaign_id": CAMPAIGN,
            "expected_train_digest": CASES[case_id]["train_digest"],
            "expected_lineage_digest": LINEAGE_DIGEST}


def _cold_verify(module, work: Path) -> dict:
    # The frozen axis runner hashes its CLI __main__ module identity, so use
    # the exact validated entrypoint instead of importing its verify function.
    repo = Path(__file__).resolve().parents[3]
    env = {**os.environ, "PYTHONPATH": str(repo / "memory"),
           "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run(
        [sys.executable, "-m", module.__name__, "verify", "--work", str(work)],
        cwd=repo, env=env, capture_output=True, check=True, timeout=60)
    return json.loads(proc.stdout)


def verify_acquisition(raw: Mapping) -> dict:
    if not isinstance(raw, Mapping) or set(raw) != set(acquisition("axis_register", "control")):
        raise ValueError("RTL TRAIN acquisition fields are not exact")
    case_id, role = raw.get("case_id"), raw.get("role")
    if (case_id not in CASES or role not in {"control", "treatment"} or
            dict(raw) != acquisition(case_id, role)):
        raise ValueError("RTL TRAIN acquisition identity or lock drift")
    case = CASES[case_id]
    lineage_fresh = lineage.audit()
    lineage_saved = json.loads(LINEAGE_RECEIPT.read_bytes())
    if (lineage_fresh != lineage_saved or
            lineage_fresh.get("audit_digest") != LINEAGE_DIGEST or
            lineage_fresh.get("distinct_source_lineages_for_bounded_pilot") is not True):
        raise ValueError("RTL TRAIN bounded lineage receipt does not replay")
    work = axis.CORPUS / "_r5_pilot" / "training" / case["work"]
    fresh = _cold_verify(case["module"], work)
    saved = json.loads((work / "receipt.json").read_bytes())
    prereg = json.loads((work / "preregistration.json").read_bytes())
    if (fresh != saved or fresh.get("valid") is not True or
            fresh.get("digest") != case["train_digest"] or
            fresh.get("role") != "TRAIN_REUSED_DEV_RESEARCHER_ASSISTED" or
            prereg.get("role") != fresh["role"] or
            prereg.get("source_repository") != case["repository"] or
            prereg.get("unseen_transfer") is not False or
            prereg.get("production_authority") is not False or
            lineage_fresh["training"][case_id]["training_receipt_digest"] != fresh["digest"]):
        raise ValueError("RTL TRAIN raw execution or role does not replay")
    before = (work / "fault" / "stage" / case["source"]).read_bytes() if case_id == "axis_register" else (
        work / "fault" / "backpressure" / "stage" / case["source"]).read_bytes()
    after = (work / "candidate" / "stage" / case["source"]).read_bytes() if case_id == "axis_register" else (
        work / "candidate" / "backpressure" / "stage" / case["source"]).read_bytes()
    agent_file = work / "agent-inputs" / (case["source"] if case_id == "axis_register"
                                          else "skidbuffer.v")
    if before != agent_file.read_bytes():
        raise ValueError("RTL TRAIN staged fault differs from agent-visible source")
    context = prereg["public_context"]
    payload = payload_from_source_v3(before.decode("utf-8"), context)
    edited, action_receipt = apply_skid_payload_action_v3(before.decode("utf-8"), payload)
    if edited.encode("utf-8") != after or action_receipt.get("rewritten") != 1:
        raise ValueError("RTL TRAIN source-only action does not replay candidate")
    return {"case_id": case_id, "role": role, "work": str(work),
            "repository": case["repository"], "design": case["design"],
            "source_file": case["source"], "source_git_sha": prereg["source_git_sha"],
            "public_context": context, "before_source_sha256": _sha(before),
            "after_source_sha256": _sha(after), "action_payload": payload,
            "action_receipt": action_receipt,
            "train_receipt_digest": fresh["digest"],
            "train_receipt_sha256": _sha((work / "receipt.json").read_bytes()),
            "preregistration_sha256": _sha((work / "preregistration.json").read_bytes()),
            "lineage_receipt_digest": LINEAGE_DIGEST,
            "measurement_contract_digest": _digest(MEASUREMENT_CONTRACT),
            "oracle_kind": case["oracle_kind"],
            "target_obligation": case["target"],
            "preservation_obligation": case["preservation"],
            "raw": fresh}


def _arm_result(checked: dict, arm: str) -> dict:
    raw = checked["raw"]
    if checked["case_id"] == "axis_register":
        entry = raw["arms"][arm]
        return {"verdict": entry["verdict"]["verdict"],
                "target_verdict": "FAIL" if any(
                    entry["cases"][item] == "FAIL" for item in axis.TARGET_IDS) else "PASS",
                "preservation_verdict": "PASS" if all(
                    entry["cases"][item] == "PASS" for item in axis.PRESERVATION_IDS) else "FAIL",
                "test_count": len(entry["cases"]),
                "failed_count": sum(value == "FAIL" for value in entry["cases"].values())}
    direct = raw["arms"][arm + "/direct"]["verdict"]["verdict"]
    backpressure = raw["arms"][arm + "/backpressure"]["verdict"]["verdict"]
    return {"verdict": "PASS" if direct == backpressure == "PASS" else
            "FAIL" if direct == "PASS" and backpressure == "FAIL" else "UNKNOWN",
            "target_verdict": backpressure,
            "preservation_verdict": direct,
            "test_count": 2, "failed_count": int(backpressure == "FAIL") + int(direct == "FAIL")}


def build_record(raw: Mapping) -> ExecutionRecord:
    checked = verify_acquisition(raw)
    case_id, role = checked["case_id"], checked["role"]
    baseline = _arm_result(checked, "fault")
    repaired = _arm_result(checked, "candidate")
    if (baseline["verdict"] != "FAIL" or baseline["target_verdict"] != "FAIL" or
            baseline["preservation_verdict"] != "PASS" or
            repaired["verdict"] != "PASS" or repaired["target_verdict"] != "PASS" or
            repaired["preservation_verdict"] != "PASS"):
        raise ValueError("RTL TRAIN scoped target/preservation causal pair is incomplete")
    contract = MEASUREMENT_CONTRACT
    contract_digest = checked["measurement_contract_digest"]
    pair = {"contract": contract, "contract_digest": contract_digest,
            "before": {"contract": contract, "oracle_instance": checked["oracle_kind"],
                       "verdict": baseline["verdict"],
                       "target_verdict": baseline["target_verdict"],
                       "preservation_verdict": baseline["preservation_verdict"],
                       "source_sha256": checked["before_source_sha256"],
                       "train_receipt_digest": checked["train_receipt_digest"]},
            "after": {"contract": contract, "oracle_instance": checked["oracle_kind"],
                      "verdict": repaired["verdict"],
                      "target_verdict": repaired["target_verdict"],
                      "preservation_verdict": repaired["preservation_verdict"],
                      "source_sha256": checked["after_source_sha256"],
                      "train_receipt_digest": checked["train_receipt_digest"]},
            "controlled_measurement_valid": True,
            "native_oracle_equivalence": False}
    pair["receipt_digest"] = _digest(pair)
    scoped = {"version": SCOPED_VERSION,
              "role": "before" if role == "control" else "after",
              "case_id": case_id, "acquisition_digest": _digest(dict(raw)),
              "train_receipt_digest": checked["train_receipt_digest"],
              "lineage_receipt_digest": LINEAGE_DIGEST,
              "pair_receipt": pair}
    run_tag = "r5-train-" + case_id + "-fault-" + checked["train_receipt_digest"][-12:]
    config = {"public_context": checked["public_context"],
              "compatibility_profile": PROFILE,
              "source_file": checked["source_file"]}
    base_state = {
        "config": config,
        "reports": {"target_oracle": {"scope": contract["scope"],
                                      "verdict": "FAIL", "oracle_kind": checked["oracle_kind"],
                                      "target_verdict": "FAIL", "preservation_verdict": "PASS",
                                      "provenance": {"run_tag": run_tag}}},
        "artifacts": {"rtl_source_sha256": checked["before_source_sha256"],
                      "train_receipt_digest": checked["train_receipt_digest"],
                      "preregistration_sha256": checked["preregistration_sha256"]},
        "failure_signature": {"mechanism_family": FAMILY,
                              "failure": "buffered_payload_source_mismatch"},
    }
    if role == "control":
        target_state = json.loads(json.dumps(base_state))
        action = {"domain": "rtl.BASELINE_CONTROL",
                  "transformation_family": FAMILY,
                  "payload": {"control": True, "observation_only": True,
                              "compatibility_profile": PROFILE,
                              "measurement_contract_digest": contract_digest}}
        after_result = baseline
        failure_state, experiment = "PRESENT", "OBSERVATION"
    else:
        target_state = {
            "config": config,
            "reports": {"target_oracle": {"scope": contract["scope"],
                                          "verdict": "PASS", "oracle_kind": checked["oracle_kind"],
                                          "target_verdict": "PASS", "preservation_verdict": "PASS",
                                          "provenance": {"run_tag": run_tag + "-candidate"}}},
            "artifacts": {"rtl_source_sha256": checked["after_source_sha256"],
                          "train_receipt_digest": checked["train_receipt_digest"],
                          "preregistration_sha256": checked["preregistration_sha256"]},
        }
        action = {"domain": DOMAIN, "transformation_family": FAMILY,
                  "payload": checked["action_payload"]}
        after_result = repaired
        failure_state, experiment = "REMOVED", "REPAIR"
    verification = {
        "verdict": after_result["verdict"], "oracle_type": "TARGET_TEST",
        "scope": contract["scope"], "confidence_tier": "T",
        "oracle_complete": True, "obligation_coverage": 1.0,
        "evidence_refs": [checked["train_receipt_digest"],
                          checked["preregistration_sha256"], LINEAGE_DIGEST],
        "extractor_version": SCOPED_VERSION,
        "tool_versions": {"source_group": checked["repository"],
                          "train_receipt_sha256": checked["train_receipt_sha256"]},
        "full_oracle": {"before": {"complete": True, **baseline},
                        "after": {"complete": True, **after_result}},
        "scoped_execution": scoped,
    }
    record = ExecutionRecord(
        record_id="r5-rtl-train:" + _digest(scoped).split(":", 1)[1],
        domain="rtl.functional", project_id=checked["repository"],
        design_id=checked["design"], lineage_id=checked["repository"],
        repository_ref=checked["repository"] + "@" + checked["source_git_sha"],
        before=base_state, after=target_state, action=action,
        observation_delta={"original_failure": failure_state,
                           "failing_tests": {"before": baseline["failed_count"],
                                             "after": after_result["failed_count"]},
                           "created_regressions": [], "newly_observed_failures": [],
                           "experiment_kind": experiment, "utility_verdict": "UNKNOWN"},
        verification=verification)
    record.validate()
    return record


def replay_record(record: ExecutionRecord) -> dict:
    record.validate()
    scoped = record.verification.get("scoped_execution")
    if not isinstance(scoped, Mapping) or scoped.get("version") != SCOPED_VERSION:
        raise ValueError("unsupported RTL TRAIN scoped record")
    raw = acquisition(scoped.get("case_id"),
                      "control" if scoped.get("role") == "before" else "treatment")
    expected = build_record(raw)
    if asdict(record) != asdict(expected):
        raise ValueError("RTL TRAIN canonical record differs from raw acquisition")
    return expected.verification["scoped_execution"]["pair_receipt"]


def replay_persisted_rtl_train(conn: sqlite3.Connection, transition_id: str,
                               *, acquisition_data: Mapping) -> dict:
    from tehm.adapters.orfs_scoped import _compare_persisted_flow_record
    from tehm.causal.mechanism import load_transition_facts

    facts = load_transition_facts(conn, transition_id)
    scoped = facts.verifier.get("scoped_execution")
    if not isinstance(scoped, dict) or scoped.get("version") != SCOPED_VERSION:
        raise ValueError("transition lacks R5 RTL scoped execution")
    record = build_record(acquisition_data)
    _compare_persisted_flow_record(conn, transition_id, record)
    return {"version": SCOPED_VERSION, "transition_id": transition_id,
            "acquisition_digest": _digest(dict(acquisition_data)),
            "persisted_binding_verified": True, "learner_admission": False,
            "promotion_attempted": False}


__all__ = ["ACQUISITION_VERSION", "SCOPED_VERSION", "MEASUREMENT_CONTRACT",
           "acquisition", "verify_acquisition", "build_record", "replay_record",
           "replay_persisted_rtl_train"]
