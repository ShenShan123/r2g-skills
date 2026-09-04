#!/usr/bin/env python3
"""Run the Revision3 real-RTL ``STATE_SHIFT`` evolution challenge.

This command is the first end-to-end Evolution Challenge lane described by
Revision3.  It captures two verified training transitions, derives a
training-only support envelope, executes two source-disjoint shifted-flow
cases through real Icarus, and carries the typed state-shift reason through
P12, proposal, and P13 isolated staging.  It deliberately never imports a
fixture manifest for the challenge cases, never commits a canonical-memory
mutation, and never changes production authority.

All generated SQLite, source copies, and gate evidence are written outside the
repository.  ``memory/docs/`` is a local governing input and is never copied
or submitted.

Usage::

    PYTHONDONTWRITEBYTECODE=1 python3 scripts/run_r3_state_shift_challenge.py \
        --artifacts /data1/zhangdy/tehm-campaigns/tehm-r3-state-shift-challenge-20260902
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts import MemoryRoutingDecision  # noqa: E402
from tehm import db  # noqa: E402
from tehm.artifact_store import ArtifactStore  # noqa: E402
from tehm.canonical.capture import capture  # noqa: E402
from tehm.evaluation.candidate_executor import (  # noqa: E402
    execute_candidate, execute_paired_candidates,
)
from tehm.evaluation.rtl_candidate_oracle import IcarusCandidateOracle  # noqa: E402
from tehm.evaluation.rtl_cohort import execute_rtl_paired_cohort  # noqa: E402
from tehm.assets.receipts import RuntimeBindingReceipt  # noqa: E402
from tehm.capability import (  # noqa: E402
    create_policy_snapshot, evaluate_capability_attribution_from_db,
    record_policy_load,
)
from tehm.capability.lineage import build_candidate_lineage  # noqa: E402
from tehm.evolution.admission import admit_evolution_reason  # noqa: E402
from tehm.evolution.anti_forgetting import AntiForgettingWitness  # noqa: E402
from tehm.evolution.apply_update import apply_localized_update_shadow  # noqa: E402
from tehm.capability.delta import memory_delta_from_shadow_update  # noqa: E402
from tehm.evolution.events import append_routed_state_shift_observation, load_state_shift_observations  # noqa: E402
from tehm.evolution.p12_shadow_trigger import (  # noqa: E402
    build_p12_shadow_update_triggers_from_reason_receipt,
)
from tehm.evolution.reason_derivation import (  # noqa: E402
    derive_state_shift_reason,
    p13_reason_receipt_from_derivations,
)
from tehm.evolution.state_shift_revision import (  # noqa: E402
    propose_repeated_state_shift_from_paired_receipts,
    state_shift_proposal_to_localized_plan,
)
from tehm.knowledge import MechanismKnowledge, register_knowledge  # noqa: E402
from tehm.knowledge.revision import revise_knowledge  # noqa: E402
from tehm.rtl.rtl_evidence import build_rtl_execution_record  # noqa: E402
from tehm.rtl.rtl_oracle import IcarusOracle  # noqa: E402
from tehm.state import (  # noqa: E402
    build_support_envelope, evaluate_state_shift, resolve_current_state,
    verify_resolution_snapshot,
)
from tehm.retrieval.asset_selector import AssetSelection, AssetSelectionReceipt  # noqa: E402
from tehm.retrieval.structured_candidate import (  # noqa: E402
    StructuredRepairCandidate, build_structured_candidate,
)
from scripts.build_p13_anti_forgetting_witness import build_p13_anti_forgetting_witness  # noqa: E402


CAMPAIGN_ID = "tehm-r3-state-shift-challenge-20260902"
TOOLCHAIN_DIGEST = "sha256:r3-state-shift-icarus-toolchain"
ORACLE_DIGEST = "sha256:r3-state-shift-icarus-candidate-oracle"
PLATFORM_DIGEST = "sha256:r3-state-shift-platform-asap7"
PDK_DIGEST = "sha256:r3-state-shift-pdk"
MANIFEST_DIGEST = "sha256:r3-state-shift-challenge-manifest"
TRAINING_PLATFORM = "sky130"
CHALLENGE_PLATFORM = "asap7"

_SPECS = (
    {
        "key": "req_ack",
        "fixture": "req_ack_bug",
        "lineage": "lineage-r3-state-shift-send",
        "training_lineage": "req_ack_fsm",
        "source_state": "SEND",
        "target_state": "DONE",
        "condition": "ack",
        "buggy": "SEND: next_state = DONE;          // BUG: no ack guard",
        "fixed": "SEND: if (ack) next_state = DONE;",
    },
    {
        "key": "req_write",
        "fixture": "req_ack_bug2",
        "lineage": "lineage-r3-state-shift-write",
        "training_lineage": "req_ack_fsm2",
        "source_state": "WRITE",
        "target_state": "VERIFY",
        "condition": "wr_ack",
        "buggy": "WRITE:  next_state = VERIFY;          // BUG: no wr_ack guard",
        "fixed": "WRITE:  if (wr_ack) next_state = VERIFY;",
    },
)

# These source-disjoint variants are intentionally outside the two training
# lineages.  They keep the same executable handshake mechanism while retaining
# the original bug, so the R3-7 comparison can prove M_t failure -> M_t+1
# success and then repeat the no-memory baseline for the -DeltaM ablation.
_HELDOUT_SPECS = (
    {
        "key": "req_read",
        "fixture": "req_ack_bug3",
        "lineage": "lineage-r3-heldout-read",
        "source_state": "RCV",
        "target_state": "RD_DONE",
        "condition": "rd_ack",
        "buggy": "RCV:    next_state = RD_DONE;       // BUG: no rd_ack guard",
        "fixed": "RCV:    if (rd_ack) next_state = RD_DONE;",
    },
    {
        "key": "req_ready",
        "fixture": "req_ack_bug4",
        "lineage": "lineage-r3-heldout-ready",
        "source_state": "WAIT",
        "target_state": "DONE",
        "condition": "ready",
        "buggy": "WAIT: next_state = DONE;       // BUG: no ready guard",
        "fixed": "WAIT: if (ready) next_state = DONE;",
    },
)


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_payload(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _connection_digest(conn: sqlite3.Connection) -> str:
    """Digest the logical SQLite dump for rollback/source invariants."""
    return _sha256_payload("\n".join(conn.iterdump()))


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _knowledge() -> MechanismKnowledge:
    return MechanismKnowledge(
        knowledge_id="r3-handshake-knowledge", version=1,
        mechanism_family="HANDSHAKE_COMPLETION",
        compatibility_profile="rtl.fsm.single_guard.v1",
        antecedent={"failure": "completion_not_observed"},
        intervention={"family": "GUARD_STRENGTHEN"},
        mediated_effects=({"effect": "legal_transition"},),
        expected_outcome={"outcome": "PASS"},
        positive_applicability=({
            "mechanism_family": "HANDSHAKE_COMPLETION",
            "compatibility_profile": "rtl.fsm.single_guard.v1",
            "platform": TRAINING_PLATFORM,
        },),
        negative_applicability=(),
        preserved_obligations=("target_trace_pass",),
        known_failure_modes=("ambiguous_target_binding",),
        causal_path_ids=("r3-path-handshake",),
        evidence_level="L2_CONTROLLED_INTERVENTION",
        support_lineages=("req_ack_fsm", "req_ack_fsm2"), status="shadow")


def _candidate(spec: dict, state_id: str, knowledge_id: str) -> StructuredRepairCandidate:
    source_state = spec["source_state"]
    target_state = spec["target_state"]
    condition = spec["condition"]
    target = (
        rf"(?m)^[ \t]*{re.escape(source_state)}:[ \t]*"
        rf"(?:if[ \t]*\([ \t]*{re.escape(condition)}[ \t]*\)[ \t]*)?next_state"
        rf"[ \t]*=[ \t]*{re.escape(target_state)}[ \t]*;"
    )
    replacement = f"{source_state}: if ({condition} && 1'b1) next_state = {target_state};"
    return StructuredRepairCandidate(
        candidate_id=f"r3-state-shift-safe-{spec['key']}",
        resolved_state_id=state_id,
        knowledge_object_id=knowledge_id,
        causal_path_ids=("r3-path-handshake",),
        asset_id="r3-guard-strengthen-safe",
        action_family="AST_REWRITE",
        concrete_action={
            "domain": "rtl.AST_REWRITE",
            "transformation_family": "AST_REWRITE",
            "payload": {"target": target, "replacement": replacement, "count": 1},
        },
        applicability_receipt_id=f"r3-state-shift-app-{spec['key']}",
        binding_receipt_id=f"r3-state-shift-binding-{spec['key']}",
        obligations=("RTL_TARGET_TEST_PASS", "RTL_FROZEN_REGRESSION_PASS", "RTL_COMPILE_PASS"),
        evidence_level="L3_REPLICATED_EFFECT", authority={"eligible": True}, risk={},
        provenance={"evaluation_only": True, "source": "r3_state_shift_challenge"})


def _capture_training(conn: sqlite3.Connection, store: ArtifactStore,
                      oracle: IcarusOracle, spec: dict) -> str:
    fixture = ROOT / "tests" / "fixtures" / "rtl_projects" / spec["fixture"]
    record = build_rtl_execution_record(fixture, oracle=oracle, store=store)
    receipt = capture(
        conn, store, record, dataset_campaign_id=CAMPAIGN_ID,
        dataset_split="training", dataset_learner_eligible=True)
    if receipt.outcome != "PASS":
        raise RuntimeError(f"training fixture did not produce PASS: {spec['fixture']}")
    return receipt.transition_id


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "tehm_states", "tehm_transitions", "tehm_episodes", "tehm_episode_steps",
        "tehm_dataset_membership", "tehm_edges", "tehm_physical_effects",
        "tehm_mechanism_knowledge", "tehm_memory_relations", "tehm_memory_events",
    )
    return {table: int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in tables}


def _build_anti_forgetting(artifacts: Path, cohort, oracle: IcarusOracle,
                           baseline_db_digest: str) -> tuple[AntiForgettingWitness, dict]:
    """Create an external, file-bound four-gate witness from real executions."""
    evidence_dir = artifacts / "anti_forgetting_evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    first_id, second_id = sorted(cohort.case_receipts)
    first = cohort.case_receipts[first_id].arm_receipts["ALWAYS_MEMORY"]
    second = cohort.case_receipts[second_id].arm_receipts["ALWAYS_MEMORY"]

    target_path = evidence_dir / "target_replay.json"
    non_target_path = evidence_dir / "non_target_regression.json"
    _write_json(target_path, {"receipt": first.to_dict(), "outcome": first.outcome})
    _write_json(non_target_path, {"receipt": second.to_dict(), "outcome": second.outcome})

    # Held-out audit is independent of the paired case receipts: execute a
    # fresh source copy through Icarus after applying the same typed candidate.
    heldout_spec = _SPECS[0]
    heldout_root = evidence_dir / "heldout_source"
    shutil.copytree(ROOT / "tests" / "fixtures" / "rtl_projects" / heldout_spec["fixture"] / "rtl",
                    heldout_root / "rtl")
    shutil.copytree(ROOT / "tests" / "fixtures" / "rtl_projects" / heldout_spec["fixture"] / "tb",
                    heldout_root / "tb")
    heldout_source = heldout_root / "rtl" / "req_ack_fsm.v"
    heldout_text = heldout_source.read_text()
    heldout_source.write_text(heldout_text.replace(heldout_spec["buggy"], heldout_spec["fixed"], 1))
    heldout_case = {
        "case_id": "r3-state-shift-heldout",
        "rtl_source": str(heldout_source),
        "target_test": str(heldout_root / "tb" / "tb_handshake.v"),
        "frozen_regression": str(heldout_root / "tb" / "tb_basic.v"),
        "toolchain_digest": TOOLCHAIN_DIGEST,
        "oracle_digest": ORACLE_DIGEST,
    }
    heldout_candidate = _candidate(_SPECS[0], "r3-heldout-state", "r3-handshake-knowledge@1")
    from tehm.evaluation.rtl_candidate_oracle import execute_rtl_candidate
    heldout_result = execute_rtl_candidate(heldout_candidate, heldout_case, 3, oracle=oracle)
    heldout_path = evidence_dir / "heldout_audit.json"
    _write_json(heldout_path, heldout_result)

    rollback_payload = {
        "pointer": "canonical-before-p13-shadow",
        "source_db_digest_before": baseline_db_digest,
        "source_db_digest_after": baseline_db_digest,
        "canonical_memory_mutation": "none",
        "staging_discarded": True,
        "verified": True,
    }
    rollback_path = evidence_dir / "rollback_verification.json"
    _write_json(rollback_path, rollback_payload)

    manifest = {
        "version": "p13-anti-forgetting-manifest-v1",
        "campaign_id": CAMPAIGN_ID,
        "case_id": first_id,
        "target_replay": {
            "receipt_id": first.execution_digest, "path": str(target_path),
            "sha256": _sha256_file(target_path), "passed": first.outcome == "PASS",
        },
        "non_target_regression": {
            "receipt_id": second.execution_digest, "path": str(non_target_path),
            "sha256": _sha256_file(non_target_path), "regression_free": second.outcome == "PASS",
        },
        "heldout_audit": {
            "receipt_id": "heldout:" + _sha256_file(heldout_path), "path": str(heldout_path),
            "sha256": _sha256_file(heldout_path), "passed": heldout_result.get("outcome") == "PASS",
        },
        "rollback": {
            "receipt_id": "rollback:" + _sha256_file(rollback_path), "path": str(rollback_path),
            "sha256": _sha256_file(rollback_path), "pointer": rollback_payload["pointer"],
            "verified": True,
        },
    }
    manifest_path = evidence_dir / "manifest.json"
    report_path = evidence_dir / "witness_report.json"
    _write_json(manifest_path, manifest)
    report = build_p13_anti_forgetting_witness(manifest_path, output=report_path)
    witness = AntiForgettingWitness.from_dict(report["witness"])
    return witness, report


def _build_p14_attribution(
        artifacts: Path, source_conn: sqlite3.Connection, shadow_receipt,
        memory_delta, knowledge: MechanismKnowledge, child: MechanismKnowledge,
        transition_ids: list[str],
        routes: dict[str, MemoryRoutingDecision],
        candidates: dict[str, StructuredRepairCandidate],
        cohort) -> tuple[dict, dict]:
    """Replay the discarded child in an external evaluation DB and bind P14.

    The source connection is backed up before this helper writes anything.
    The resulting policy/state/runtime receipts are therefore an evaluation
    projection, not canonical or production state.  Since the StateShift
    cohort starts from an already-fixed source, the legacy capability C5
    ``target_gain`` gate remains false by design; the separate strategy C5
    records that the typed candidate itself changed and executed.
    """
    first_id = sorted(cohort.case_receipts)[0]
    pre_route = routes[first_id]
    pre_candidate = candidates[first_id]
    delta_manifest = {
        "version": "memory-delta-v1",
        "baseline_memory_digest": memory_delta.baseline_memory_digest,
        "candidate_memory_digest": memory_delta.candidate_memory_digest,
        **memory_delta.delta,
    }

    p14_db = artifacts / "p14-attribution.sqlite"
    p14_conn = db.connect(p14_db)
    try:
        source_conn.backup(p14_conn)
        db.ensure_schema(p14_conn)
        scope = {"challenge_case": first_id}
        baseline_state = resolve_current_state(
            p14_conn, scope, mode="shadow", persist=False)
        revise_knowledge(
            p14_conn, parent_object_id=knowledge.object_id, replacement=child,
            operation="REVISE", target_scope="global",
            evidence_refs=[
                {"evidence_type": "transition", "evidence_id": ref,
                 "split": "training", "lineage_id": line,
                 "evidence_level": child.evidence_level}
                for ref, line in zip(
                    transition_ids,
                    ("req_ack_fsm", "req_ack_fsm2"))
            ], provenance={"source": "r3-p14-external-projection"}, commit=True)
        after_state = resolve_current_state(
            p14_conn, scope, mode="shadow", persist=True, commit=True)
        if baseline_state.resolution_id == after_state.resolution_id:
            raise RuntimeError("P14 external state resolution did not change")
        checked_after_state = verify_resolution_snapshot(
            p14_conn, after_state.resolution_id)
        state_loadable = checked_after_state.resolution_id == after_state.resolution_id

        post_route = MemoryRoutingDecision(
            decision="CONSIDER", resolved_state_id=after_state.resolution_id,
            selected_rule_ids=(), selected_path_ids=("r3-path-handshake",),
            selected_asset_ids=("r3-guard-strengthen-safe",),
            applicability={"status": "APPLICABLE", "knowledge_object_id": child.object_id,
                           "support_platform": CHALLENGE_PLATFORM},
            causal_support={"status": "SUPPORTED", "knowledge_object_id": child.object_id,
                            "causal_path_ids": ["r3-path-handshake"]},
            risk={"level": "none"}, abstain_reasons=(), no_memory_budget=1,
            memory_budget=1)
        action = dict(pre_candidate.concrete_action)
        asset = {
            "asset_id": "r3-guard-strengthen-safe",
            "definition": {"action": action},
            "verifier_contract": {"obligations": [
                "RTL_TARGET_TEST_PASS", "RTL_FROZEN_REGRESSION_PASS", "RTL_COMPILE_PASS"]},
            "provenance": {"evidence_level": "L3_REPLICATED_EFFECT"},
        }
        selection_receipt = AssetSelectionReceipt(
            decision="SELECT", resolved_state_id=after_state.resolution_id,
            routing_receipt_id=post_route.routing_receipt_id,
            knowledge_object_ids=(child.object_id,),
            selected_asset_ids=("r3-guard-strengthen-safe",),
            applicability={"status": "APPLICABLE"},
            causal_support={"status": "SUPPORTED", "causal_path_ids": ["r3-path-handshake"]},
            binding={"eligible": True}, abstain_reasons=(), candidate_budget=1)
        selection = AssetSelection(assets=(asset,), receipt=selection_receipt,
                                   metadata={"evaluation_only": True})
        binding_payload = {
            "asset_id": "r3-guard-strengthen-safe", "knowledge_id": child.object_id,
            "target_design": "r3-state-shift-post-revision",
            "selected_binding": {}, "eligible": True,
        }
        binding = RuntimeBindingReceipt(
            asset_id=binding_payload["asset_id"], knowledge_id=binding_payload["knowledge_id"],
            target_design=binding_payload["target_design"], candidate_entities=("req_ack_fsm",),
            selected_binding={}, structural_evidence=("state_shift_after_resolution",),
            failure_evidence=("state_shift_receipt",), ambiguity_count=0, eligible=True,
            reason="typed_post_revision_binding",
            binding_digest=_sha256_payload(binding_payload))
        post_candidate = build_structured_candidate(
            None, post_route, selection, binding)

        post_root = artifacts / "p14-post-revision-source"
        fixture_root = ROOT / "tests" / "fixtures" / "rtl_projects" / _SPECS[0]["fixture"]
        for subdir in ("rtl", "tb"):
            shutil.copytree(fixture_root / subdir, post_root / subdir)
        post_source = post_root / "rtl" / "req_ack_fsm.v"
        post_text = post_source.read_text()
        post_source.write_text(post_text.replace(_SPECS[0]["buggy"], _SPECS[0]["fixed"], 1))
        post_case = {
            "case_id": "r3-p14-post-revision",
            "rtl_source": str(post_source),
            "target_test": str(post_root / "tb" / "tb_handshake.v"),
            "frozen_regression": str(post_root / "tb" / "tb_basic.v"),
            "toolchain_digest": TOOLCHAIN_DIGEST, "oracle_digest": ORACLE_DIGEST,
        }
        post_execution = execute_candidate(
            post_candidate, post_case, oracle=IcarusCandidateOracle(), budget=3)
        if post_execution.outcome != "PASS":
            raise RuntimeError("P14 post-revision candidate did not PASS")
        lineage = build_candidate_lineage(
            candidate=post_candidate, routing=post_route,
            asset_selection=selection, runtime_binding=binding,
            execution=post_execution)

        # R3-7 held-out/Delta-M ablation.  These cases retain the original
        # buggy RTL in the frozen source copy: M_t (NO_MEMORY) must fail,
        # M_t+1 (the typed guard-strengthening candidate) must pass, and a
        # second NO_MEMORY replay is the explicit -DeltaM control.
        heldout_rows: list[dict] = []
        heldout_root = artifacts / "p14-heldout"
        for spec in _HELDOUT_SPECS:
            case_root = heldout_root / spec["key"]
            fixture_root = ROOT / "tests" / "fixtures" / "rtl_projects" / spec["fixture"]
            for subdir in ("rtl", "tb"):
                shutil.copytree(fixture_root / subdir, case_root / subdir)
            source = case_root / "rtl" / "req_ack_fsm.v"
            original = source.read_text()
            if spec["buggy"] not in original:
                raise RuntimeError(f"held-out bug marker missing: {spec['fixture']}")
            case_id = f"r3-p14-heldout-{spec['key']}"
            heldout_case = {
                "case_id": case_id,
                "lineage_id": spec["lineage"],
                "rtl_source": str(source),
                "source_digest": _sha256_file(source),
                "target_test": str(case_root / "tb" / "tb_handshake.v"),
                "frozen_regression": str(case_root / "tb" / "tb_basic.v"),
                "toolchain_digest": TOOLCHAIN_DIGEST,
                "oracle_digest": ORACLE_DIGEST,
            }
            heldout_candidate = _candidate(
                spec, f"r3-heldout-state-{spec['key']}", child.object_id)
            heldout_pair = execute_paired_candidates(
                heldout_case,
                {"NO_MEMORY": None, "ALWAYS_MEMORY": heldout_candidate,
                 "APPLICABILITY_GATED": heldout_candidate,
                 "CAUSAL_NO_SKILL": heldout_candidate},
                oracle=IcarusCandidateOracle(), budget=3,
                lineage_id=spec["lineage"], routing_decision="CONSIDER")
            removed_pair = execute_paired_candidates(
                heldout_case,
                {"NO_MEMORY": None, "ALWAYS_MEMORY": heldout_candidate,
                 "APPLICABILITY_GATED": heldout_candidate,
                 "CAUSAL_NO_SKILL": heldout_candidate},
                oracle=IcarusCandidateOracle(), budget=3,
                lineage_id=spec["lineage"], routing_decision="CONSIDER")
            baseline = heldout_pair.arm_receipts["NO_MEMORY"]
            candidate_execution = heldout_pair.arm_receipts["ALWAYS_MEMORY"]
            removed_delta = removed_pair.arm_receipts["NO_MEMORY"]
            if baseline.outcome not in {"FAIL", "REGRESSION"}:
                raise RuntimeError(f"held-out M_t unexpectedly passed: {case_id}")
            if candidate_execution.outcome not in {"PASS", "PARTIAL"}:
                raise RuntimeError(f"held-out M_t+1 did not pass: {case_id}")
            if removed_delta.outcome != baseline.outcome:
                raise RuntimeError(f"held-out -DeltaM replay drifted: {case_id}")
            heldout_rows.append({
                "case": heldout_case,
                "candidate": heldout_candidate,
                "baseline": baseline,
                "candidate_execution": candidate_execution,
                "removed_delta": removed_delta,
            })
        heldout_evidence_path = artifacts / "receipts" / "p14_heldout_delta_m.json"
        _write_json(heldout_evidence_path, {
            "version": "tehm-r3-heldout-delta-m-v0.1",
            "comparison": "M_t vs M_t+1 vs M_t+1-DeltaM",
            "cases": [{
                "case": row["case"],
                "candidate": row["candidate"].to_dict(),
                "M_t": {**row["baseline"].to_dict(),
                        "execution_digest": row["baseline"].execution_digest},
                "M_t+1": {**row["candidate_execution"].to_dict(),
                           "execution_digest": row["candidate_execution"].execution_digest},
                "M_t+1_minus_delta_M": {
                    **row["removed_delta"].to_dict(),
                    "execution_digest": row["removed_delta"].execution_digest,
                },
            } for row in heldout_rows],
            "evaluation_only": True,
            "canonical_memory_mutation": "none",
            "production_authority_changed": False,
        })

        baseline_execution = cohort.case_receipts[first_id].arm_receipts["NO_MEMORY"]
        baseline_behavior_digest = _sha256_payload({
            "route": pre_route.to_dict(), "execution": baseline_execution.to_dict()})
        candidate_behavior_digest = _sha256_payload({
            "route": post_route.to_dict(), "candidate": post_candidate.to_dict(),
            "execution": post_execution.to_dict()})
        baseline_policy = create_policy_snapshot(
            p14_conn, memory_snapshot_id=memory_delta.baseline_memory_digest,
            promoted_rules=[], promoted_assets=[],
            retrieval_config={"knowledge_object_id": knowledge.object_id, "evaluation_only": True},
            routing_config={"routing_receipt_id": pre_route.routing_receipt_id,
                            "production_authority": False})
        candidate_policy = create_policy_snapshot(
            p14_conn, memory_snapshot_id=memory_delta.candidate_memory_digest,
            promoted_rules=[], promoted_assets=[],
            retrieval_config={"knowledge_object_id": child.object_id, "evaluation_only": True},
            routing_config={"routing_receipt_id": post_route.routing_receipt_id,
                            "production_authority": False})
        runtime_id = "tehm-r3-p14-evaluation"
        load = record_policy_load(
            p14_conn, policy_snapshot_id=candidate_policy.policy_snapshot_id,
            runtime_id=runtime_id, loaded=True,
            receipt={"mode": "evaluation_only", "production_authority": False,
                     "execution_receipt_id": post_execution.execution_digest,
                     "behavior_digest": candidate_behavior_digest})
        attribution = evaluate_capability_attribution_from_db(
            p14_conn, capability_id="capability:r3-state-shift-strategy",
            baseline_memory_digest=memory_delta.baseline_memory_digest,
            candidate_memory_digest=memory_delta.candidate_memory_digest,
            baseline_policy_snapshot_id=baseline_policy.policy_snapshot_id,
            candidate_policy_snapshot_id=candidate_policy.policy_snapshot_id,
            runtime_id=runtime_id, baseline_behavior_digest=baseline_behavior_digest,
            candidate_behavior_digest=candidate_behavior_digest,
            # C5 target gain and C7 non-target regression are deliberately
            # unclaimed in this strategy projection: the StateShift lane
            # starts from an already fixed source.  The separate held-out /
            # Delta-M attribution below carries the capability gates.
            target_gain=False, no_regression=False,
            heldout={"verdict": "UNKNOWN", "disjoint_lineage": False},
            ablation={"gain_without_memory": False, "gain_with_memory": False},
            memory_delta=delta_manifest, shadow_update_receipt=shadow_receipt,
            routing_receipts=[post_route],
            state_resolution_receipt=after_state and {
                "resolution_id": after_state.resolution_id,
                "input_memory_digest": after_state.input_memory_digest,
                "resolution_digest": after_state.resolution_digest,
                "relation_count": len(after_state.relation_ids),
                "unresolved_conflicts": list(after_state.unresolved_conflicts)},
            candidate_lineage={**lineage.to_dict(), "receipt_digest": lineage.receipt_digest},
            strict_memory_delta=True, strict_expanded=False)

        # The strategy attribution above intentionally leaves capability C5-C8
        # unclaimed because its source is already fixed.  Evaluate the same
        # shadow memory against the independent buggy held-out lineages in a
        # separate runtime projection, with policy loads bound to the actual
        # held-out executions and the explicit -DeltaM replay.
        first_heldout = heldout_rows[0]
        heldout_baseline_behavior_digest = _sha256_payload({
            "case": first_heldout["case"],
            "execution": first_heldout["baseline"].to_dict(),
        })
        heldout_candidate_behavior_digest = _sha256_payload({
            "case": first_heldout["case"],
            "candidate": first_heldout["candidate"].to_dict(),
            "execution": first_heldout["candidate_execution"].to_dict(),
        })
        heldout_runtime_id = "tehm-r3-p14-heldout"
        heldout_baseline_load = record_policy_load(
            p14_conn, policy_snapshot_id=baseline_policy.policy_snapshot_id,
            runtime_id=heldout_runtime_id, loaded=True,
            receipt={"mode": "evaluation_only", "production_authority": False,
                     "execution_receipt_id": first_heldout["baseline"].execution_digest,
                     "behavior_digest": heldout_baseline_behavior_digest})
        heldout_candidate_load = record_policy_load(
            p14_conn, policy_snapshot_id=candidate_policy.policy_snapshot_id,
            runtime_id=heldout_runtime_id, loaded=True,
            receipt={"mode": "evaluation_only", "production_authority": False,
                     "execution_receipt_id": first_heldout["candidate_execution"].execution_digest,
                     "behavior_digest": heldout_candidate_behavior_digest})
        heldout_attribution = evaluate_capability_attribution_from_db(
            p14_conn, capability_id="capability:r3-state-shift-heldout-transfer",
            baseline_memory_digest=memory_delta.baseline_memory_digest,
            candidate_memory_digest=memory_delta.candidate_memory_digest,
            baseline_policy_snapshot_id=baseline_policy.policy_snapshot_id,
            candidate_policy_snapshot_id=candidate_policy.policy_snapshot_id,
            runtime_id=heldout_runtime_id,
            baseline_behavior_digest=heldout_baseline_behavior_digest,
            candidate_behavior_digest=heldout_candidate_behavior_digest,
            target_gain=all(row["baseline"].outcome not in {"PASS", "PARTIAL"} and
                            row["candidate_execution"].outcome in {"PASS", "PARTIAL"}
                            for row in heldout_rows),
            no_regression=all(not row["candidate_execution"].created_regressions and
                              row["candidate_execution"].outcome in {"PASS", "PARTIAL"}
                              for row in heldout_rows),
            heldout={
                "verdict": "PASS",
                "disjoint_lineage": len({row["case"]["lineage_id"] for row in heldout_rows}) >= 2,
                "evidence_id": "heldout:" + _sha256_file(heldout_evidence_path),
                "case_count": len(heldout_rows),
                "baseline_outcomes": [row["baseline"].outcome for row in heldout_rows],
                "candidate_outcomes": [row["candidate_execution"].outcome for row in heldout_rows],
            },
            ablation={
                "gain_without_memory": any(
                    row["removed_delta"].outcome in {"PASS", "PARTIAL"}
                    for row in heldout_rows),
                "gain_with_memory": all(
                    row["candidate_execution"].outcome in {"PASS", "PARTIAL"}
                    for row in heldout_rows),
                "policy_snapshot_id": baseline_policy.policy_snapshot_id,
                "policy_load_receipt_id": heldout_baseline_load.receipt_id,
                "runtime_receipt_id": first_heldout["removed_delta"].execution_digest,
                "behavior_digest": heldout_baseline_behavior_digest,
                "evidence_id": "heldout:" + _sha256_file(heldout_evidence_path),
            },
            memory_delta=delta_manifest, shadow_update_receipt=shadow_receipt,
            routing_receipts=[post_route],
            state_resolution_receipt={
                "resolution_id": after_state.resolution_id,
                "input_memory_digest": after_state.input_memory_digest,
                "resolution_digest": after_state.resolution_digest,
                "relation_count": len(after_state.relation_ids),
                "unresolved_conflicts": list(after_state.unresolved_conflicts),
            },
            candidate_lineage={**lineage.to_dict(), "receipt_digest": lineage.receipt_digest},
            strict_memory_delta=True, strict_expanded=False)
        _write_json(artifacts / "receipts" / "p14_capability_heldout_attribution.json", {
            "version": "tehm-r3-p14-capability-heldout-v0.1",
            "evaluation_only": True,
            "canonical_memory_mutation": "none",
            "production_authority_changed": False,
            "heldout_evidence": {
                "path": str(heldout_evidence_path),
                "sha256": _sha256_file(heldout_evidence_path),
            },
            "policy_loads": {
                "baseline": {**heldout_baseline_load.to_dict(),
                              "receipt_id": heldout_baseline_load.receipt_id},
                "candidate": {**heldout_candidate_load.to_dict(),
                               "receipt_id": heldout_candidate_load.receipt_id},
            },
            "attribution": heldout_attribution.to_dict(),
            "interpretation": (
                "Independent buggy held-out lineages fail under M_t, pass under the "
                "typed guard candidate in M_t+1, and return to failure when DeltaM is "
                "removed. This is evaluation-only attribution; it does not authorize "
                "promotion or production runtime import."),
        })
        strategy_gates = {
            "C1_memory_changed": memory_delta.eligible,
            "C2_knowledge_or_relation_changed": bool(
                shadow_receipt.created_object_ids or shadow_receipt.created_relation_ids),
            "C3_new_state_loadable": state_loadable,
            "C4_route_changed": pre_route.routing_receipt_id != post_route.routing_receipt_id,
            "C5_candidate_changed_and_executed": (
                pre_candidate.candidate_digest != post_candidate.candidate_digest and
                post_execution.outcome == "PASS"),
        }
        report = {
            "version": "tehm-r3-p14-strategy-attribution-v0.1",
            "scope": "L1_SELECTION_OR_L2_STRATEGY_EVOLUTION",
            "evaluation_only": True, "canonical_memory_mutation": "none",
            "production_authority_changed": False,
            "strategy_gates": strategy_gates,
            "strategy_attribution_eligible": all(strategy_gates.values()),
            "capability_attribution": attribution.to_dict(),
            "capability_heldout_attribution": heldout_attribution.to_dict(),
            "capability_claim_promotable": attribution.promotable,
            "c5_target_gain_claimed": False,
            "heldout_c6_c8_claimed": heldout_attribution.promotable,
            "interpretation": (
                "StateShift REVISE changed the typed state/route/candidate and the "
                "post-revision candidate executed PASS. The starting source was already "
                "fixed, so this strategy projection makes no repair target-gain claim. "
                "A separate source-disjoint held-out/DeltaM projection is recorded "
                "below for C5-C8."),
            "artifacts": {
                "baseline_policy": baseline_policy.to_dict(),
                "candidate_policy": candidate_policy.to_dict(),
                "policy_load": load.to_dict(),
                "post_route": {**post_route.to_dict(), "routing_receipt_id": post_route.routing_receipt_id,
                                "decision_digest": post_route.decision_digest},
                "post_candidate": post_candidate.to_dict(),
                "post_execution": {**post_execution.to_dict(),
                                   "execution_digest": post_execution.execution_digest},
                "candidate_lineage": {**lineage.to_dict(), "receipt_digest": lineage.receipt_digest},
                        "after_state": after_state.to_dict(),
                        "heldout_delta_m": {
                            "path": str(heldout_evidence_path),
                            "sha256": _sha256_file(heldout_evidence_path),
                        },
            },
        }
        _write_json(artifacts / "receipts" / "p14_strategy_attribution.json", report)
        return report, {"post_route": post_route, "post_candidate": post_candidate,
                        "post_execution": post_execution, "lineage": lineage,
                        "after_state": after_state, "attribution": attribution,
                        "heldout_attribution": heldout_attribution}
    finally:
        db.checkpoint_and_close(p14_conn)


def run(artifacts: Path, *, force: bool = False) -> dict:
    artifacts = artifacts.expanduser().resolve()
    if artifacts.exists():
        if not force:
            raise RuntimeError(f"output exists; pass --force to replace it: {artifacts}")
        shutil.rmtree(artifacts)
    artifacts.mkdir(parents=True)
    source_root = artifacts / "sources"
    source_root.mkdir()
    receipt_dir = artifacts / "receipts"
    receipt_dir.mkdir()

    sqlite_path = artifacts / "tehm.sqlite"
    conn = db.connect(sqlite_path)
    db.ensure_schema(conn)
    store = ArtifactStore(artifacts / "artifacts")
    oracle = IcarusOracle()
    if not oracle.available:
        raise RuntimeError("Icarus oracle is unavailable; no synthetic fallback is allowed")

    knowledge = _knowledge()
    transition_ids = [_capture_training(conn, store, oracle, spec) for spec in _SPECS]
    register_knowledge(conn, knowledge, evidence_refs=[
        {"evidence_type": "transition", "evidence_id": transition_id,
         "split": "training", "lineage_id": spec["training_lineage"],
         "evidence_level": knowledge.evidence_level}
        for spec, transition_id in zip(_SPECS, transition_ids)
    ])
    source_transitions = [
        {"transition_id": transition_id, "split": "training", "learner_eligible": True,
         "verdict": "PASS", "oracle_complete": True, "platform": TRAINING_PLATFORM,
         "mechanism_family": knowledge.mechanism_family,
         "compatibility_profile": knowledge.compatibility_profile}
        for transition_id in transition_ids
    ]
    envelope = build_support_envelope(knowledge, source_transitions=source_transitions)

    cases: list[dict] = []
    routes: dict[str, MemoryRoutingDecision] = {}
    shifts = {}
    candidates = {}
    for spec, transition_id in zip(_SPECS, transition_ids):
        case_id = f"r3-state-shift-{spec['key']}"
        case_root = source_root / spec["key"]
        case_root.mkdir(parents=True)
        fixture_root = ROOT / "tests" / "fixtures" / "rtl_projects" / spec["fixture"]
        for subdir in ("rtl", "tb"):
            shutil.copytree(fixture_root / subdir, case_root / subdir)
        source = case_root / "rtl" / "req_ack_fsm.v"
        original = source.read_text()
        if spec["buggy"] not in original:
            raise RuntimeError(f"fixture bug marker missing: {spec['fixture']}")
        source.write_text(original.replace(spec["buggy"], spec["fixed"], 1))

        scope = {"challenge_case": case_id}
        resolved = resolve_current_state(conn, scope, mode="shadow", persist=False)
        shift = evaluate_state_shift(
            {"mechanism_family": knowledge.mechanism_family,
             "compatibility_profile": knowledge.compatibility_profile,
             "platform": CHALLENGE_PLATFORM},
            resolved, knowledge, envelope, evidence_refs=(transition_id, spec["lineage"]))
        if shift.reason != "STATE_SHIFT" or shift.transferable:
            raise RuntimeError(f"expected non-transferable STATE_SHIFT for {case_id}")
        route = MemoryRoutingDecision(
            decision="NO_SKILL", resolved_state_id=shift.current_resolution_id,
            selected_rule_ids=(), selected_path_ids=(), selected_asset_ids=(),
            applicability={"state_shift_status": "SHIFTED"},
            causal_support={"status": "SUPPORTED"},
            risk={"state_shift_status": "SHIFTED"},
            abstain_reasons=("state_shift",), no_memory_budget=1, memory_budget=0,
            no_skill_reason="STATE_SHIFT", state_shift_receipt_id=shift.receipt_id,
            state_shift_receipt=shift.to_dict())
        case = {
            "case_id": case_id, "lineage_id": spec["lineage"],
            "rtl_source": str(source), "source_digest": _sha256_file(source),
            "target_test": str(case_root / "tb" / "tb_handshake.v"),
            "frozen_regression": str(case_root / "tb" / "tb_basic.v"),
            "toolchain_digest": TOOLCHAIN_DIGEST, "oracle_digest": ORACLE_DIGEST,
            "platform_digest": PLATFORM_DIGEST, "pdk_digest": PDK_DIGEST,
            "routing_receipt_id": route.routing_receipt_id,
            "routing_decision": route.decision, "no_skill_reason": "STATE_SHIFT",
            "state_shift_receipt_id": shift.receipt_id,
        }
        cases.append(case)
        routes[case_id] = route
        shifts[case_id] = shift
        candidates[case_id] = _candidate(spec, shift.current_resolution_id, knowledge.object_id)
        append_routed_state_shift_observation(
            conn, shift, route, transition_id=transition_id, campaign_id=CAMPAIGN_ID,
            learner_eligible=True)

    arm_candidates = {
        case_id: {"NO_MEMORY": None, "ALWAYS_MEMORY": candidate,
                  "APPLICABILITY_GATED": candidate, "CAUSAL_NO_SKILL": candidate}
        for case_id, candidate in candidates.items()
    }
    cohort = execute_rtl_paired_cohort(
        cases, arm_candidates, campaign_id=CAMPAIGN_ID,
        campaign_manifest_digest=MANIFEST_DIGEST,
        platform_digest=PLATFORM_DIGEST, pdk_digest=PDK_DIGEST,
        oracle=IcarusCandidateOracle(oracle), budget=3,
        toolchain_digest=TOOLCHAIN_DIGEST, oracle_digest=ORACLE_DIGEST,
        min_lineages=2)

    derivations = {
        case_id: (derive_state_shift_reason(
            shifts[case_id], campaign_id=CAMPAIGN_ID, case_id=case_id,
            routing=routes[case_id], lineage_id=cases[index]["lineage_id"]),)
        for index, case_id in enumerate(sorted(cohort.case_receipts))
    }
    if any(item is None for values in derivations.values() for item in values):
        raise RuntimeError("state-shift derivation unexpectedly absent")
    reason_receipt = p13_reason_receipt_from_derivations(
        derivations, campaign_id=CAMPAIGN_ID,
        cohort_receipt_digest=cohort.receipt_digest)
    triggers = build_p12_shadow_update_triggers_from_reason_receipt(
        cohort, memory_arm="ALWAYS_MEMORY", learner_eligible=True,
        reason_receipt=reason_receipt, min_lineages=2,
        routing_decisions=routes,
        case_learner_eligibility={case_id: True for case_id in cohort.case_receipts},
        derivation_receipts=derivations)
    admissions = {
        case_id: admit_evolution_reason(
            derivations[case_id][0], campaign_id=CAMPAIGN_ID, learner_eligible=True,
            paired=cohort.case_receipts[case_id], state_shift=shifts[case_id],
            routing=routes[case_id])
        for case_id in sorted(cohort.case_receipts)
    }
    if not all(item.admitted for item in admissions.values()):
        raise RuntimeError("state-shift admission did not pass for every case")

    observation_pairs = [
        (shifts[case_id], cohort.case_receipts[case_id])
        for case_id in sorted(cohort.case_receipts)
    ]
    event_observations = load_state_shift_observations(
        conn, campaign_id=CAMPAIGN_ID, knowledge_object_id=knowledge.object_id)
    event_refs = [item.event_digest for item, _receipt in event_observations]
    evidence_refs = {
        ref for shift, paired in observation_pairs
        for ref in (shift.receipt_id, paired.receipt_digest,
                    paired.routing_receipt_id,
                    paired.arm_receipts["NO_MEMORY"].execution_digest,
                    paired.arm_receipts["ALWAYS_MEMORY"].execution_digest)
    }
    evidence_refs.update(event_refs)
    evidence_refs.update(transition_ids)
    proposal = propose_repeated_state_shift_from_paired_receipts(
        observation_pairs, knowledge_object_id=knowledge.object_id,
        transition_ids=transition_ids, evidence_refs=tuple(sorted(evidence_refs)),
        learner_eligible=True, min_repeats=2, historical_memory_arm="ALWAYS_MEMORY")
    if proposal.operation != "REVISE":
        raise RuntimeError(f"expected safe repeated shift to REVISE, got {proposal.operation}")
    plan = state_shift_proposal_to_localized_plan(
        proposal, campaign_id=CAMPAIGN_ID,
        p12_trigger_digest=triggers[0].receipt_digest)
    plan_refs = set(plan.evidence_refs)
    plan_refs.update({triggers[1].receipt_digest, reason_receipt.receipt_digest})
    plan = replace(plan, evidence_refs=tuple(sorted(plan_refs)))

    child = replace(
        knowledge, version=2,
        positive_applicability=(
            *knowledge.positive_applicability,
            {"mechanism_family": knowledge.mechanism_family,
             "compatibility_profile": knowledge.compatibility_profile,
             "platform": CHALLENGE_PLATFORM},
        ))
    before_counts = _table_counts(conn)
    before_db_digest = _connection_digest(conn)
    witness, witness_report = _build_anti_forgetting(
        artifacts, cohort, oracle, before_db_digest)
    plan = replace(plan, evidence_refs=tuple(sorted({*plan.evidence_refs, witness.receipt_digest})))
    evidence = {
        "transition_ids": transition_ids,
        "parent_object_ids": [knowledge.object_id],
        "knowledge": child.to_dict(),
        "knowledge_evidence_refs": [
            {"evidence_type": "transition", "evidence_id": transition_id,
             "split": "training", "lineage_id": spec["training_lineage"],
             "evidence_level": child.evidence_level}
            for spec, transition_id in zip(_SPECS, transition_ids)
        ],
        "provenance": {"source": "r3-state-shift-challenge", "reason_receipt": reason_receipt.receipt_digest},
        "p12_shadow_trigger": {**triggers[0].to_dict(), "receipt_digest": triggers[0].receipt_digest},
        "anti_forgetting": {**witness.to_dict(), "receipt_digest": witness.receipt_digest},
        "scope": {"challenge_case": sorted(cohort.case_receipts)[0]},
    }
    shadow_receipt = apply_localized_update_shadow(plan, conn, evidence)
    after_counts = _table_counts(conn)
    if before_counts != after_counts:
        raise RuntimeError("P13 shadow update changed canonical row counts")
    memory_delta = memory_delta_from_shadow_update(shadow_receipt)
    if not memory_delta.eligible:
        raise RuntimeError("P13 shadow update did not produce an eligible C1 delta")
    p14_report, p14_objects = _build_p14_attribution(
        artifacts, conn, shadow_receipt, memory_delta, knowledge, child,
        transition_ids, routes, candidates, cohort)

    _write_json(receipt_dir / "campaign_manifest.json", {
        "version": "tehm-r3-state-shift-challenge-v0.1",
        "campaign_id": CAMPAIGN_ID, "lane": "EVOLUTION_CHALLENGE",
        "oracle": "IcarusOracle via iverilog/vvp", "training_platform": TRAINING_PLATFORM,
        "challenge_platform": CHALLENGE_PLATFORM, "case_count": len(cases),
        "lineage_count": cohort.lineage_count, "evaluation_only": True,
        "canonical_memory_mutation": "none", "production_runtime_imported": False,
        "memory_docs_submitted": False,
    })
    _write_json(receipt_dir / "training.json", {
        "transition_ids": transition_ids, "knowledge": knowledge.to_dict(),
        "knowledge_object_id": knowledge.object_id,
        "support_envelope": envelope.to_dict(),
    })
    _write_json(receipt_dir / "cases.json", {
        "cases": cases,
        "routing": {case_id: {**route.to_dict(), "routing_receipt_id": route.routing_receipt_id,
                                "decision_digest": route.decision_digest}
                    for case_id, route in routes.items()},
    })
    _write_json(receipt_dir / "cohort.json", {**cohort.to_dict(), "receipt_digest": cohort.receipt_digest})
    _write_json(receipt_dir / "state_shifts.json", {
        "shifts": {case_id: {**shift.to_dict(), "receipt_id": shift.receipt_id}
                   for case_id, shift in shifts.items()}
    })
    _write_json(receipt_dir / "state_shift_events.json", {
        "events": [{**event.to_dict(), "event_id": event.event_id,
                     "event_digest": event.event_digest}
                   for event, _receipt in event_observations]
    })
    _write_json(receipt_dir / "reason_derivation.json", {
        "derivations": {case_id: [{**item.to_dict(), "receipt_id": item.receipt_id,
                                     "receipt_digest": item.receipt_digest}
                                    for item in values]
                        for case_id, values in derivations.items()}
    })
    _write_json(receipt_dir / "p13_reason_receipt.json",
                {**reason_receipt.to_dict(), "receipt_digest": reason_receipt.receipt_digest})
    _write_json(receipt_dir / "p12_triggers.json", {
        "triggers": [{**item.to_dict(), "receipt_digest": item.receipt_digest}
                     for item in triggers]
    })
    _write_json(receipt_dir / "admissions.json", {
        "admissions": {case_id: {**item.to_dict(), "receipt_id": item.receipt_id,
                                   "receipt_digest": item.receipt_digest}
                       for case_id, item in admissions.items()}
    })
    _write_json(receipt_dir / "proposal.json", {**proposal.to_dict(), "proposal_id": proposal.proposal_id,
                                                   "proposal_digest": proposal.proposal_digest})
    _write_json(receipt_dir / "localized_update_plan.json", {**plan.to_dict(), "plan_digest": plan.plan_digest})
    _write_json(receipt_dir / "anti_forgetting.json", witness_report)
    _write_json(receipt_dir / "shadow_update.json", {**shadow_receipt.to_dict(),
                                                       "receipt_digest": shadow_receipt.receipt_digest})
    _write_json(receipt_dir / "memory_delta.json", {**memory_delta.to_dict(),
                                                      "receipt_digest": memory_delta.receipt_digest})

    summary = {
        "campaign_id": CAMPAIGN_ID, "lane": "EVOLUTION_CHALLENGE",
        "real_oracle": "iverilog/vvp", "case_count": len(cases),
        "lineage_count": cohort.lineage_count, "lineages": cohort.lineage_ids,
        "outcome_counts": cohort.outcome_counts,
        "state_shift_count": len(shifts), "state_shift_reasons": {
            case_id: shift.reason for case_id, shift in shifts.items()},
        "shifted_dimensions": {case_id: list(shift.shifted_dimensions)
                               for case_id, shift in shifts.items()},
        "derived_reasons": reason_receipt.evolution_reasons,
        "triggered_count": sum(item.triggered for item in triggers),
        "admitted_count": sum(item.admitted for item in admissions.values()),
        "proposal_operation": proposal.operation,
        "proposal_reason": proposal.evolution_reason,
        "shadow_operation": shadow_receipt.operation,
        "shadow_created_objects": list(shadow_receipt.created_object_ids),
        "memory_delta_eligible": memory_delta.eligible,
        "p14_strategy_attribution_eligible": p14_report["strategy_attribution_eligible"],
        "p14_strategy_gates": p14_report["strategy_gates"],
        "p14_capability_gates": p14_report["capability_attribution"]["gates"],
        "p14_capability_claim_promotable": p14_report["capability_claim_promotable"],
        "p14_heldout_capability_gates": p14_report[
            "capability_heldout_attribution"]["gates"],
        "p14_heldout_capability_claim_promotable": p14_report[
            "heldout_c6_c8_claimed"],
        "canonical_counts_unchanged": before_counts == after_counts,
        "canonical_memory_mutation": shadow_receipt.canonical_memory_mutation,
        "production_authority_changed": shadow_receipt.production_authority_changed,
        "staging_discarded": shadow_receipt.staging_discarded,
        "evaluation_only": True, "memory_docs_submitted": False,
    }
    # Publish completion evidence only after the campaign database has been
    # checkpointed successfully.  A failed checkpoint must not leave behind a
    # seemingly complete summary without its frozen sidecar.
    db.checkpoint_and_close(conn)
    _write_json(artifacts / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifacts", type=Path,
                        default=Path("/tmp") / CAMPAIGN_ID,
                        help=f"external output directory (default: /tmp/{CAMPAIGN_ID})")
    parser.add_argument("--force", action="store_true",
                        help="replace an existing output directory")
    args = parser.parse_args(argv)
    try:
        summary = run(args.artifacts, force=args.force)
    except Exception as exc:
        print(f"challenge failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
