"""RAM-only conformance for R5 generation-3 shared measurement and TRAIN replay."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path

from tehm import db
from tehm.adapters import research_r5_rtl_scoped_v3 as adapter
from tehm.artifact_store import ArtifactStore
from tehm.canonical.capture import capture
from tehm.causal.intervention import build_intervention_pair
from tehm.causal.path_builder import (
    build_transition_causal_fragment, consolidate_causal_path,
)
from tehm.causal.replication import evaluate_replicated_effect
from tehm.ids import stable_dumps
from tehm.knowledge import (
    build_knowledge_from_path, record_knowledge_authority, register_knowledge,
    verify_knowledge_authority,
)
from tehm.rtl.rtl_actions import apply_rtl_action
from tehm.verified_execution import require_verified_transition, scoped_learning_replay


CASES = ("axis_register", "zipcpu_skidbuffer")
ROLES = ("control", "treatment")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _reject(call) -> bool:
    try:
        call()
    except (ValueError, KeyError, TypeError):
        return True
    return False


def check() -> dict:
    with tempfile.TemporaryDirectory(prefix="r5-rtl-scoped-v3-check-") as tmp:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            db.ensure_schema(conn)
            store = ArtifactStore(Path(tmp) / "artifacts")
            acquisitions: dict[str, dict] = {}
            ids: dict[tuple[str, str], str] = {}
            records = {}
            witnesses = {}
            cases: dict[str, bool] = {}
            for case in CASES:
                for role in ROLES:
                    raw = adapter.acquisition(case, role)
                    record = adapter.build_record(raw)
                    pair = adapter.replay_record(record)
                    witness = pair["before"]["oracle_instance_witness"]
                    witnesses[(case, role)] = witness
                    cases[case + "/" + role + "/same_instance_on_pair"] = (
                        pair["after"]["oracle_instance_witness"] == witness and
                        witness["shared_contract_digest"] == pair["contract_digest"] and
                        witness["witness_digest"] == _digest({
                            k: v for k, v in witness.items() if k != "witness_digest"}))
                    receipt = capture(
                        conn, store, record, dataset_campaign_id=adapter.CAMPAIGN,
                        dataset_split="training", dataset_learner_eligible=True)
                    ids[(case, role)] = receipt.transition_id
                    acquisitions[receipt.transition_id] = raw
                    records[(case, role)] = record
                checked = adapter.verify_acquisition(
                    adapter.acquisition(case, "treatment"))
                source = adapter._source(checked)
                payload = records[(case, "treatment")].action["payload"]
                candidate, edit = apply_rtl_action(source, payload)
                cases[case + "/v5_action_replays_candidate"] = (
                    edit["rewritten"] == 1 and
                    adapter._sha(candidate.encode("utf-8")) ==
                    checked["after_source_sha256"])
            contract = adapter.MEASUREMENT_CONTRACT
            contract_text = stable_dumps(contract)
            cases["shared_contract_has_no_instance_identity"] = (
                "axis_register" not in contract_text and
                "zipcpu" not in contract_text and
                "source_specific_oracles" not in contract_text and
                "testbench_sha256" not in contract_text and
                contract["scope"] == adapter.PROFILE)
            cases["instance_witnesses_are_distinct"] = (
                witnesses[(CASES[0], "treatment")]["witness_digest"] !=
                witnesses[(CASES[1], "treatment")]["witness_digest"] and
                all(witnesses[(case, "control")] == witnesses[(case, "treatment")]
                    for case in CASES))
            cases["four_distinct_transitions"] = len(set(ids.values())) == 4
            control = records[(CASES[0], "control")]
            forged = copy.deepcopy(control)
            forged.verification["scoped_execution"]["pair_receipt"]["before"][
                "oracle_instance_witness"]["oracle_source_sha256"] = "sha256:forged"
            cases["forged_oracle_witness_rejected"] = _reject(
                lambda: adapter.replay_record(forged))
            forged_contract = copy.deepcopy(control)
            forged_contract.verification["scoped_execution"]["pair_receipt"][
                "contract"]["common_obligation"] = "anything"
            cases["forged_shared_contract_rejected"] = _reject(
                lambda: adapter.replay_record(forged_contract))
            cases["wrong_acquisition_campaign_rejected"] = _reject(lambda:
                adapter.verify_acquisition({**adapter.acquisition(CASES[0], "control"),
                                            "campaign_id": "wrong"}))
            cases["outside_ram_replay_rejected"] = _reject(lambda:
                require_verified_transition(conn, ids[(CASES[0], "treatment")]))
            digest = _digest(acquisitions)
            with scoped_learning_replay(
                    conn, campaign_id=adapter.CAMPAIGN,
                    acquisitions=acquisitions, expected_digest=digest):
                for transition_id in ids.values():
                    require_verified_transition(conn, transition_id)
                cases["four_scoped_transitions_verified"] = True
                pairs = [build_intervention_pair(
                    conn, ids[(case, "control")], ids[(case, "treatment")],
                    campaign_id=adapter.CAMPAIGN,
                    target_scope=adapter.PROFILE) for case in CASES]
                cases["both_pairs_l2"] = all(
                    pair.validity_status == "VALID_CONTROLLED_PAIR" and
                    pair.evidence_level == "L2_CONTROLLED_INTERVENTION"
                    for pair in pairs)
                fragments = [build_transition_causal_fragment(
                    conn, tid, campaign_id=adapter.CAMPAIGN)
                    for tid in sorted(acquisitions)]
                path = consolidate_causal_path(
                    conn, fragments, campaign_id=adapter.CAMPAIGN, status="shadow")
                replication = evaluate_replicated_effect(
                    conn, path.path_id, campaign_id=adapter.CAMPAIGN)
                cases["two_source_replication_l3"] = (
                    replication.eligible is True and
                    replication.evidence_level == "L3_REPLICATED_EFFECT" and
                    set(replication.unique_lineages) == {
                        "alexforencich/verilog-axis", "ZipCPU/wb2axip"})
                knowledge = build_knowledge_from_path(conn, path.path_id)
                measurement = knowledge.intervention.get("measurement_contract", {})
                cases["knowledge_exact_shared_digest"] = (
                    measurement.get("contract_digest") == _digest(contract) and
                    measurement.get("scope") == adapter.PROFILE)
                register_knowledge(conn, knowledge, target_scope=adapter.PROFILE)
                authority = record_knowledge_authority(
                    conn, knowledge, target_scope=adapter.PROFILE)
                cases["strict_knowledge_authority"] = (
                    authority.eligible is True and
                    verify_knowledge_authority(conn, authority)["eligible"] is True)
                swapped = dict(acquisitions)
                a = ids[(CASES[0], "treatment")]
                b = ids[(CASES[1], "treatment")]
                swapped[a], swapped[b] = swapped[b], swapped[a]
                with scoped_learning_replay(
                        conn, campaign_id=adapter.CAMPAIGN,
                        acquisitions=swapped, expected_digest=_digest(swapped)):
                    cases["swapped_acquisition_rejected"] = _reject(lambda:
                        require_verified_transition(conn, a))
                conn.execute("SAVEPOINT r5_v3_membership_negative")
                conn.execute(
                    "UPDATE tehm_dataset_membership SET split='heldout', learner_eligible=0 "
                    "WHERE transition_id=? AND campaign_id=?", (a, adapter.CAMPAIGN))
                cases["heldout_membership_rejected"] = _reject(lambda:
                    require_verified_transition(conn, a))
                conn.execute("ROLLBACK TO SAVEPOINT r5_v3_membership_negative")
                conn.execute("RELEASE SAVEPOINT r5_v3_membership_negative")
            return {
                "schema": "tehm-r5-shared-measurement-v3-ram-conformance-v1",
                "valid": all(cases.values()), "case_count": len(cases),
                "failed": sorted(key for key, passed in cases.items() if not passed),
                "cases": cases, "role": "TRAIN_RAM_ONLY_NOT_M_PLUS_NOT_TRANSFER",
                "shared_contract_digest": _digest(contract),
                "oracle_instance_witness_digests": {
                    case: witnesses[(case, "treatment")]["witness_digest"]
                    for case in CASES},
                "knowledge_object_id": knowledge.object_id,
                "knowledge_authority_receipt_digest": authority.receipt_digest,
                "memory_m_plus_constructed": False,
                "target_instance_witness_validated": False,
                "asset_authority": False, "model_calls": 0,
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
    dev = Path("/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/dev")
    if args.output is not None:
        target = args.output.resolve(strict=False)
        if target.parent != dev or target.exists() or target.is_symlink():
            raise ValueError("v3 preflight output must be a new direct child of pilot dev")
        with target.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, sort_keys=True)
            handle.write("\n")
    if args.verify is not None:
        source = args.verify.resolve(strict=True)
        if source.parent != dev or source.is_symlink() or json.loads(
                source.read_bytes()) != result:
            raise ValueError("v3 frozen RAM preflight does not cold replay")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
