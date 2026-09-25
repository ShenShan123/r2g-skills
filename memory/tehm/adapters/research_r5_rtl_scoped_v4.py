"""Generation-4 TRAIN records for the v6 shadow action and shared obligation.

The same researcher-assisted TRAIN sources are reused. This is a new
measurement/profile identity, not a fresh oracle instance or held-out task.
"""
from __future__ import annotations

import copy
import sqlite3
from collections.abc import Mapping
from dataclasses import asdict

from tehm.adapters import research_r5_rtl_scoped_v3 as v3
from tehm.canonical.capture import ExecutionRecord
from tehm.rtl.skid_payload_action_v6 import (
    DOMAIN, PROFILE, apply_skid_payload_action_v6, payload_from_source_v6,
)

ACQUISITION_VERSION = "tehm-r5-rtl-train-acquisition-v4"
SCOPED_VERSION = "tehm-r5-rtl-train-scoped-v4"
CAMPAIGN = "r5-rtl-skid-train-generation-4"
FAMILY = v3.FAMILY
CASES = v3.CASES
MEASUREMENT_CONTRACT = {
    **v3.MEASUREMENT_CONTRACT,
    "version": "tehm-r5-skid-payload-measurement-v4",
    "scope": PROFILE,
}

v1 = v3.v1
_digest = v3._digest
_sha = v3._sha


def acquisition(case_id: str, role: str) -> dict:
    original = v3.acquisition(case_id, role)
    return {**original, "version": ACQUISITION_VERSION, "campaign_id": CAMPAIGN}


def verify_acquisition(raw: Mapping) -> dict:
    if not isinstance(raw, Mapping):
        raise ValueError("R5 v4 acquisition must be a mapping")
    case_id, role = raw.get("case_id"), raw.get("role")
    if (case_id not in CASES or role not in {"control", "treatment"} or
            dict(raw) != acquisition(case_id, role)):
        raise ValueError("R5 v4 TRAIN acquisition identity or lock drift")
    return v3.verify_acquisition(v3.acquisition(case_id, role))


def _source(checked: Mapping) -> str:
    return v3._source(checked)


def build_record(raw: Mapping) -> ExecutionRecord:
    checked = verify_acquisition(raw)
    record = copy.deepcopy(v3.build_record(v3.acquisition(
        checked["case_id"], checked["role"])))
    source = _source(checked)
    payload = payload_from_source_v6(source, checked["public_context"])
    candidate, action = apply_skid_payload_action_v6(source, payload)
    if (_sha(candidate.encode()) != checked["after_source_sha256"] or
            action.get("rewritten") != 1 or
            action.get("source_binding_rederived") is not True or
            action.get("delegated_from") is None):
        raise ValueError("R5 v4 v6 action does not replay TRAIN candidate")
    contract = copy.deepcopy(MEASUREMENT_CONTRACT)
    digest = _digest(contract)
    scoped = record.verification["scoped_execution"]
    pair = scoped["pair_receipt"]
    pair["contract"] = contract
    pair["contract_digest"] = digest
    old_witness_digest = scoped["oracle_instance_witness_digest"]
    witness = copy.deepcopy(pair["after"]["oracle_instance_witness"])
    witness["version"] = "tehm-r5-oracle-instance-witness-v4"
    witness["shared_contract_digest"] = digest
    witness.pop("witness_digest", None)
    witness["witness_digest"] = _digest(witness)
    for side in ("before", "after"):
        pair[side]["contract"] = contract
        pair[side]["oracle_instance_witness"] = copy.deepcopy(witness)
    pair["receipt_digest"] = _digest({key: value for key, value in pair.items()
                                       if key != "receipt_digest"})
    scoped["version"] = SCOPED_VERSION
    scoped["acquisition_digest"] = _digest(dict(raw))
    scoped["oracle_instance_witness_digest"] = witness["witness_digest"]
    record.record_id = "r5-rtl-train-v4:" + _digest(scoped).split(":", 1)[1]
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
    record.verification["evidence_refs"] = [
        witness["witness_digest"] if ref == old_witness_digest else ref
        for ref in record.verification["evidence_refs"]]
    record.validate()
    return record


def replay_record(record: ExecutionRecord) -> dict:
    record.validate()
    scoped = record.verification.get("scoped_execution")
    if not isinstance(scoped, Mapping) or scoped.get("version") != SCOPED_VERSION:
        raise ValueError("unsupported R5 v4 scoped record")
    case_id, role = scoped.get("case_id"), scoped.get("role")
    raw = acquisition(case_id, "control" if role == "before" else "treatment")
    expected = build_record(raw)
    if asdict(record) != asdict(expected):
        raise ValueError("R5 v4 canonical record differs from raw TRAIN acquisition")
    return expected.verification["scoped_execution"]["pair_receipt"]


def replay_persisted_rtl_train_v4(conn: sqlite3.Connection, transition_id: str,
                                  *, acquisition_data: Mapping) -> dict:
    from tehm.adapters.orfs_scoped import _compare_persisted_flow_record
    from tehm.causal.mechanism import load_transition_facts

    facts = load_transition_facts(conn, transition_id)
    scoped = facts.verifier.get("scoped_execution")
    if not isinstance(scoped, dict) or scoped.get("version") != SCOPED_VERSION:
        raise ValueError("transition lacks R5 v4 RTL scoped execution")
    record = build_record(acquisition_data)
    _compare_persisted_flow_record(conn, transition_id, record)
    return {"version": SCOPED_VERSION, "transition_id": transition_id,
            "acquisition_digest": _digest(dict(acquisition_data)),
            "persisted_binding_verified": True, "learner_admission": False,
            "promotion_attempted": False}


def oracle_for(checked: Mapping, record: ExecutionRecord, arm: str):
    """Replay the pinned TRAIN outcome for the exact v6 candidate source."""
    if arm not in {"target", "preservation"}:
        raise ValueError("unsupported TRAIN oracle arm")
    expected = record.verification["full_oracle"]["after"]
    witness = record.verification["scoped_execution"]["pair_receipt"]["after"]["oracle_instance_witness"]
    if (witness["source_after_sha256"] != checked["after_source_sha256"] or
            witness["train_receipt_digest"] != checked["train_receipt_digest"] or
            witness["shared_contract_digest"] != _digest(MEASUREMENT_CONTRACT)):
        raise ValueError("v4 TRAIN oracle instance witness does not bind raw source")

    def check(candidate: str, _asset: Mapping) -> dict:
        if _sha(candidate.encode("utf-8")) != checked["after_source_sha256"]:
            return {"verdict": "FAIL"}
        verdict = expected["target_verdict" if arm == "target" else "preservation_verdict"]
        return {"verdict": "PASS" if expected["complete"] is True and verdict == "PASS"
                else "FAIL"}

    return check


__all__ = ["ACQUISITION_VERSION", "SCOPED_VERSION", "CAMPAIGN", "FAMILY",
           "CASES", "MEASUREMENT_CONTRACT", "acquisition", "verify_acquisition",
           "build_record", "replay_record", "replay_persisted_rtl_train_v4", "oracle_for"]
