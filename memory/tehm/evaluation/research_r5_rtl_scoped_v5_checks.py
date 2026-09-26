"""Three-source generation-5 TRAIN capture, causal and Knowledge admission.

All canonical state is confined to RAM and temporary artifacts. No target
results, persisted M+, production promotion or model calls are produced.
"""
from __future__ import annotations

import argparse
import copy
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

from tehm import db
from tehm.adapters import research_r5_rtl_scoped_v4 as previous
from tehm.adapters import research_r5_rtl_scoped_v5 as adapter
from tehm.artifact_store import ArtifactStore
from tehm.canonical.capture import capture
from tehm.causal.intervention import build_intervention_pair
from tehm.causal.path_builder import build_transition_causal_fragment, consolidate_causal_path
from tehm.causal.replication import evaluate_replicated_effect
from tehm.knowledge import (
    build_knowledge_from_path, record_knowledge_authority, register_knowledge,
    verify_knowledge_authority,
)
from tehm.rtl.rtl_actions import apply_rtl_action
from tehm.verified_execution import require_verified_transition, scoped_learning_replay

CASES = tuple(adapter.CASES)
ROOT = Path("/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/dev")
RAM_CLOCK = "2026-09-25T00:00:00+00:00"


def _reject(call) -> bool:
    try:
        call()
    except (ValueError, KeyError, TypeError):
        return True
    return False


def check() -> dict:
    with patch("tehm.db.now_local", return_value=RAM_CLOCK):
        return _check()


def _check() -> dict:
    with tempfile.TemporaryDirectory(prefix="r5-scoped-v5-") as tmp:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            db.ensure_schema(conn)
            store = ArtifactStore(Path(tmp) / "artifacts")
            acquisitions, ids, records, witnesses, cases = {}, {}, {}, {}, {}
            for case in CASES:
                for role in ("control", "treatment"):
                    raw = adapter.acquisition(case, role)
                    record = adapter.build_record(raw)
                    pair = adapter.replay_record(record)
                    witness = pair["before"]["oracle_instance_witness"]
                    witnesses[(case, role)] = witness
                    cases[case + "/" + role + "/same_instance_on_pair"] = (
                        pair["after"]["oracle_instance_witness"] == witness and
                        witness["shared_contract_digest"] == pair["contract_digest"] and
                        witness["witness_digest"] == adapter._digest({
                            key: value for key, value in witness.items() if key != "witness_digest"}))
                    receipt = capture(conn, store, record, dataset_campaign_id=adapter.CAMPAIGN,
                                      dataset_split="training", dataset_learner_eligible=True)
                    ids[(case, role)] = receipt.transition_id
                    acquisitions[receipt.transition_id] = raw
                    records[(case, role)] = record
                checked = adapter.verify_acquisition(adapter.acquisition(case, "treatment"))
                source = adapter._source(checked)
                record = records[(case, "treatment")]
                candidate, edit = apply_rtl_action(source, record.action["payload"])
                cases[case + "/source_action_and_exact_candidate_oracle"] = (
                    edit.get("rewritten") == 1 and edit.get("source_binding_rederived") is True and
                    adapter._sha(candidate.encode()) == checked["after_source_sha256"] and
                    adapter.oracle_for(checked, record, "target")(candidate, {})["verdict"] == "PASS" and
                    adapter.oracle_for(checked, record, "preservation")(candidate, {})["verdict"] == "PASS" and
                    adapter.oracle_for(checked, record, "target")(source, {})["verdict"] == "FAIL")
            contract = adapter.MEASUREMENT_CONTRACT
            contract_text = json.dumps(contract, sort_keys=True)
            cases["shared_contract_has_no_instance_identity"] = (
                all(name not in contract_text for name in CASES) and
                "testbench_sha256" not in contract_text and
                contract["scope"] == adapter.PROFILE and
                contract["native_oracle_equivalence"] is False)
            cases["three_distinct_oracle_instances"] = (
                len({witnesses[(case, "treatment")]["witness_digest"] for case in CASES}) == 3 and
                all(witnesses[(case, "control")] == witnesses[(case, "treatment")]
                    for case in CASES))
            cases["six_distinct_transitions"] = len(set(ids.values())) == 6
            libsv_record = records[("libsv_train", "treatment")]
            cases["libsv_native_sensitivity_separate_from_private_pair"] = (
                libsv_record.verification["full_oracle"]["before"]["test_count"] == 2 and
                libsv_record.verification["full_oracle"]["before"]["failed_count"] == 1 and
                witnesses[("libsv_train", "treatment")]["native_sensitivity"][
                    "native-fault"]["verdict"] == "FAIL")
            forged = copy.deepcopy(libsv_record)
            forged.verification["scoped_execution"]["pair_receipt"]["before"][
                "oracle_instance_witness"]["oracle_source_sha256"] = "sha256:forged"
            cases["forged_libsv_witness_rejected"] = _reject(lambda: adapter.replay_record(forged))
            forged_contract = copy.deepcopy(libsv_record)
            forged_contract.verification["scoped_execution"]["pair_receipt"][
                "contract"]["common_obligation"] = "anything"
            cases["forged_contract_rejected"] = _reject(lambda: adapter.replay_record(forged_contract))
            cases["old_acquisition_rejected"] = _reject(lambda: adapter.verify_acquisition(
                previous.acquisition("axis_register", "treatment")))
            cases["forged_train_digest_rejected"] = _reject(lambda: adapter.verify_acquisition({
                **adapter.acquisition("libsv_train", "treatment"),
                "expected_train_digest": "sha256:forged"}))
            cases["outside_ram_context_rejected"] = _reject(lambda: require_verified_transition(
                conn, ids[("libsv_train", "treatment")]))
            with scoped_learning_replay(conn, campaign_id=adapter.CAMPAIGN,
                                        acquisitions=acquisitions,
                                        expected_digest=adapter._digest(acquisitions)):
                for transition_id in ids.values():
                    require_verified_transition(conn, transition_id)
                cases["six_scoped_transitions_verified"] = True
                pairs = [build_intervention_pair(
                    conn, ids[(case, "control")], ids[(case, "treatment")],
                    campaign_id=adapter.CAMPAIGN, target_scope=adapter.PROFILE) for case in CASES]
                cases["three_controlled_pairs_l2"] = all(
                    pair.validity_status == "VALID_CONTROLLED_PAIR" and
                    pair.evidence_level == "L2_CONTROLLED_INTERVENTION" for pair in pairs)
                fragments = [build_transition_causal_fragment(
                    conn, tid, campaign_id=adapter.CAMPAIGN) for tid in sorted(acquisitions)]
                path = consolidate_causal_path(conn, fragments, campaign_id=adapter.CAMPAIGN,
                                               status="shadow")
                replication = evaluate_replicated_effect(conn, path.path_id,
                                                         campaign_id=adapter.CAMPAIGN)
                cases["three_source_replication_l3"] = (
                    replication.eligible is True and
                    replication.evidence_level == "L3_REPLICATED_EFFECT" and
                    set(replication.unique_lineages) == {
                        adapter.CASES[case]["repository"] for case in CASES})
                knowledge = build_knowledge_from_path(conn, path.path_id)
                measurement = knowledge.intervention.get("measurement_contract", {})
                cases["knowledge_exact_shared_contract"] = (
                    measurement.get("contract_digest") == adapter._digest(contract) and
                    measurement.get("scope") == adapter.PROFILE)
                register_knowledge(conn, knowledge, target_scope=adapter.PROFILE)
                authority = record_knowledge_authority(conn, knowledge, target_scope=adapter.PROFILE)
                cold = verify_knowledge_authority(conn, authority)
                cases["strict_knowledge_authority"] = authority.eligible is True and cold["eligible"] is True
                swapped = dict(acquisitions)
                a, b = ids[("libsv_train", "treatment")], ids[("axis_register", "treatment")]
                swapped[a], swapped[b] = swapped[b], swapped[a]
                with scoped_learning_replay(conn, campaign_id=adapter.CAMPAIGN,
                                            acquisitions=swapped,
                                            expected_digest=adapter._digest(swapped)):
                    cases["swapped_acquisition_rejected"] = _reject(lambda:
                        require_verified_transition(conn, a))
                conn.execute("SAVEPOINT membership_negative")
                conn.execute("UPDATE tehm_dataset_membership SET split='heldout', learner_eligible=0 "
                             "WHERE transition_id=? AND campaign_id=?", (a, adapter.CAMPAIGN))
                cases["heldout_membership_rejected"] = _reject(lambda:
                    require_verified_transition(conn, a))
                conn.execute("ROLLBACK TO SAVEPOINT membership_negative")
                conn.execute("RELEASE SAVEPOINT membership_negative")
            return {"schema": "tehm-r5-shared-measurement-v5-ram-conformance-v1",
                    "valid": all(cases.values()), "case_count": len(cases),
                    "failed": sorted(name for name, passed in cases.items() if not passed),
                    "cases": cases, "role": "TRAIN_RAM_ONLY_NOT_M_PLUS_NOT_TRANSFER",
                    "shared_contract_digest": adapter._digest(contract),
                    "oracle_instance_witness_digests": {
                        case: witnesses[(case, "treatment")]["witness_digest"] for case in CASES},
                    "knowledge_object_id": knowledge.object_id,
                    "knowledge_authority_receipt_digest": authority.receipt_digest,
                    "knowledge_authority_eligible": authority.eligible,
                    "replication_evidence_level": replication.evidence_level,
                    "source_groups": sorted(replication.unique_lineages),
                    "source_group_claim": "bounded_pinned_source_groups_only",
                    "transition_ids": {case + "/" + role: tid for (case, role), tid in ids.items()},
                    "memory_m_plus_constructed": False, "target_instance_witness_validated": False,
                    "asset_authority": False, "model_calls": 0,
                    "ram_fixture_clock": RAM_CLOCK, "adapter_profile": adapter.PROFILE,
                    "adapter_campaign": adapter.CAMPAIGN}
        finally:
            conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.output is not None and (args.output.parent != ROOT or
                                   args.output.exists() or args.output.is_symlink()):
        raise ValueError("generation-5 RAM receipt must be new under pilot dev")
    if args.verify is not None and (args.verify.parent != ROOT or args.verify.is_symlink()):
        raise ValueError("generation-5 RAM receipt verification path is outside pilot dev")
    report = check()
    if args.output is not None:
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
    elif json.loads(args.verify.read_bytes()) != report:
        raise ValueError("generation-5 TRAIN RAM receipt differs from cold replay")
    print(json.dumps({"valid": report["valid"], "case_count": report["case_count"],
                      "failed": report["failed"],
                      "knowledge_object_id": report["knowledge_object_id"]}, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
