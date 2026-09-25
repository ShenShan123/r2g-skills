"""RAM-only R5 RTL TRAIN canonical/causal/Knowledge and fail-closed checks.

The runner performs no target transfer, M+ export, Asset promotion, or model
call.  Its only external reads are pinned evaluator TRAIN and lineage receipts.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path

from tehm import db
from tehm.adapters.research_r5_rtl_scoped import (
    CAMPAIGN, PROFILE, acquisition, build_record, replay_record,
)
from tehm.artifact_store import ArtifactStore
from tehm.canonical.capture import capture
from tehm.causal.intervention import build_intervention_pair
from tehm.causal.path_builder import (
    build_transition_causal_fragment, consolidate_causal_path,
)
from tehm.causal.replication import evaluate_replicated_effect
from tehm.ids import stable_dumps
from tehm.knowledge import (
    build_knowledge_from_path, record_knowledge_authority,
    register_knowledge, verify_knowledge_authority,
)
from tehm.verified_execution import (
    require_verified_transition, scoped_learning_replay,
)


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
    with tempfile.TemporaryDirectory(prefix="r5-rtl-scoped-check-") as tmp:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            db.ensure_schema(conn)
            store = ArtifactStore(Path(tmp) / "artifacts")
            acquisitions: dict[str, dict] = {}
            ids: dict[tuple[str, str], str] = {}
            records = {}
            for case in CASES:
                for role in ROLES:
                    raw = acquisition(case, role)
                    record = build_record(raw)
                    receipt = capture(
                        conn, store, record, dataset_campaign_id=CAMPAIGN,
                        dataset_split="training", dataset_learner_eligible=True)
                    ids[(case, role)] = receipt.transition_id
                    acquisitions[receipt.transition_id] = raw
                    records[(case, role)] = record
            digest = _digest(acquisitions)
            cases = {
                "four_distinct_canonical_transitions": len(set(ids.values())) == 4,
                "raw_record_replay": all(
                    replay_record(records[(case, role)])["controlled_measurement_valid"] is True
                    for case in CASES for role in ROLES),
                "outside_context_rejected": _reject(lambda:
                    require_verified_transition(conn, ids[("axis_register", "treatment")])),
                "wrong_acquisition_freeze_digest_rejected": _reject(lambda:
                    scoped_learning_replay(conn, campaign_id=CAMPAIGN,
                                           acquisitions=acquisitions,
                                           expected_digest="sha256:bad").__enter__()),
            }
            file_db = sqlite3.connect(Path(tmp) / "file-backed.sqlite")
            try:
                cases["file_backed_replay_rejected"] = _reject(lambda:
                    scoped_learning_replay(file_db, campaign_id=CAMPAIGN,
                                           acquisitions=acquisitions,
                                           expected_digest=digest).__enter__())
            finally:
                file_db.close()
            with scoped_learning_replay(
                    conn, campaign_id=CAMPAIGN, acquisitions=acquisitions,
                    expected_digest=digest):
                for transition_id in ids.values():
                    require_verified_transition(conn, transition_id)
                cases["four_scoped_transitions_verified"] = True
                pairs = {}
                for case in CASES:
                    pairs[case] = build_intervention_pair(
                        conn, ids[(case, "control")], ids[(case, "treatment")],
                        campaign_id=CAMPAIGN, target_scope=PROFILE)
                cases["both_controlled_pairs_l2"] = all(
                    pair.validity_status == "VALID_CONTROLLED_PAIR" and
                    pair.evidence_level == "L2_CONTROLLED_INTERVENTION"
                    for pair in pairs.values())
                fragments = [build_transition_causal_fragment(
                    conn, tid, campaign_id=CAMPAIGN) for tid in sorted(ids.values())]
                path = consolidate_causal_path(
                    conn, fragments, campaign_id=CAMPAIGN, status="shadow")
                replicated = evaluate_replicated_effect(
                    conn, path.path_id, campaign_id=CAMPAIGN)
                cases["two_source_replication_l3"] = (
                    replicated.eligible is True and
                    replicated.evidence_level == "L3_REPLICATED_EFFECT" and
                    set(replicated.unique_lineages) == {
                        "alexforencich/verilog-axis", "ZipCPU/wb2axip"})
                knowledge = build_knowledge_from_path(conn, path.path_id)
                cases["rtl_control_excluded_from_intervention"] = (
                    knowledge.intervention.get("action_domains") ==
                    ["rtl.SKID_TEMP_PAYLOAD_RESTORE_SHADOW_V3"])
                cases["measurement_contract_scoped"] = (
                    knowledge.intervention.get("measurement_contract", {}).get("scope") ==
                    PROFILE and knowledge.expected_outcome.get("full_signoff_claim") is False)
                register_knowledge(conn, knowledge, target_scope=PROFILE)
                authority = record_knowledge_authority(
                    conn, knowledge, target_scope=PROFILE)
                cold = verify_knowledge_authority(conn, authority)
                cases["strict_knowledge_ledger_eligible"] = (
                    authority.eligible is True and cold["eligible"] is True and
                    authority.gates.get("authority_evidence_ledger") is True)
                forged = copy.deepcopy(authority.to_dict())
                forged["eligible"] = False
                cases["forged_authority_rejected"] = (
                    verify_knowledge_authority(conn, forged)["eligible"] is False)
                swapped = dict(acquisitions)
                a, b = ids[("axis_register", "treatment")], ids[("zipcpu_skidbuffer", "treatment")]
                swapped[a], swapped[b] = swapped[b], swapped[a]
                with scoped_learning_replay(
                        conn, campaign_id=CAMPAIGN, acquisitions=swapped,
                        expected_digest=_digest(swapped)):
                    cases["swapped_acquisition_rejected"] = _reject(lambda:
                        require_verified_transition(conn, a))
                conn.execute("SAVEPOINT r5_membership_negative")
                conn.execute(
                    "UPDATE tehm_dataset_membership SET split='heldout', learner_eligible=0 "
                    "WHERE transition_id=? AND campaign_id=?", (a, CAMPAIGN))
                cases["heldout_membership_rejected"] = _reject(lambda:
                    require_verified_transition(conn, a))
                conn.execute("ROLLBACK TO SAVEPOINT r5_membership_negative")
                conn.execute("RELEASE SAVEPOINT r5_membership_negative")
            result = {
                "valid": all(cases.values()), "case_count": len(cases),
                "failed": sorted(key for key, passed in cases.items() if not passed),
                "cases": cases, "role": "RAM_ONLY_CORE_REPLAY_NOT_PERSISTENT_M_PLUS",
                "transition_ids": {case + "/" + role: ids[(case, role)]
                                   for case in CASES for role in ROLES},
                "causal_path_id": path.path_id,
                "knowledge_object_id": knowledge.object_id,
                "knowledge_authority_receipt_digest": authority.receipt_digest,
                "lineages": list(replicated.unique_lineages),
                "memory_m_plus_constructed": False,
                "asset_authority": False,
            }
            return result
        finally:
            conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--output", type=Path, help="write a new frozen preflight receipt")
    mode.add_argument("--verify", type=Path, help="cold-replay a frozen preflight receipt")
    args = parser.parse_args()
    result = check()
    if args.output is not None:
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, sort_keys=True)
            handle.write("\n")
    if args.verify is not None:
        saved = json.loads(args.verify.read_text(encoding="utf-8"))
        if saved != result:
            raise ValueError("R5 RTL scoped preflight receipt does not cold replay")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
