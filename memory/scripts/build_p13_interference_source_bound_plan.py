#!/usr/bin/env python3
"""Compile an admitted interference challenge into a source-bound shadow plan.

Replay the detector, prospective partition, router, selector, binder and
candidate builder. Infer a proposed exclusion only from frozen query facts,
not a caller-authored interference label. No replacement Knowledge or
anti-forgetting witness is manufactured here, and no mutation is executed.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts import MemoryQuery
from scripts.build_p13_interference_reason_bundle import (
    _digest, _load_json, _reference, _sha256, build_interference_reason_bundle,
)
from scripts.build_p13_shadow_trigger_report import (
    P13ShadowTriggerReportError, build_p13_shadow_trigger_report,
)
from scripts.build_r3_orfs_interference_source_bound_inputs import (
    _runtime_binding, _source_snapshot,
)
from tehm.causal.mechanism import load_transition_facts
from tehm.evaluation import OrfsPairedCohortReceipt
from tehm.evaluation.orfs_candidate_oracle import _source_binding, _source_inputs
from tehm.evolution.interference_revision import (
    interference_proposal_to_localized_plan, propose_memory_interference_specialization,
)
from tehm.evolution.reason_derivation import EvolutionReasonDerivationReceipt
from tehm.ids import stable_dumps
from tehm.knowledge.registry import get_knowledge_by_object_id
from tehm.retrieval.asset_selector import select_knowledge_grounded_assets
from tehm.retrieval.memory_router import route_memory, scope_for_query
from tehm.retrieval.structured_candidate import build_structured_candidate
from tehm.state import resolve_current_state
from tehm.verified_execution import require_verified_transition, scoped_learning_replay


class InterferenceSourceBoundPlanError(ValueError):
    """A diagnostic signal or drifting source cannot authorize a shadow plan."""


def _negative_contexts(queries: Mapping[str, MemoryQuery], *,
                       utility_contract_id: str, utility_contract_digest: str) -> tuple[dict, ...]:
    """A proposal scope, not a validated generalization or held-out claim."""
    if (not utility_contract_id or not isinstance(utility_contract_digest, str) or
            not utility_contract_digest.startswith("sha256:")):
        raise InterferenceSourceBoundPlanError("exclusion requires immutable utility contract binding")
    contexts = {}
    for case_id, query in sorted(queries.items()):
        facts = query.query_plan
        config = facts.get("flow_config")
        if (facts.get("mechanism_family") != "DENSITY_RELIEF" or
                facts.get("target_scope") != "flow_feasibility" or
                not isinstance(facts.get("measurement_contract_digest"), str) or
                not isinstance(config, Mapping) or
                set(config) != {"CORE_UTILIZATION"}):
            raise InterferenceSourceBoundPlanError(f"{case_id} lacks measured exclusion facts")
        context = {key: facts[key] for key in (
            "mechanism_family", "target_scope", "measurement_contract_digest", "flow_config")}
        context.update(utility_contract_id=utility_contract_id,
                       utility_contract_digest=utility_contract_digest)
        contexts[stable_dumps(context)] = context
    if not contexts:
        raise InterferenceSourceBoundPlanError("no exclusion contexts")
    return tuple(contexts[key] for key in sorted(contexts))


def _is_intervention(action: dict, candidate_action: dict) -> bool:
    payload = action.get("payload", {})
    expected = candidate_action.get("payload", {})
    return (action.get("domain") == candidate_action.get("domain") == "flow.CONFIG_DELTA" and
            action.get("transformation_family") == candidate_action.get("transformation_family") and
            not payload.get("control") and not payload.get("observation_only") and
            bool(payload.get("config_edits")) and
            all(payload.get(key) == expected.get(key) for key in (
                "config_edits", "measurement_contract_digest", "recheck")))


def _utility_binding(cohort: OrfsPairedCohortReceipt, manifest: dict, authority: dict) -> None:
    contract_id = manifest.get("utility_contract_id")
    digest = manifest.get("utility_contract_digest")
    if (not isinstance(digest, str) or not digest.startswith("sha256:") or
            authority.get("utility_contract_id") != contract_id or
            authority.get("utility_contract_digest") != digest):
        raise InterferenceSourceBoundPlanError("manifest/authority utility contract drift")
    for case_id, paired in cohort.case_receipts.items():
        utility = paired.arm_receipts["ALWAYS_MEMORY"].metadata.get("paired_utility", {})
        if (utility.get("contract_id") != contract_id or
                utility.get("contract_digest") != digest.removeprefix("sha256:")):
            raise InterferenceSourceBoundPlanError(f"{case_id} physical utility context mismatch")


def _source_replay(authority: dict, manifest: dict) -> dict:
    for case in manifest["cases"]:
        current = _source_binding(Path(case["project_dir"]), _source_inputs(case["source_inputs"]))
        if current != case["source_digest"]:
            raise InterferenceSourceBoundPlanError(f"{case['case_id']} challenge source drift")
    _, preregistration = _reference(authority.get("preregistration"), "preregistration")
    (snapshot_path, snapshot, source_db, acquisition_path, acquisitions,
     acquisition_digest, training_campaign) = _source_snapshot(preregistration)
    source_sha = _sha256(source_db)
    if authority.get("source_database", {}).get("sha256") != source_sha:
        raise InterferenceSourceBoundPlanError("source database authority drift")
    frozen = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True)
    ram = sqlite3.connect(":memory:")
    ram.row_factory = sqlite3.Row
    try:
        frozen.backup(ram)
    finally:
        frozen.close()
    queries, routes, candidates, scopes = {}, {}, {}, {}
    try:
        with scoped_learning_replay(ram, campaign_id=training_campaign,
                                    acquisitions=acquisitions, expected_digest=acquisition_digest):
            for case_id, audit in sorted(authority["cases"].items()):
                query = MemoryQuery(**audit["query"])
                route = route_memory(ram, query, no_memory_budget=2, memory_budget=1,
                                     persist_state=False, commit=False)
                if {**route.to_dict(), "decision_digest": route.decision_digest} != audit["route"]:
                    raise InterferenceSourceBoundPlanError(f"{case_id} actual router replay drift")
                selection = select_knowledge_grounded_assets(
                    ram, query, routing=route, candidate_budget=1)
                if stable_dumps(selection.to_dict()) != stable_dumps(audit["asset_selection"]):
                    raise InterferenceSourceBoundPlanError(f"{case_id} selector replay drift")
                binding = _runtime_binding(selection.metadata["runtime_binding"])
                candidate = build_structured_candidate(query, route, selection, binding)
                if (binding.to_dict() != audit["runtime_binding"] or
                        candidate.candidate_digest != audit["candidate"]["candidate_digest"]):
                    raise InterferenceSourceBoundPlanError(f"{case_id} binding/candidate replay drift")
                queries[case_id], routes[case_id], candidates[case_id] = query, route, candidate
                scopes[case_id] = scope_for_query(query)
            parents = {candidate.knowledge_object_id for candidate in candidates.values()}
            resolutions = {route.resolved_state_id for route in routes.values()}
            if len(parents) != 1 or len(resolutions) != 1 or len({stable_dumps(s) for s in scopes.values()}) != 1:
                raise InterferenceSourceBoundPlanError("challenge must bind one parent and source state")
            scope = next(iter(scopes.values()))
            parent = get_knowledge_by_object_id(ram, next(iter(parents)),
                                                target_scope=scope["target_scope"])
            state = resolve_current_state(ram, scope, mode="shadow", persist=False)
            if state.resolution_id != next(iter(resolutions)):
                raise InterferenceSourceBoundPlanError("actual source state differs from routing state")
            transitions, controls = set(), set()
            candidate_action = next(iter(candidates.values())).concrete_action
            for path in parent.causal_path_ids:
                row = ram.execute("SELECT source_transitions_json FROM tehm_causal_paths WHERE path_id=?",
                                  (path,)).fetchone()
                for tid in json.loads(row[0]):
                    fact = load_transition_facts(ram, tid)
                    require_verified_transition(ram, tid)
                    membership = ram.execute(
                        "SELECT split,learner_eligible FROM tehm_dataset_membership "
                        "WHERE transition_id=? AND campaign_id=?", (tid, training_campaign)).fetchone()
                    if membership is None or tuple(membership) != ("training", 1):
                        raise InterferenceSourceBoundPlanError("parent lacks verified training membership")
                    if _is_intervention(fact.action, candidate_action):
                        transitions.add(tid)
                    else:
                        controls.add(tid)
            result = {
                "parent": parent, "state": state, "queries": queries,
                "negative_contexts": _negative_contexts(
                    queries, utility_contract_id=manifest["utility_contract_id"],
                    utility_contract_digest=manifest["utility_contract_digest"]),
                "parent_transition_ids": tuple(sorted(transitions)),
                "parent_non_intervention_transition_ids": tuple(sorted(controls)),
                "training_campaign": training_campaign,
                "source_database": authority["source_database"],
                "source_snapshot_report": {"path": str(snapshot_path), "sha256": _sha256(snapshot_path),
                                           "report_digest": snapshot["report_digest"]},
                "parent_acquisitions": {"path": str(acquisition_path), "sha256": _sha256(acquisition_path),
                                        "digest": acquisition_digest},
            }
    finally:
        ram.close()
    if _sha256(source_db) != source_sha:
        raise InterferenceSourceBoundPlanError("source database changed during replay")
    return result


def build_source_bound_plan(reason_bundle: Path | str, *, output: Path | str) -> dict:
    bundle_path, output_path = (Path(p).expanduser().resolve() for p in (reason_bundle, output))
    if output_path.exists() or output_path == bundle_path:
        raise InterferenceSourceBoundPlanError("output must be new and separate")
    bundle = _load_json(bundle_path, "reason bundle")
    unsigned = dict(bundle)
    supplied = unsigned.pop("bundle_digest", None)
    if supplied != _digest(unsigned) or bundle.get("shadow_mutation_eligible") is not True:
        raise InterferenceSourceBoundPlanError("bundle lacks admitted prospective training evidence")
    cohort_path, raw_cohort = _reference({"path": bundle["cohort_receipt"],
                                        "sha256": bundle["cohort_receipt_sha256"]}, "cohort")
    manifest_path, manifest = _reference({"path": bundle["manifest"],
                                          "sha256": bundle["manifest_sha256"]}, "manifest")
    authority_path, authority = _reference(bundle["input_authority"], "input authority")
    forbidden = {bundle_path, cohort_path, manifest_path, authority_path}
    if output_path in forbidden:
        raise InterferenceSourceBoundPlanError("output aliases evidence")
    with tempfile.TemporaryDirectory(prefix="tehm-interference-replay-") as tmp:
        replay = build_interference_reason_bundle(cohort_path, manifest_path,
                                                  output=Path(tmp) / "reason.json")
        if stable_dumps(replay) != stable_dumps(bundle):
            raise InterferenceSourceBoundPlanError("reason/admission bundle does not replay")
        try:
            trigger = build_p13_shadow_trigger_report(
                cohort_path, manifest_path, output=Path(tmp) / "trigger.json",
                routing_path=authority["routing_decisions"]["path"],
                typed_reason_bundle_path=bundle_path, min_lineages=2)
        except P13ShadowTriggerReportError as exc:
            raise InterferenceSourceBoundPlanError(f"P12 trigger rejected: {exc}") from exc
    if trigger["p13_eligible"] is not True:
        raise InterferenceSourceBoundPlanError(f"P12 trigger rejected: {trigger['blocked_reasons']}")
    cohort = OrfsPairedCohortReceipt.from_dict(raw_cohort)
    _utility_binding(cohort, manifest, authority)
    source = _source_replay(authority, manifest)
    ordered = sorted(cohort.case_receipts)
    derivations = {cid: EvolutionReasonDerivationReceipt.from_dict(
        bundle["derivation_receipts"][cid][0]) for cid in ordered}
    tids = source["parent_transition_ids"]
    if len(tids) != len(ordered):
        raise InterferenceSourceBoundPlanError("parent intervention count does not align with proposal seam")
    proposal = propose_memory_interference_specialization(
        [(derivations[cid], cohort.case_receipts[cid]) for cid in ordered],
        knowledge_object_id=source["parent"].object_id, transition_ids=tids,
        negative_applicability=source["negative_contexts"],
        trigger_receipt_ids=tuple(item["receipt_digest"] for item in trigger["triggers"]),
        evidence_refs=(supplied, source["source_database"]["sha256"], *tids))
    plan = replace(interference_proposal_to_localized_plan(proposal),
                   state_resolution_id=source["state"].resolution_id)
    payload = {
        "version": "p13-interference-source-bound-plan-v1", "campaign_id": cohort.campaign_id,
        "reason_bundle": {"path": str(bundle_path), "sha256": _sha256(bundle_path), "bundle_digest": supplied},
        "input_authority": bundle["input_authority"], "source_database": source["source_database"],
        "source_snapshot_report": source["source_snapshot_report"],
        "parent_acquisitions": source["parent_acquisitions"],
        "parent_knowledge": source["parent"].to_dict(),
        "parent_transition_ids": list(tids), "parent_training_campaign": source["training_campaign"],
        "parent_non_intervention_transition_ids": list(source["parent_non_intervention_transition_ids"]),
        "parent_transitions_are_challenge_executions": False,
        "resolved_source_state": source["state"].to_dict(),
        "proposal": {**proposal.to_dict(), "proposal_digest": proposal.proposal_digest},
        "source_bound_localized_update_plan": {**plan.to_dict(), "plan_digest": plan.plan_digest},
        "actual_generation_chain_replayed": True, "source_database_unchanged": True,
        "physical_utility_contract_bound": True,
        "negative_scope_validated_generalization": False,
        "utility_context_adapter": {
            "version": "frozen-manifest-utility-query-context-v1",
            "source": "P12 manifest, not a label or observed outcome",
            "facts": {key: manifest[key] for key in (
                "utility_contract_id", "utility_contract_digest")},
            "apply_identically_to": ["M_t", "M_t_plus_1", "M_t_plus_1_minus_delta"],
            "original_frozen_queries_unchanged": True,
        },
        "anti_forgetting_present": False, "shadow_update_attempted": False,
        "shadow_execution_ready": False, "remaining_gates": ["anti_forgetting", "exact_shadow_child", "heldout_and_ablation"],
        "evaluation_only": True, "canonical_memory_mutation": "none",
        "production_runtime_imported": False, "memory_docs_submitted": False,
    }
    payload["report_digest"] = _digest(payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reason-bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build_source_bound_plan(args.reason_bundle, output=args.output)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"report_digest": report["report_digest"], "shadow_update_attempted": False}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
