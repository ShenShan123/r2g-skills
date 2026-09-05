#!/usr/bin/env python3
"""Bind the real ORFS interference shadow receipt to P14 attribution.

This is an evaluation-only projection.  It replays the immutable P13 shadow
receipt in a disposable SQLite copy, creates database-bound policy snapshots
and runtime-load receipts, and records the distinction between an L2 safety
strategy gain (vetoing harmful memory) and a capability gain.  The source
shadow database is opened read-only and is never promoted or imported into a
production runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts import MemoryQuery, MemoryRoutingDecision  # noqa: E402
from scripts import run_r3_orfs_interference_shadow as shadow  # noqa: E402
from tehm import db  # noqa: E402
from tehm.capability import (  # noqa: E402
    create_policy_snapshot, evaluate_capability_attribution_from_db,
    record_policy_load,
)
from tehm.capability.delta import MemoryDeltaReceipt  # noqa: E402
from tehm.evolution.apply_update import AppliedShadowUpdateReceipt  # noqa: E402
from tehm.evolution.admission import EvolutionAdmissionReceipt  # noqa: E402
from tehm.evolution.local_revision import LocalizedUpdatePlan  # noqa: E402
from tehm.evolution.interference_revision import (  # noqa: E402
    MemoryInterferenceEvolutionProposal,
)
from tehm.evolution.p12_shadow_trigger import (  # noqa: E402
    P12ShadowUpdateTriggerReceipt, P13EvolutionReasonReceipt,
)
from tehm.ids import stable_dumps  # noqa: E402
from tehm.knowledge import MechanismKnowledge  # noqa: E402
from tehm.knowledge.revision import revise_knowledge  # noqa: E402
from tehm.state import resolve_current_state, verify_resolution_snapshot  # noqa: E402
from tehm.evolution.anti_forgetting import AntiForgettingWitness  # noqa: E402
from tehm.evaluation.orfs_cohort import OrfsPairedCohortReceipt  # noqa: E402
from tehm.retrieval.memory_router import route_memory  # noqa: E402


VERSION = "tehm-r3-orfs-interference-p14-v0.1"
DEFAULT_SHADOW = Path(
    "/data1/zhangdy/tehm-campaigns/tehm-r3-orfs-interference-shadow-20260903"
)
DEFAULT_CHALLENGE = Path(
    "/data1/zhangdy/tehm-campaigns/tehm-r3-orfs-interference-challenge-20260903"
)


class OrfsInterferenceAttributionError(ValueError):
    """P14 projection input or replay is incomplete."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise OrfsInterferenceAttributionError(f"cannot read JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise OrfsInterferenceAttributionError(f"JSON must be an object: {path}")
    return payload


def _child(parent: MechanismKnowledge) -> MechanismKnowledge:
    """Reconstruct the exact typed child used by the P13 producer."""
    negative = ({
        "mechanism_family": parent.mechanism_family,
        "compatibility_profile": parent.compatibility_profile,
        "platform": "sky130hs",
        "core_utilization": "99",
        "interference_signature": "forced_memory_high_utilization",
    },)
    return MechanismKnowledge(
        knowledge_id="r3-orfs-density-relief-specialized", version=1,
        mechanism_family=parent.mechanism_family,
        compatibility_profile=parent.compatibility_profile,
        antecedent=dict(parent.antecedent), intervention=dict(parent.intervention),
        mediated_effects=parent.mediated_effects,
        expected_outcome=dict(parent.expected_outcome),
        positive_applicability=parent.positive_applicability,
        negative_applicability=negative,
        preserved_obligations=parent.preserved_obligations,
        known_failure_modes=tuple(sorted({
            *parent.known_failure_modes,
            "memory_interference:forced_high_utilization",
        })),
        causal_path_ids=parent.causal_path_ids,
        evidence_level=parent.evidence_level,
        support_lineages=parent.support_lineages,
        status="shadow",
    )


def _routing_query(case: dict, candidate, parent: MechanismKnowledge) -> MemoryQuery:
    """Freeze query inputs, not the desired decision or interference label.

    A proposed config edit is deliberately not relabelled as a current-state
    fact. Historical cases lack that observation; inventing it here would
    manufacture applicability evidence just to obtain the expected veto.
    """
    return MemoryQuery(
        query_plan={
            "target_scope": "global", "check": case["target_check"],
            "platform": case["platform"],
            "mechanism_family": parent.mechanism_family,
            "compatibility_profile": parent.compatibility_profile,
            "proposed_action": json.loads(stable_dumps(candidate.concrete_action)),
        }, context_ref=case["source_digest"])


def audit_router_outputs(conn, cases: list[dict], candidates: dict,
                         parent: MechanismKnowledge, recorded: dict) -> dict:
    """Recompute routes from the stored state without promoting hypotheses."""
    case_ids = [case["case_id"] for case in cases]
    if (not case_ids or len(set(case_ids)) != len(case_ids) or
            set(case_ids) != set(candidates) or set(case_ids) != set(recorded)):
        raise OrfsInterferenceAttributionError("router audit requires exact non-empty case coverage")
    rows = {}
    for case in cases:
        case_id = case["case_id"]
        query = _routing_query(case, candidates[case_id], parent)
        actual = route_memory(conn, query, no_memory_budget=1, memory_budget=1,
                              persist_state=False, commit=False)
        previous = recorded[case_id]
        rows[case_id] = {
            "query": query.to_dict(),
            "actual": {**actual.to_dict(), "routing_receipt_id": actual.routing_receipt_id},
            "recorded": {**previous.to_dict(), "routing_receipt_id": previous.routing_receipt_id},
            "matches": actual.routing_receipt_id == previous.routing_receipt_id,
        }
    return {"version": "orfs-router-replay-v1", "cases": rows,
            "eligible": all(row["matches"] for row in rows.values()),
            "evaluation_only": True, "canonical_memory_mutation": "none",
            "production_runtime_imported": False}


def _behavior_digest(routes: dict[str, MemoryRoutingDecision], arms: dict,
                     arm_name: str) -> str:
    payload = []
    for case_id in sorted(routes):
        receipt = arms[case_id].arm_receipts[arm_name]
        payload.append({
            "case_id": case_id,
            "route": routes[case_id].to_dict(),
            "execution": receipt.to_dict(),
        })
    return _digest(payload)


def _state_payload(state) -> dict:
    return {
        "resolution_id": state.resolution_id,
        "input_memory_digest": state.input_memory_digest,
        "resolution_digest": state.resolution_digest,
        "relation_count": len(state.relation_ids),
        "unresolved_conflicts": list(state.unresolved_conflicts),
    }


def run(*, shadow_artifacts: Path | str = DEFAULT_SHADOW,
        challenge_artifacts: Path | str = DEFAULT_CHALLENGE,
        artifacts: Path | str, force: bool = False) -> dict:
    shadow_root = Path(shadow_artifacts).expanduser().resolve()
    challenge_root = Path(challenge_artifacts).expanduser().resolve()
    output = Path(artifacts).expanduser().resolve()
    if output.exists():
        if not force:
            raise OrfsInterferenceAttributionError(
                f"output exists; pass --force to replace it: {output}")
        shutil.rmtree(output)
    output.mkdir(parents=True)
    receipts = shadow_root / "receipts"
    source_db = shadow_root / "tehm.sqlite"
    if not source_db.is_file():
        raise OrfsInterferenceAttributionError(f"shadow SQLite is missing: {source_db}")

    shadow_receipt = AppliedShadowUpdateReceipt.from_dict(
        _read_json(receipts / "shadow_update.json"))
    memory_delta = MemoryDeltaReceipt.from_dict(
        _read_json(receipts / "memory_delta.json"))
    shadow_reason = P13EvolutionReasonReceipt.from_dict(
        _read_json(receipts / "p13_reason_receipt.json"))
    proposal = MemoryInterferenceEvolutionProposal.from_dict(
        _read_json(receipts / "proposal.json"))
    plan = LocalizedUpdatePlan.from_dict(
        _read_json(receipts / "localized_update_plan.json"))
    trigger_payload = _read_json(receipts / "p12_triggers.json").get("triggers") or []
    triggers = tuple(P12ShadowUpdateTriggerReceipt.from_dict(item)
                     for item in trigger_payload)
    admission_payload = _read_json(receipts / "admissions.json").get("admissions") or {}
    admissions = tuple(EvolutionAdmissionReceipt.from_dict(item)
                       for item in admission_payload.values())
    if (shadow_reason.receipt_digest != shadow._load_challenge(challenge_root)[-1].receipt_digest or
            proposal.operation != "SPECIALIZE" or plan.operation != "SPECIALIZE" or
            not triggers or not admissions or
            any(not item.admitted for item in admissions)):
        raise OrfsInterferenceAttributionError(
            "P13 reason/proposal/trigger/admission replay is incomplete")
    witness_payload = _read_json(receipts / "anti_forgetting.json")
    witness = AntiForgettingWitness.from_dict(witness_payload["witness"])
    if not (memory_delta.eligible and witness.eligible):
        raise OrfsInterferenceAttributionError("P13 C1/anti-forgetting witness is ineligible")
    if (shadow_receipt.source_digest_before != shadow_receipt.source_digest_after or
            shadow_receipt.production_runtime_imported is not False or
            shadow_receipt.canonical_memory_mutation != "none"):
        raise OrfsInterferenceAttributionError("P13 receipt crosses authority boundary")

    (cases_payload, pre_cohort, pre_routes, candidates, _derivations, _triggers,
     _admissions, reason) = shadow._load_challenge(challenge_root)
    post_cohort = OrfsPairedCohortReceipt.from_dict(
        _read_json(receipts / "post_revision_cohort.json"))
    training = _read_json(receipts / "training.json")
    parent = MechanismKnowledge.from_dict(training["knowledge"])
    child = _child(parent)
    if f"knowledge:{child.object_id}" not in memory_delta.changed_ids:
        raise OrfsInterferenceAttributionError("P13 delta does not name the typed child")
    if set(pre_cohort.case_receipts) != set(post_cohort.case_receipts):
        raise OrfsInterferenceAttributionError("pre/post ORFS cohorts are not aligned")

    # The source is only read to establish a baseline digest.  All projection
    # writes go to a separate SQLite file.
    # The P13 source is already a frozen snapshot.  Immutable read-only mode
    # prevents this audit from creating WAL/SHM sidecars on its evidence.
    source_conn = db.connect_read_only(source_db)
    source_before = shadow._connection_digest(source_conn)
    if source_before != memory_delta.baseline_memory_digest:
        source_conn.close()
        raise OrfsInterferenceAttributionError("shadow source digest does not bind memory delta")
    p14_db = output / "p14-attribution.sqlite"
    shutil.copy2(source_db, p14_db)
    conn = db.connect(p14_db)
    db.ensure_schema(conn)
    scope = dict(shadow_receipt.metadata.get("scope") or {})
    if not scope:
        scope = {"target_scope": "global"}
    baseline_state = resolve_current_state(conn, scope, mode="shadow", persist=False)
    baseline_router_audit = audit_router_outputs(
        conn, cases_payload["cases"], candidates, parent, pre_routes)
    provenance = {
        "source": "r3-orfs-interference-p14-projection",
        "p13_shadow_receipt": shadow_receipt.receipt_digest,
        "reason_receipt": reason.receipt_digest,
    }
    evidence_refs = [
        {"evidence_type": "transition", "evidence_id": transition_id,
         "split": "training", "lineage_id": lineage,
         "evidence_level": child.evidence_level}
        for transition_id, lineage in zip(
            training["transition_ids"], training["lineages"])
    ]
    revise_knowledge(
        conn, parent_object_id=training["knowledge_object_id"], replacement=child,
        operation="SPECIALIZE", target_scope="global", evidence_refs=evidence_refs,
        provenance=provenance, commit=True)
    after_state = resolve_current_state(conn, scope, mode="shadow", persist=True, commit=True)
    checked_after_state = verify_resolution_snapshot(conn, after_state.resolution_id)
    state_loadable = checked_after_state.resolution_id == after_state.resolution_id
    projection_digest = shadow._connection_digest(conn)
    if baseline_state.resolution_id == after_state.resolution_id:
        raise OrfsInterferenceAttributionError("P14 projection did not change resolved state")

    recorded_post_routes = {
        case_id: MemoryRoutingDecision.from_dict(payload)
        for case_id, payload in _read_json(receipts / "post_routes.json").items()}
    candidate_router_audit = audit_router_outputs(
        conn, cases_payload["cases"], candidates, parent, recorded_post_routes)
    router_audit = {"baseline": baseline_router_audit, "candidate": candidate_router_audit}
    _write_json(output / "router_replay.json", router_audit)
    if not (baseline_router_audit["eligible"] and candidate_router_audit["eligible"]):
        db.checkpoint_and_close(conn)
        source_conn.close()
        raise OrfsInterferenceAttributionError(
            "recorded ORFS routes do not replay through the actual router; "
            "see router_replay.json. No P14 policy-load or success receipt was created")
    post_routes = {
        case_id: MemoryRoutingDecision.from_dict(row["actual"])
        for case_id, row in candidate_router_audit["cases"].items()}
    baseline_behavior = _behavior_digest(pre_routes, pre_cohort.case_receipts, "NO_MEMORY")
    candidate_behavior = _behavior_digest(post_routes, post_cohort.case_receipts,
                                          "APPLICABILITY_GATED")
    runtime_id = "tehm-r3-orfs-p14-evaluation"
    baseline_policy = create_policy_snapshot(
        conn, memory_snapshot_id=memory_delta.baseline_memory_digest,
        promoted_rules=[], promoted_assets=[],
        retrieval_config={"knowledge_object_id": parent.object_id,
                          "evaluation_only": True},
        routing_config={"routing": "pre-interference", "production_authority": False})
    candidate_policy = create_policy_snapshot(
        conn, memory_snapshot_id=memory_delta.candidate_memory_digest,
        promoted_rules=[], promoted_assets=[],
        retrieval_config={"knowledge_object_id": child.object_id,
                          "negative_applicability": list(child.negative_applicability),
                          "evaluation_only": True},
        routing_config={"routing": "negative-applicability-veto",
                        "production_authority": False})
    baseline_load = record_policy_load(
        conn, policy_snapshot_id=baseline_policy.policy_snapshot_id,
        runtime_id=runtime_id, loaded=True,
        receipt={"mode": "evaluation_only", "production_authority": False,
                 "execution_receipt_id": pre_cohort.case_receipts[sorted(pre_routes)[0]]
                 .arm_receipts["NO_MEMORY"].execution_digest,
                 "behavior_digest": baseline_behavior})
    candidate_load = record_policy_load(
        conn, policy_snapshot_id=candidate_policy.policy_snapshot_id,
        runtime_id=runtime_id, loaded=True,
        receipt={"mode": "evaluation_only", "production_authority": False,
                 "execution_receipt_id": post_cohort.case_receipts[sorted(post_routes)[0]]
                 .arm_receipts["APPLICABILITY_GATED"].execution_digest,
                 "behavior_digest": candidate_behavior})
    heldout = _read_json(receipts / "heldout.json")
    heldout_evidence = {
        "verdict": "NOT_APPLICABLE",
        "disjoint_lineage": False,
        "evidence_id": _digest(heldout),
        "baseline_outcome": heldout.get("outcome"),
        "interpretation": "held-out baseline is an audit, not a target capability gain",
    }
    attribution = evaluate_capability_attribution_from_db(
        conn, capability_id="capability:r3-orfs-interference-safety",
        baseline_memory_digest=memory_delta.baseline_memory_digest,
        candidate_memory_digest=memory_delta.candidate_memory_digest,
        baseline_policy_snapshot_id=baseline_policy.policy_snapshot_id,
        candidate_policy_snapshot_id=candidate_policy.policy_snapshot_id,
        runtime_id=runtime_id,
        baseline_behavior_digest=baseline_behavior,
        candidate_behavior_digest=candidate_behavior,
        # No repair target was added: this lane is a safety/strategy veto.
        target_gain=False,
        no_regression=all(
            paired.arm_receipts["APPLICABILITY_GATED"].outcome == "PASS" and
            paired.arm_receipts["CAUSAL_NO_SKILL"].outcome == "PASS" and
            not paired.arm_receipts["APPLICABILITY_GATED"].created_regressions
            for paired in post_cohort.case_receipts.values()),
        heldout=heldout_evidence,
        ablation={
            "gain_without_memory": False,
            "gain_with_memory": False,
            "policy_snapshot_id": baseline_policy.policy_snapshot_id,
            "policy_load_receipt_id": baseline_load.receipt_id,
            "runtime_receipt_id": pre_cohort.case_receipts[sorted(pre_routes)[0]]
            .arm_receipts["NO_MEMORY"].execution_digest,
            "behavior_digest": baseline_behavior,
            "evidence_id": heldout_evidence["evidence_id"],
        },
        memory_delta={"version": "memory-delta-v1",
                      "baseline_memory_digest": memory_delta.baseline_memory_digest,
                      "candidate_memory_digest": memory_delta.candidate_memory_digest,
                      **memory_delta.delta},
        shadow_update_receipt=shadow_receipt,
        strict_memory_delta=True,
    )

    strategy_gates = {
        "C1_memory_delta_bound": memory_delta.eligible,
        "C2_specialized_knowledge_and_relation": (
            f"knowledge:{child.object_id}" in memory_delta.changed_ids and
            bool(shadow_receipt.created_relation_ids)),
        "C3_shadow_state_loadable": state_loadable,
        "C4_route_changed_to_veto": all(
            pre_routes[case_id].decision == "CONSIDER" and
            post_routes[case_id].decision == "INAPPLICABLE" and
            pre_routes[case_id].routing_receipt_id != post_routes[case_id].routing_receipt_id
            for case_id in post_routes),
        "C5_fallback_changed_and_executed": all(
            post_cohort.case_receipts[case_id].arm_receipts["APPLICABILITY_GATED"].source ==
            "no_memory" and
            post_cohort.case_receipts[case_id].arm_receipts["APPLICABILITY_GATED"].outcome ==
            "PASS" and
            pre_cohort.case_receipts[case_id].arm_receipts["ALWAYS_MEMORY"].outcome in
            {"FAIL", "REGRESSION"}
            for case_id in post_routes),
    }
    ablation_rows = []
    for case_id in sorted(post_routes):
        pre = pre_cohort.case_receipts[case_id].arm_receipts
        post = post_cohort.case_receipts[case_id].arm_receipts
        ablation_rows.append({
            "case_id": case_id,
            "M_t_forced_memory": {**pre["ALWAYS_MEMORY"].to_dict(),
                                   "execution_digest": pre["ALWAYS_MEMORY"].execution_digest},
            "M_t+1_negative_applicability": {
                **post["APPLICABILITY_GATED"].to_dict(),
                "execution_digest": post["APPLICABILITY_GATED"].execution_digest},
            "M_t+1_minus_delta_M_no_memory": {
                **pre["NO_MEMORY"].to_dict(),
                "execution_digest": pre["NO_MEMORY"].execution_digest},
        })
    ablation_payload = {
        "version": "tehm-r3-orfs-interference-safety-ablation-v0.1",
        "comparison": "harmful forced memory vs negative-applicability fallback vs no-memory",
        "cases": ablation_rows,
        "capability_gain_claimed": False,
        "strategy_safety_gain": strategy_gates["C5_fallback_changed_and_executed"],
        "evaluation_only": True,
        "canonical_memory_mutation": "none",
        "memory_docs_submitted": False,
        "production_authority_changed": False,
        "production_runtime_imported": False,
    }
    # Include policy/load and attribution rows in the final projection digest;
    # computing it before those immutable evidence writes would make replay
    # compare the report with an intermediate database state.
    projection_digest = shadow._connection_digest(conn)
    report = {
        "version": VERSION,
        "campaign_id": post_cohort.campaign_id,
        "scope": "L2_STRATEGY_EVOLUTION",
        "reason": "MEMORY_INTERFERENCE",
        "evaluation_only": True,
        "canonical_memory_mutation": "none",
        "memory_docs_submitted": False,
        "production_authority_changed": False,
        "production_runtime_imported": False,
        "strategy_gates": strategy_gates,
        "strategy_attribution_eligible": all(strategy_gates.values()),
        "capability_attribution": attribution.to_dict(),
        "capability_claim_promotable": attribution.promotable,
        "capability_claim_boundary": (
            "No L3 capability gain is claimed: the child adds a negative-applicability "
            "veto and deliberately selects no executable asset."),
        "p14_chain": {
            "router_replay_digest": _digest(router_audit),
            "reason_receipt_digest": shadow_reason.receipt_digest,
            "admission_receipt_digests": sorted(item.receipt_digest for item in admissions),
            "p12_trigger_receipt_digests": sorted(item.receipt_digest for item in triggers),
            "proposal_digest": proposal.proposal_digest,
            "localized_update_plan_digest": plan.plan_digest,
            "shadow_update_receipt_digest": shadow_receipt.receipt_digest,
            "memory_delta_receipt_digest": memory_delta.receipt_digest,
            "baseline_resolution_id": baseline_state.resolution_id,
            "candidate_resolution_id": after_state.resolution_id,
            "projection_memory_digest": projection_digest,
            "projection_replay_digest_matches_p13": (
                projection_digest == memory_delta.candidate_memory_digest),
            "baseline_policy_snapshot": baseline_policy.to_dict(),
            "candidate_policy_snapshot": candidate_policy.to_dict(),
            "baseline_policy_load": {**baseline_load.to_dict(),
                                     "receipt_id": baseline_load.receipt_id},
            "candidate_policy_load": {**candidate_load.to_dict(),
                                      "receipt_id": candidate_load.receipt_id},
            "heldout_audit": heldout_evidence,
            "candidate_lineage": {
                "eligible": False,
                "reason": "INAPPLICABLE veto deliberately selects no executable asset",
            },
            "safety_ablation": {"path": str(output / "p14_safety_ablation.json"),
                                "digest": _digest(ablation_payload)},
        },
        "source_integrity": {
            "source_db": str(source_db),
            "source_digest_before": source_before,
            "source_digest_after": shadow._connection_digest(source_conn),
            "unchanged": source_before == shadow._connection_digest(source_conn),
            "projection_db": str(p14_db),
            "projection_memory_digest": projection_digest,
            "p13_candidate_memory_digest": memory_delta.candidate_memory_digest,
        },
        "interpretation": (
            "P14 proves a real ORFS strategy change: SPECIALIZE makes the harmful "
            "memory route INAPPLICABLE and the gated arms execute no-memory PASS. "
            "The standard capability C5/C6/C8 gates remain unclaimed, so this receipt "
            "cannot authorize promotion or production runtime import."),
    }
    summary = {
        "version": VERSION,
        "strategy_attribution_eligible": report["strategy_attribution_eligible"],
        "capability_claim_promotable": attribution.promotable,
        "standard_gates": attribution.gates,
        "standard_missing_gates": list(attribution.missing_gates),
        "source_unchanged": report["source_integrity"]["unchanged"],
        "evaluation_only": True,
        "canonical_memory_mutation": "none",
        "memory_docs_submitted": False,
        "production_authority_changed": False,
        "production_runtime_imported": False,
    }
    # The projection is disposable, but emitted P14 evidence must still be a
    # sidecar-free snapshot for deterministic replay.  Do not publish any of
    # the completion reports until the projection checkpoint succeeds.
    db.checkpoint_and_close(conn)
    source_conn.close()
    _write_json(output / "p14_safety_ablation.json", ablation_payload)
    _write_json(output / "p14_strategy_attribution.json", report)
    _write_json(output / "p14_capability_attribution.json", attribution.to_dict())
    _write_json(output / "summary.json", summary)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--shadow-artifacts", type=Path, default=DEFAULT_SHADOW)
    parser.add_argument("--challenge-artifacts", type=Path, default=DEFAULT_CHALLENGE)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = run(shadow_artifacts=args.shadow_artifacts,
                     challenge_artifacts=args.challenge_artifacts,
                     artifacts=args.artifacts, force=args.force)
    except (OSError, sqlite3.Error, TypeError, ValueError,
            OrfsInterferenceAttributionError) as exc:
        print(f"ORFS P14 attribution failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
