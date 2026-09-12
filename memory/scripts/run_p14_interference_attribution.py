#!/usr/bin/env python3
"""Bind formal P13 interference mutation to independently retained executions.

Structural shadow creation and evaluation-only lifecycle activation are two
different witnesses. Neither grants production authority. Candidate absence
is witnessed by the real veto and no-memory execution, never a fabricated
CandidateLineageReceipt. Wider unresolved non-target failures remain open.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts import MemoryQuery
from scripts.audit_p13_interference_shadow_view import _route_candidate
from scripts.build_p13_interference_policy_views import VIEWS, _self_digest
from scripts.build_p13_interference_source_bound_plan import _digest, _load_json, _reference, _sha256
from scripts.build_r3_orfs_interference_source_bound_inputs import _runtime_binding, _write
from scripts.run_p13_interference_source_bound_shadow_update import run_source_bound_interference_shadow_update
from tehm.capability.attribution import evaluate_capability_attribution_from_db
from tehm.capability.delta import memory_delta_from_shadow_update, MemoryDeltaReceipt
from tehm.capability.lineage import build_candidate_lineage
from tehm.capability.policy_snapshot import create_policy_snapshot, record_policy_load
from tehm.evaluation import OrfsPairedCohortReceipt
from tehm.evolution import AppliedShadowUpdateReceipt, LocalizedUpdatePlan
from tehm.evolution.anti_forgetting import raw_evidence_digest
from tehm.evolution.apply_update import _apply_plan, _connection_digest, _scoped_replay, _staging_copy
from tehm.ids import stable_dumps
from tehm.knowledge import MechanismKnowledge, record_knowledge_authority, set_knowledge_status
from tehm.knowledge.registry import get_knowledge_by_object_id
from tehm.retrieval.asset_selector import select_knowledge_grounded_assets
from tehm.state import resolve_current_state, verify_resolution_snapshot
from tehm.state.schema import ensure_state_schema
from tehm.verified_execution import scoped_learning_replay


class InterferenceAttributionError(ValueError):
    """The formal mutation does not produce the frozen evaluation behavior."""


def _pin(path):
    return {"path": str(path), "sha256": _sha256(path)}


def _state_load(conn, scope, expected_resolution):
    # Persist/reload/replay in RAM, then undo only these audit bookkeeping rows.
    conn.execute("SAVEPOINT p14_state_load")
    try:
        state = resolve_current_state(conn, scope, mode="shadow", persist=True, commit=False)
        checked = verify_resolution_snapshot(conn, state.resolution_id)
        if checked.resolution_id != expected_resolution:
            raise InterferenceAttributionError("materialized state does not load/replay the formal resolution")
        return checked.to_dict()
    finally:
        conn.execute("ROLLBACK TO p14_state_load")
        conn.execute("RELEASE p14_state_load")


def _materialize(conn, report, receipt):
    """Execute the SAME core operation and verify the complete staging digest."""
    plan = LocalizedUpdatePlan.from_dict(report["execution_bound_plan"])
    evidence = report["execution_evidence"]
    if (plan.plan_digest != receipt.plan_digest or
            report["execution_bound_plan"].get("plan_digest") != plan.plan_digest or
            plan.operation != "SPECIALIZE" or plan.failure_type != "MEMORY_INTERFERENCE" or
            _connection_digest(conn) != receipt.staging_digest_before):
        raise InterferenceAttributionError("formal mutation plan or pre-staging digest mismatch")
    scope = receipt.metadata["scope"]
    before = resolve_current_state(conn, scope, mode="shadow", persist=False)
    with patch("tehm.db.now_local", return_value=evidence["created_at"]):
        with _scoped_replay(conn, evidence):
            campaigns = _apply_plan(conn, plan, evidence)
    after = resolve_current_state(conn, scope, mode="shadow", persist=False)
    if (before.resolution_id != receipt.before_resolution_id or
            after.resolution_id != receipt.after_resolution_id or
            _connection_digest(conn) != receipt.staging_digest_after or
            raw_evidence_digest(conn) != receipt.raw_evidence_after_digest or
            campaigns != receipt.metadata["training_evidence_campaigns"]):
        raise InterferenceAttributionError("formal P13 staging does not exactly rematerialize")
    load = _state_load(conn, scope, after.resolution_id)
    return before, after, load


def _activate_existing_child(conn, child, scope, preflight):
    """Activation of an already materialized shadow child; no second revision."""
    if conn.execute("PRAGMA database_list").fetchall()[0][2]:
        raise InterferenceAttributionError("evaluation activation requires RAM")
    with patch("tehm.db.now_local", return_value="2000-01-01T00:00:00+00:00"):
        set_knowledge_status(conn, knowledge_id=child.knowledge_id, version=child.version,
            target_scope=scope["target_scope"], status="candidate", commit=False)
        claim = get_knowledge_by_object_id(conn, child.object_id, target_scope=scope["target_scope"])
        ledger = record_knowledge_authority(conn, claim, target_scope=scope["target_scope"])
        if not ledger.eligible or ledger.to_dict() != preflight["evaluation_view_authority"]:
            raise InterferenceAttributionError("evaluation authority differs from the frozen lifecycle")
        set_knowledge_status(conn, knowledge_id=child.knowledge_id, version=child.version,
            target_scope=scope["target_scope"], status="validated", authority_receipt=ledger, commit=False)
    state = resolve_current_state(conn, scope, mode="shadow", persist=False)
    if state.to_dict() != preflight["evaluation_state"]:
        raise InterferenceAttributionError("evaluation state differs from the frozen policy generation")
    load = _state_load(conn, scope, state.resolution_id)
    return state, ledger, load


def _candidate_matches(candidate, ref):
    if candidate is None:
        if ref is not None:
            raise InterferenceAttributionError("real veto differs from frozen selected candidate")
        return None
    if ref is None:
        raise InterferenceAttributionError("real selection differs from frozen candidate absence")
    _, payload = _reference(ref, "executed structured candidate")
    if (payload != candidate.to_dict() or ref.get("candidate_digest") != candidate.candidate_digest or
            ref.get("candidate_id") != candidate.candidate_id):
        raise InterferenceAttributionError("real selection does not replay the exact executed candidate")
    return candidate


def _replay_view(conn, rows, view):
    results = {}
    for purpose, row in rows.items():
        authority, cohort = row["authorities"][view], row["cohorts"][view]
        cases = {}
        for cid, audit in sorted(authority["cases"].items()):
            query = MemoryQuery(**audit["query"])
            route, candidate = _route_candidate(conn, query)
            frozen_route = {**route.to_dict(), "decision_digest": route.decision_digest}
            if frozen_route != audit["route"]:
                raise InterferenceAttributionError("formal evaluation route does not replay frozen policy")
            _candidate_matches(candidate, audit["candidate_freeze"]["CAUSAL_NO_SKILL"])
            execution = cohort.case_receipts[cid].arm_receipts["CAUSAL_NO_SKILL"]
            lineage = None
            if candidate is not None:
                selection = select_knowledge_grounded_assets(conn, query, routing=route, candidate_budget=1)
                binding = _runtime_binding(selection.metadata["runtime_binding"])
                lineage = build_candidate_lineage(candidate=candidate, routing=route,
                    asset_selection=selection, runtime_binding=binding, execution=execution)
            elif (route.decision != "INAPPLICABLE" or execution.source != "no_memory" or
                    execution.candidate_digest != _digest({}) or execution.action_digest != _digest({}) or
                    execution.outcome != "PASS"):
                raise InterferenceAttributionError("candidate absence is not a real successful veto fallback")
            cases[cid] = {"route": frozen_route, "candidate": None if candidate is None else candidate.to_dict(),
                "execution_digest": execution.execution_digest, "execution_source": execution.source,
                "execution_outcome": execution.outcome,
                "candidate_lineage": None if lineage is None else {
                    **lineage.to_dict(), "receipt_digest": lineage.receipt_digest},
                "candidate_absence_witness": None if candidate is not None else {
                    "routing_receipt_id": route.routing_receipt_id,
                    "execution_digest": execution.execution_digest,
                    "decision": route.decision, "source": execution.source,
                    "action_digest": execution.action_digest, "candidate_digest": execution.candidate_digest,
                    "authority": "actual router plus retained no-memory policy execution"}}
        results[purpose] = cases
    return results


def _load_execution_rows(anti):
    rows = {}
    for purpose in ("target_replay", "non_target", "heldout"):
        _, gate = _reference(anti["gate_evidence"][purpose], "cold-replayed execution gate")
        _, freeze = _reference(gate["policy_freeze"], "executed policy freeze")
        authorities, cohorts = {}, {}
        for view in VIEWS:
            _, manifest = _reference(freeze["policy_manifests"][view], "executed policy manifest")
            _, authorities[view] = _reference(manifest["input_authority"], "executed policy authority")
            _, raw = _reference(gate["executed_cohorts"][view], "actual policy cohort")
            cohorts[view] = OrfsPairedCohortReceipt.from_dict(raw)
        rows[purpose] = {"gate": gate, "freeze": freeze, "authorities": authorities, "cohorts": cohorts}
    return rows


def _changed_candidates(before, after):
    return all(before[cid]["candidate"] is not None and after[cid]["candidate"] is None and
               after[cid]["candidate_absence_witness"] is not None and
               before[cid]["candidate_lineage"]["eligible"] is True
               for cid in before)


def _heldout_disjoint_witness(training_cases, heldout_cases):
    training_lineages = {row["lineage_id"] for row in training_cases.values()}
    heldout_lineages = {row["lineage_id"] for row in heldout_cases.values()}
    training_rtl = {sha for row in training_cases.values() for sha in row["rtl_sha256"]}
    heldout_rtl = {sha for row in heldout_cases.values() for sha in row["rtl_sha256"]}
    if (len(heldout_lineages) < 2 or not training_lineages or not training_rtl or not heldout_rtl or
            heldout_lineages & training_lineages or heldout_rtl & training_rtl):
        raise InterferenceAttributionError("heldout lineage or actual RTL content overlaps training")
    return {"training_lineages": sorted(training_lineages), "heldout_lineages": sorted(heldout_lineages),
        "training_rtl_sha256": sorted(training_rtl), "heldout_rtl_sha256": sorted(heldout_rtl),
        "disjoint": True, "authority": "content-bound original input authorities, not renamed case IDs"}


def _policy_receipts(conn, receipt, delta, replay, rows, state):
    """DB-backed attribution inputs after actual runtime state/behavior replay."""
    behaviors = {view: _digest({purpose: {cid: {"route": case["route"], "candidate": case["candidate"]}
        for cid, case in cases.items()} for purpose, cases in replay[view].items()}) for view in VIEWS}
    if behaviors["Mt"] != behaviors["Mt_plus_delta_minus_delta"]:
        raise InterferenceAttributionError("remove delta does not restore complete routing/selection behavior")
    policies = {}
    runtime_id = "tehm-r3-p14-interference-isolated-evaluation"
    loads = {}
    for view in VIEWS:
        memory = delta.candidate_memory_digest if view == "Mt_plus_delta" else delta.baseline_memory_digest
        policy = create_policy_snapshot(conn, memory_snapshot_id=memory, promoted_rules=[], promoted_assets=[],
            retrieval_config={"evaluation_only": True, "evaluation_view_activation_separate": True,
                              "formal_p13_receipt_digest": receipt.receipt_digest},
            routing_config={"behavior_digest": behaviors[view], "production_authority": False})
        # Snapshot rows are audit records, not a claim that promoted rules loaded.
        # The actual RAM state/router/candidate replay ABOVE proves acceptance.
        execution_id = _digest({p: {cid: case["execution_digest"] for cid, case in cases.items()}
                               for p, cases in replay[view].items()})
        load = record_policy_load(conn, policy_snapshot_id=policy.policy_snapshot_id, runtime_id=runtime_id,
            loaded=True, receipt={"mode": "evaluation_only", "actual_state_and_policy_replayed": True,
                "production_authority": False, "execution_receipt_id": execution_id,
                "behavior_digest": behaviors[view]})
        policies[view], loads[view] = policy, (load, execution_id)
    removed_load, removed_execution = loads["Mt_plus_delta_minus_delta"]
    attribution = evaluate_capability_attribution_from_db(conn,
        capability_id="strategy:r3-memory-interference-contract-scoped-veto",
        baseline_memory_digest=delta.baseline_memory_digest, candidate_memory_digest=delta.candidate_memory_digest,
        baseline_policy_snapshot_id=policies["Mt"].policy_snapshot_id,
        candidate_policy_snapshot_id=policies["Mt_plus_delta"].policy_snapshot_id,
        runtime_id=runtime_id, baseline_behavior_digest=behaviors["Mt"],
        candidate_behavior_digest=behaviors["Mt_plus_delta"],
        target_gain=rows["target_replay"]["gate"]["passed"],
        # CORE70 baseline is unresolved. Bounded CORE45 success is NOT global C7.
        no_regression=False,
        heldout={"verdict": "PASS" if rows["heldout"]["gate"]["passed"] else "FAIL",
            "disjoint_lineage": rows["heldout"]["disjoint_witness"]["disjoint"],
            "evidence_id": rows["heldout"]["gate"]["report_digest"]},
        ablation={"gain_without_memory": False, "gain_with_memory": True,
            "policy_snapshot_id": policies["Mt"].policy_snapshot_id,
            "policy_load_receipt_id": removed_load.receipt_id, "runtime_receipt_id": removed_execution,
            "behavior_digest": behaviors["Mt_plus_delta_minus_delta"]},
        shadow_update_receipt=receipt, strict_memory_delta=True, strict_expanded=False)
    return attribution, {"behavior_digests": behaviors,
        "snapshots": {v: p.to_dict() for v, p in policies.items()},
        "loads": {v: load.to_dict() for v, (load, _) in loads.items()},
        "formal_mutation_digest_excludes_separate_evaluation_activation": True,
        "evaluation_state_resolution_id": state.resolution_id}


def run_interference_attribution(p13_shadow_update, *, output_dir):
    report_path, output = (Path(p).resolve() for p in (p13_shadow_update, output_dir))
    if output.exists() or report_path.is_relative_to(output):
        raise InterferenceAttributionError("attribution output must be new and separate")
    report = _load_json(report_path, "formal P13 shadow update")
    _self_digest(report, "report_digest")
    if (report.get("version") != "p13-interference-source-bound-shadow-update-report-v1" or
            report.get("eligible_for_p14_attribution") is not True or
            report.get("evaluation_view_activation_performed_by_this_update") is not False or
            report.get("canonical_memory_mutation") != "none" or
            report.get("production_runtime_imported") is not False or report.get("promotion_attempted") is not False):
        raise InterferenceAttributionError("formal P13 authority boundary is not preserved")
    receipt = AppliedShadowUpdateReceipt.from_dict(report["applied_shadow_update_receipt"])
    delta = memory_delta_from_shadow_update(receipt)
    if MemoryDeltaReceipt.from_dict(report["memory_delta_receipt"]).to_dict() != delta.to_dict() or not delta.eligible:
        raise InterferenceAttributionError("P13 delta does not replay")
    anti_path, anti = _reference(report["anti_forgetting_evidence"], "actual anti-forgetting evidence")
    # Re-execute existing isolated P13 against actual cold gate/rollback oracles.
    with tempfile.TemporaryDirectory(prefix="tehm-p14-interference-formal-replay-") as tmp:
        cold = run_source_bound_interference_shadow_update(anti_path, output=Path(tmp) / "p13.json")
        if stable_dumps(cold) != stable_dumps(report):
            raise InterferenceAttributionError("formal P13 does not cold replay exactly")
    _, plan = _reference(report["source_bound_plan"], "source plan")
    _, preflight = _reference(report["shadow_view_preflight"], "audited evaluation activation")
    _, acquisition = _reference(plan["parent_acquisitions"], "original training acquisition")
    child = MechanismKnowledge.from_dict(report["execution_evidence"]["knowledge"])
    rows = _load_execution_rows(anti)
    _, training_authority = _reference(plan["input_authority"], "original training input authority")
    _, heldout_authority = _reference(rows["heldout"]["authorities"]["Mt"]["baseline_input_authority"],
                                     "original heldout input authority")
    rows["heldout"]["disjoint_witness"] = _heldout_disjoint_witness(
        training_authority["cases"], heldout_authority["cases"])
    source = Path(report["source_database"]["path"])
    if _sha256(source) != report["source_database"]["sha256"]:
        raise InterferenceAttributionError("formal source database drift")
    frozen = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        conn = _staging_copy(frozen)
    finally:
        frozen.close()
    replay = {}
    try:
        ensure_state_schema(conn, commit=False)
        staging_before = _connection_digest(conn)
        conn.execute("SAVEPOINT p14_formal_delta")
        with scoped_learning_replay(conn, campaign_id=plan["parent_training_campaign"],
                acquisitions=acquisition["acquisitions"], expected_digest=acquisition["digest"]):
            replay["Mt"] = _replay_view(conn, rows, "Mt")
        before, shadow_state, shadow_load = _materialize(conn, report, receipt)
        with scoped_learning_replay(conn, campaign_id=plan["parent_training_campaign"],
                acquisitions=acquisition["acquisitions"], expected_digest=acquisition["digest"]):
            state, ledger, evaluation_load = _activate_existing_child(conn, child, receipt.metadata["scope"], preflight)
            replay["Mt_plus_delta"] = _replay_view(conn, rows, "Mt_plus_delta")
        conn.execute("ROLLBACK TO p14_formal_delta")
        conn.execute("RELEASE p14_formal_delta")
        if _connection_digest(conn) != staging_before:
            raise InterferenceAttributionError("remove formal delta plus evaluation activation did not restore database")
        with scoped_learning_replay(conn, campaign_id=plan["parent_training_campaign"],
                acquisitions=acquisition["acquisitions"], expected_digest=acquisition["digest"]):
            replay["Mt_plus_delta_minus_delta"] = _replay_view(conn, rows, "Mt_plus_delta_minus_delta")
        attribution, policy_receipts = _policy_receipts(conn, receipt, delta, replay, rows, state)
        gates = {"C1_memory_changed": delta.eligible,
            "C2_state_knowledge_relation_changed": "knowledge:" + child.object_id in delta.changed_ids
                and bool(delta.added_relation_ids) and before.resolution_id != shadow_state.resolution_id,
            "C3_formal_and_evaluation_state_loadable": bool(shadow_load and evaluation_load),
            "C4_actual_route_changed": all(replay["Mt"][purpose][cid]["route"]["decision"] in {"CONSIDER", "APPLY"}
                and case["route"]["decision"] == "INAPPLICABLE"
                for purpose in ("target_replay", "heldout") for cid, case in replay["Mt_plus_delta"][purpose].items()),
            "C5_candidate_changed_and_execution_bound": all(_changed_candidates(replay["Mt"][purpose],
                replay["Mt_plus_delta"][purpose]) for purpose in ("target_replay", "heldout")),
            "C6_source_disjoint_heldout_gain": rows["heldout"]["gate"]["passed"],
            "C7_bounded_non_target_no_regression": rows["non_target"]["gate"]["passed"],
            "C8_actual_remove_delta_gain_disappears": all(rows[p]["gate"]["passed"]
                for p in ("target_replay", "heldout")) and all(
                    {cid: {k: c[k] for k in ("route", "candidate")} for cid, c in replay["Mt"][p].items()} ==
                    {cid: {k: c[k] for k in ("route", "candidate")} for cid, c in replay["Mt_plus_delta_minus_delta"][p].items()}
                    for p in rows)}
        if not all(gates.values()) or attribution.promotable:
            raise InterferenceAttributionError("bounded attribution failed or wider C7 was incorrectly granted")
        result = {"version": "p14-interference-formal-attribution-report-v1", "campaign_id": receipt.campaign_id,
            "p13_shadow_update": {**_pin(report_path), "report_digest": report["report_digest"],
                "receipt_digest": receipt.receipt_digest}, "anti_forgetting_evidence": report["anti_forgetting_evidence"],
            "memory_delta": {**delta.to_dict(), "receipt_digest": delta.receipt_digest},
            "formal_state_rematerialization": {"staging_digest": receipt.staging_digest_after,
                "before": before.to_dict(), "after": shadow_state.to_dict(), "state_load": shadow_load,
                "exact_complete_database_digest_verified": True},
            "separate_evaluation_activation": {"child_content_digest": child.content_digest,
                "knowledge_authority": ledger.to_dict(), "state": state.to_dict(), "state_load": evaluation_load,
                "status": "validated_in_disposable_ram_only", "included_in_formal_p13_mutation": False,
                "production_authority": False},
            "policy_runtime_replay": replay, "policy_receipts": policy_receipts, "r3_6_and_bounded_r3_7_gates": gates,
            "heldout_disjoint_witness": rows["heldout"]["disjoint_witness"],
            "standard_capability_attribution": attribution.to_dict(), "standard_capability_claim_promotable": False,
            "claim_level": "L2_CONTRACT_SCOPED_STRATEGY_EVOLUTION", "new_action_space_claimed": False,
            "bounded_attribution_complete": True, "global_non_target_regression_gate_closed": False,
            "remaining_uncertainty": ["CORE70 original and self-contained baseline failures remain retained",
                "reason-stratified independent calibration", "statistical MIR and repair Pareto gates",
                "production authority replay and rollback"],
            "independently_executed_workspaces": sum(row["gate"]["independent_executed_workspaces"] for row in rows.values()),
            "source_database": report["source_database"], "source_database_sha256_after": _sha256(source),
            "raw_evidence_preserved": raw_evidence_digest(conn) == receipt.raw_evidence_before_digest,
            "source_opened_read_only": True, "staging_discarded": True,
            "canonical_memory_mutation": "none", "production_runtime_imported": False,
            "promotion_attempted": False, "evaluation_only": True, "learner_audit_support_imported": False,
            "runner_source_binding": _pin(Path(__file__).resolve()), "memory_docs_submitted": False}
    finally:
        conn.close()
    if _sha256(source) != report["source_database"]["sha256"] or not result["raw_evidence_preserved"]:
        raise InterferenceAttributionError("attribution changed source or canonical evidence")
    result["report_digest"] = _digest(result)
    _write(output / "p14-interference-attribution-report.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--p13-shadow-update", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = run_interference_attribution(args.p13_shadow_update, output_dir=args.output_dir)
    except (KeyError, OSError, sqlite3.Error, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"bounded_attribution_complete": report["bounded_attribution_complete"],
                      "report_digest": report["report_digest"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
