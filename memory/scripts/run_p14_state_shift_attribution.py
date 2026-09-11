#!/usr/bin/env python3
"""Fill P14 C1-C5 from one real, discarded StateShift P13 receipt.

The runner materializes the exact P13 child in a disposable SQLite copy,
checks its logical digest against ``AppliedShadowUpdateReceipt``, then loads
that child into an evaluation-only validated view.  It re-routes the frozen
P12 target contexts, selects and binds the existing immutable Flow Asset,
executes the resulting candidates through the real ORFS oracle, and builds
typed candidate lineages.  C6-C8, promotion, canonical mutation, and
production runtime import remain explicitly out of scope.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts import MemoryQuery, MemoryRoutingDecision  # noqa: E402
from tehm import db  # noqa: E402
from tehm.capability import (  # noqa: E402
    build_candidate_lineage, create_policy_snapshot,
    evaluate_capability_attribution_from_db, memory_delta_from_shadow_update,
    record_policy_load,
)
from tehm.evaluation import OrfsCandidateOracle, execute_candidate  # noqa: E402
from tehm.evolution import (  # noqa: E402
    AppliedShadowUpdateReceipt, StateShiftSupportExpansionReceipt,
)
from tehm.ids import stable_dumps  # noqa: E402
from tehm.knowledge import (  # noqa: E402
    MechanismKnowledge, record_knowledge_authority, revise_knowledge,
    set_knowledge_status,
)
from tehm.retrieval.asset_selector import (  # noqa: E402
    select_knowledge_grounded_assets,
)
from tehm.retrieval.memory_router import route_memory  # noqa: E402
from tehm.retrieval.structured_candidate import (  # noqa: E402
    StructuredRepairCandidate, build_structured_candidate,
)
from tehm.state import (  # noqa: E402
    SupportEnvelope, resolve_current_state, verify_resolution_snapshot,
)
from tehm.assets.receipts import RuntimeBindingReceipt  # noqa: E402
from tehm.verified_execution import scoped_learning_replay  # noqa: E402


REPORT_VERSION = "p14-state-shift-attribution-report-v1"
P13_MATERIALIZED_AT = "2000-01-01T00:00:00+00:00"
P14_EVALUATED_AT = "2000-01-01T00:00:01+00:00"


class P14StateShiftAttributionError(ValueError):
    """The real P13-to-P14 evidence chain is incomplete or inconsistent."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _read(path: Path, name: str) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise P14StateShiftAttributionError(
            f"cannot read {name}: {path}") from exc
    if not isinstance(payload, dict):
        raise P14StateShiftAttributionError(f"{name} must be an object")
    return payload


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _path(value: object, name: str) -> Path:
    if type(value) is not str or not value.strip():
        raise P14StateShiftAttributionError(f"{name} path is missing")
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise P14StateShiftAttributionError(f"{name} is not a file: {path}")
    return path


def _logical_digest(conn: sqlite3.Connection) -> str:
    return _digest("\n".join(conn.iterdump()))


def _self_digest(payload: dict, field: str, name: str) -> str:
    supplied = payload.get(field)
    replay = dict(payload)
    replay.pop(field, None)
    if supplied != _digest(replay):
        raise P14StateShiftAttributionError(f"{name} {field} mismatch")
    return supplied


def _closed(payload: dict, name: str) -> None:
    if (payload.get("evaluation_only") is not True or
            payload.get("canonical_memory_mutation") != "none" or
            payload.get("production_runtime_imported") is not False or
            payload.get("production_integration") not in {
                None, "not_attempted"} or
            payload.get("memory_docs_submitted") not in {None, False}):
        raise P14StateShiftAttributionError(
            f"{name} crosses the evaluation authority boundary")


def _current_contexts(preregistration: dict) -> dict[str, dict]:
    rows = preregistration.get("cases")
    if not isinstance(rows, list) or not rows:
        raise P14StateShiftAttributionError(
            "preregistration cases must be a non-empty list")
    result = {}
    for row in rows:
        registration = row.get("registration") if isinstance(row, dict) else None
        case_id = registration.get("challenge_id") if isinstance(
            registration, dict) else None
        context = registration.get("current_context") if isinstance(
            registration, dict) else None
        if (type(case_id) is not str or not case_id or
                not isinstance(context, dict) or
                row.get("admitted_to_state_shift_bucket") is not True):
            raise P14StateShiftAttributionError(
                "preregistration case is not an admitted typed context")
        if case_id in result:
            raise P14StateShiftAttributionError(
                "preregistration case IDs contain duplicates")
        result[case_id] = context
    return result


def _query(context: dict, child: MechanismKnowledge,
           envelope: SupportEnvelope) -> MemoryQuery:
    constraint = context.get("constraint_regime")
    structural = context.get("structural_signature")
    oracle = context.get("oracle_regime")
    if not all(isinstance(value, dict)
               for value in (constraint, structural, oracle)):
        raise P14StateShiftAttributionError(
            "StateShift context lacks constraint/structural/oracle facts")
    return MemoryQuery(query_plan={
        **context,
        "target_scope": oracle.get("scope"),
        "measurement_contract_digest": oracle.get("contract_digest"),
        "flow_design_id": structural.get("design_name"),
        "flow_config": {"CORE_UTILIZATION": constraint.get("core_utilization")},
        "current_state_facts": context,
        "support_envelopes": {child.object_id: envelope.to_dict()},
    })


def _runtime_binding(payload: dict) -> RuntimeBindingReceipt:
    required = {
        "asset_id", "knowledge_id", "target_design", "candidate_entities",
        "selected_binding", "structural_evidence", "failure_evidence",
        "ambiguity_count", "eligible", "reason", "binding_digest",
    }
    if not required <= set(payload):
        raise P14StateShiftAttributionError(
            "runtime binding receipt is incomplete")
    return RuntimeBindingReceipt(
        asset_id=payload["asset_id"], knowledge_id=payload["knowledge_id"],
        target_design=payload["target_design"],
        candidate_entities=tuple(payload["candidate_entities"]),
        selected_binding=dict(payload["selected_binding"]),
        structural_evidence=tuple(payload["structural_evidence"]),
        failure_evidence=tuple(payload["failure_evidence"]),
        ambiguity_count=payload["ambiguity_count"], eligible=payload["eligible"],
        reason=payload["reason"], binding_digest=payload["binding_digest"])


def _materialize_p13(
        conn: sqlite3.Connection, *, report: dict,
        child: MechanismKnowledge, expansion_receipt,
        shadow_receipt: AppliedShadowUpdateReceipt) -> tuple[object, object]:
    scope = dict(shadow_receipt.metadata.get("scope") or {})
    if not scope:
        raise P14StateShiftAttributionError("P13 shadow scope is missing")
    before = resolve_current_state(conn, scope, mode="shadow", persist=False)
    execution_plan = report.get("source_bound_plan", {}).get(
        "execution_bound_plan")
    if (not isinstance(execution_plan, dict) or
            report.get("source_bound_plan", {}).get(
                "execution_bound_plan_digest") != shadow_receipt.plan_digest):
        raise P14StateShiftAttributionError(
            "P13 execution-bound plan does not bind shadow receipt")
    evidence_refs = [{
        "evidence_type": "orfs_p12_target_execution",
        "evidence_id": expansion_receipt.target_execution_digests[case_id],
        "split": "training",
        "lineage_id": expansion_receipt.case_lineages[case_id],
        "evidence_level": child.evidence_level,
    } for case_id in sorted(expansion_receipt.case_lineages)]
    provenance = {
        "authority": "isolated_state_shift_shadow_revision",
        "campaign_id": expansion_receipt.campaign_id,
        "source_bound_plan_digest": report["source_bound_plan"][
            "base_plan_digest"],
        "execution_bound_plan_digest": shadow_receipt.plan_digest,
        "support_expansion_receipt_digest": expansion_receipt.receipt_digest,
        "anti_forgetting_witness_digest": report[
            "anti_forgetting_report"]["witness_digest"],
        "evaluation_only": True,
    }
    revision = revise_knowledge(
        conn, parent_object_id=expansion_receipt.parent_knowledge_object_id,
        replacement=child, operation="REVISE",
        target_scope=str(scope.get("target_scope")),
        evidence_refs=evidence_refs, provenance=provenance,
        created_at=P13_MATERIALIZED_AT, commit=True)
    after = resolve_current_state(conn, scope, mode="shadow", persist=False)
    if (before.resolution_id != shadow_receipt.before_resolution_id or
            after.resolution_id != shadow_receipt.after_resolution_id or
            revision.child_object_id != expansion_receipt.child_knowledge_object_id or
            revision.relation_id not in shadow_receipt.created_relation_ids or
            _logical_digest(conn) != shadow_receipt.staging_digest_after):
        raise P14StateShiftAttributionError(
            "disposable P13 materialization does not replay shadow receipt")
    return before, after


def run_p14_state_shift_attribution(
        shadow_update_report: Path | str, *, artifacts: Path | str) -> dict:
    report_path = Path(shadow_update_report).expanduser().resolve()
    output = Path(artifacts).expanduser().resolve()
    if output.exists():
        raise P14StateShiftAttributionError(
            "P14 artifacts directory must not already exist")
    report = _read(report_path, "P13 shadow update report")
    _self_digest(report, "report_digest", "P13 shadow update report")
    _closed(report, "P13 shadow update report")
    if (report.get("r3_5_complete") is not True or
            report.get("eligible_for_p14_attribution") is not True or
            report.get("promotion_attempted") is not False):
        raise P14StateShiftAttributionError("P13 report is not R3-6 eligible")
    shadow_receipt = AppliedShadowUpdateReceipt.from_dict(
        report.get("applied_shadow_update_receipt"))
    memory_delta = memory_delta_from_shadow_update(shadow_receipt)
    if not memory_delta.eligible:
        raise P14StateShiftAttributionError("P13 receipt has no eligible C1 delta")

    expansion_ref = report.get("support_expansion_report")
    if not isinstance(expansion_ref, dict):
        raise P14StateShiftAttributionError("support expansion reference is missing")
    expansion_path = _path(expansion_ref.get("path"), "support expansion report")
    if _sha256(expansion_path) != expansion_ref.get("sha256"):
        raise P14StateShiftAttributionError("support expansion file digest mismatch")
    expansion = _read(expansion_path, "support expansion report")
    if (_self_digest(expansion, "report_digest", "support expansion report") !=
            expansion_ref.get("report_digest")):
        raise P14StateShiftAttributionError("support expansion content mismatch")
    _closed(expansion, "support expansion report")
    child = MechanismKnowledge.from_dict(expansion.get("child_knowledge"))
    envelope = SupportEnvelope.from_dict(expansion.get("child_support_envelope"))
    expansion_receipt = StateShiftSupportExpansionReceipt.from_dict(
        expansion.get("support_expansion_receipt"))
    if (expansion_receipt.receipt_digest != expansion_ref.get("receipt_digest") or
            child.content_digest != expansion_receipt.child_knowledge_digest or
            envelope.envelope_digest !=
            expansion_receipt.child_support_envelope_digest):
        raise P14StateShiftAttributionError("typed support expansion binding mismatch")

    snapshot_ref = expansion.get("source_snapshot_report")
    if not isinstance(snapshot_ref, dict):
        raise P14StateShiftAttributionError("source snapshot reference is missing")
    snapshot_path = _path(snapshot_ref.get("path"), "source snapshot report")
    if _sha256(snapshot_path) != snapshot_ref.get("sha256"):
        raise P14StateShiftAttributionError("source snapshot file digest mismatch")
    snapshot = _read(snapshot_path, "source snapshot report")
    if (_self_digest(snapshot, "report_digest", "source snapshot report") !=
            snapshot_ref.get("report_digest")):
        raise P14StateShiftAttributionError("source snapshot content mismatch")
    acquisition_ref = snapshot.get("parent_acquisitions")
    replay_ref = snapshot.get("replay")
    if not isinstance(acquisition_ref, dict) or not isinstance(replay_ref, dict):
        raise P14StateShiftAttributionError("scoped replay binding is missing")
    acquisition_path = _path(
        acquisition_ref.get("path"), "scoped replay acquisitions")
    if _sha256(acquisition_path) != acquisition_ref.get("sha256"):
        raise P14StateShiftAttributionError("scoped replay file digest mismatch")
    acquisition = _read(acquisition_path, "scoped replay acquisitions")
    acquisitions = acquisition.get("acquisitions")
    acquisition_digest = acquisition.get("digest")
    replay_campaign = replay_ref.get("campaign_id")
    if (not isinstance(acquisitions, dict) or not acquisitions or
            acquisition_digest != _digest(acquisitions) or
            acquisition_digest != acquisition_ref.get("acquisition_digest") or
            type(replay_campaign) is not str or not replay_campaign):
        raise P14StateShiftAttributionError("scoped replay content mismatch")

    prereg_ref = expansion.get("preregistration_audit")
    if not isinstance(prereg_ref, dict):
        raise P14StateShiftAttributionError("preregistration binding is missing")
    prereg_path = _path(prereg_ref.get("path"), "preregistration audit")
    if _sha256(prereg_path) != prereg_ref.get("sha256"):
        raise P14StateShiftAttributionError("preregistration file digest mismatch")
    prereg = _read(prereg_path, "preregistration audit")
    contexts = _current_contexts(prereg)
    if set(contexts) != set(expansion_receipt.case_lineages):
        raise P14StateShiftAttributionError(
            "preregistered contexts differ from support expansion cases")
    preparation = prereg.get("p12_preparation")
    if not isinstance(preparation, dict):
        raise P14StateShiftAttributionError("P12 preparation binding is missing")
    manifest_path = _path(preparation.get("manifest"), "P12 manifest")
    if _sha256(manifest_path) != preparation.get("manifest_sha256"):
        raise P14StateShiftAttributionError("P12 manifest digest mismatch")
    manifest = _read(manifest_path, "P12 manifest")
    cases = {item.get("case_id"): item for item in manifest.get("cases", [])
             if isinstance(item, dict)}
    if set(cases) != set(contexts):
        raise P14StateShiftAttributionError("P12 cases differ from contexts")
    route_path = _path(preparation.get("routing_decisions"), "P12 routes")
    if _sha256(route_path) != preparation.get("routing_decisions_sha256"):
        raise P14StateShiftAttributionError("P12 route file digest mismatch")
    route_payload = _read(route_path, "P12 routes").get("routes")
    if not isinstance(route_payload, dict) or set(route_payload) != set(cases):
        raise P14StateShiftAttributionError("P12 route coverage mismatch")
    pre_routes = {case_id: MemoryRoutingDecision.from_dict(value)
                  for case_id, value in route_payload.items()}

    source_ref = report.get("source_database")
    if not isinstance(source_ref, dict):
        raise P14StateShiftAttributionError("P13 source database is missing")
    source_path = _path(source_ref.get("path"), "P13 source database")
    source_sha = _sha256(source_path)
    source = db.connect_read_only(source_path)
    try:
        source_logical = _logical_digest(source)
    finally:
        source.close()
    if (source_sha != source_ref.get("sha256_before") or
            source_ref.get("sha256_before") != source_ref.get("sha256_after") or
            source_logical != source_ref.get("logical_digest") or
            source_logical != shadow_receipt.source_digest_before):
        raise P14StateShiftAttributionError("P13 source database binding mismatch")

    output.mkdir(parents=True)
    projection_path = output / "p14-state-shift-projection.sqlite"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    frozen = db.connect_read_only(source_path)
    try:
        frozen.backup(conn)
    finally:
        frozen.close()
    conn.execute("PRAGMA foreign_keys=ON")
    case_results = {}
    original_now_local = db.now_local
    db.now_local = lambda: P14_EVALUATED_AT
    try:
        db.ensure_schema(conn)
        before_state, p13_state = _materialize_p13(
            conn, report=report, child=child,
            expansion_receipt=expansion_receipt,
            shadow_receipt=shadow_receipt)
        checked = verify_resolution_snapshot(
            conn, resolve_current_state(
                conn, dict(shadow_receipt.metadata["scope"]), mode="shadow",
                persist=True, commit=True).resolution_id)
        p13_state_loadable = checked.resolution_id == p13_state.resolution_id

        with scoped_learning_replay(
                conn, campaign_id=replay_campaign, acquisitions=acquisitions,
                expected_digest=acquisition_digest):
            set_knowledge_status(
                conn, knowledge_id=child.knowledge_id, version=child.version,
                target_scope=shadow_receipt.metadata["scope"]["target_scope"],
                status="candidate", provenance={
                    "authority": "p14-isolated-evaluation-view",
                    "p13_shadow_receipt": shadow_receipt.receipt_digest,
                    "production_authority": False,
                })
            candidate_child = MechanismKnowledge.from_dict({
                **child.to_dict(), "status": "candidate"})
            authority = record_knowledge_authority(
                conn, candidate_child,
                target_scope=shadow_receipt.metadata["scope"]["target_scope"])
            if not authority.eligible:
                raise P14StateShiftAttributionError(
                    f"P14 child authority rejected: {authority.reason}")
            set_knowledge_status(
                conn, knowledge_id=child.knowledge_id, version=child.version,
                target_scope=shadow_receipt.metadata["scope"]["target_scope"],
                status="validated", authority_receipt=authority,
                provenance={
                    "authority": "p14-isolated-evaluation-view",
                    "p13_shadow_receipt": shadow_receipt.receipt_digest,
                    "production_authority": False,
                })

            evaluation_state = resolve_current_state(
                conn, dict(shadow_receipt.metadata["scope"]), mode="shadow",
                persist=True, commit=True)
            verify_resolution_snapshot(conn, evaluation_state.resolution_id)

            for case_id in sorted(cases):
                case = dict(cases[case_id])
                query = _query(contexts[case_id], child, envelope)
                route = route_memory(
                    conn, query, no_memory_budget=2, memory_budget=1,
                    persist_state=False, commit=False)
                selection = select_knowledge_grounded_assets(
                    conn, query, routing=route, candidate_budget=1)
                binding_payload = selection.metadata.get("runtime_binding")
                if not isinstance(binding_payload, dict):
                    raise P14StateShiftAttributionError(
                        f"P14 {case_id} did not produce a runtime binding")
                binding = _runtime_binding(binding_payload)
                candidate = build_structured_candidate(
                    query, route, selection, binding)
                candidate_ref = manifest.get("candidate_freeze", {}).get(case_id)
                if not isinstance(candidate_ref, dict):
                    raise P14StateShiftAttributionError(
                        f"P12 candidate freeze is missing for {case_id}")
                old_path = _path(candidate_ref.get("path"), f"P12 candidate {case_id}")
                if _sha256(old_path) != candidate_ref.get("sha256"):
                    raise P14StateShiftAttributionError(
                        f"P12 candidate file digest mismatch for {case_id}")
                old_candidate = StructuredRepairCandidate.from_dict(
                    _read(old_path, f"P12 candidate {case_id}"))
                if (old_candidate.candidate_id != candidate_ref.get("candidate_id") or
                        old_candidate.candidate_digest !=
                        candidate_ref.get("candidate_digest")):
                    raise P14StateShiftAttributionError(
                        f"P12 candidate identity mismatch for {case_id}")
                # ``execution_artifacts_root`` belongs to the four-arm P12
                # policy executor.  P14 invokes one typed candidate directly
                # and therefore binds the singular destination instead.
                case.pop("execution_artifacts_root", None)
                case["execution_artifacts_dir"] = str(
                    output / "executions" / case_id.replace(":", "__"))
                execution = execute_candidate(
                    candidate, case, oracle=OrfsCandidateOracle(), budget=3)
                lineage = build_candidate_lineage(
                    candidate=candidate, routing=route, asset_selection=selection,
                    runtime_binding=binding, execution=execution)
                case_results[case_id] = {
                    "pre_route": pre_routes[case_id], "route": route,
                    "old_candidate": old_candidate, "candidate": candidate,
                    "selection": selection, "binding": binding,
                    "execution": execution, "lineage": lineage,
                }

        baseline_behavior = _digest([{
            "case_id": case_id,
            "route": result["pre_route"].to_dict(),
            "candidate": result["old_candidate"].to_dict(),
        } for case_id, result in sorted(case_results.items())])
        candidate_behavior = _digest([{
            "case_id": case_id,
            "route": result["route"].to_dict(),
            "candidate": result["candidate"].to_dict(),
            "execution": result["execution"].to_dict(),
        } for case_id, result in sorted(case_results.items())])
        baseline_policy = create_policy_snapshot(
            conn, memory_snapshot_id=memory_delta.baseline_memory_digest,
            promoted_rules=[], promoted_assets=[], retrieval_config={
                "knowledge_object_id": expansion_receipt.parent_knowledge_object_id,
                "evaluation_only": True}, routing_config={
                    "decision": "NO_SKILL", "reason": "STATE_SHIFT",
                    "production_authority": False})
        candidate_policy = create_policy_snapshot(
            conn, memory_snapshot_id=memory_delta.candidate_memory_digest,
            promoted_rules=[], promoted_assets=[], retrieval_config={
                "knowledge_object_id": child.object_id,
                "support_envelope_digest": envelope.envelope_digest,
                "evaluation_only": True}, routing_config={
                    "decision": "CONSIDER", "production_authority": False})
        runtime_id = "tehm-r3-p14-state-shift-evaluation"
        first = case_results[sorted(case_results)[0]]
        baseline_load = record_policy_load(
            conn, policy_snapshot_id=baseline_policy.policy_snapshot_id,
            runtime_id=runtime_id, loaded=True, receipt={
                "mode": "evaluation_only", "production_authority": False,
                "execution_receipt_id": "p12:" + first[
                    "old_candidate"].candidate_digest,
                "behavior_digest": baseline_behavior})
        candidate_load = record_policy_load(
            conn, policy_snapshot_id=candidate_policy.policy_snapshot_id,
            runtime_id=runtime_id, loaded=True, receipt={
                "mode": "evaluation_only", "production_authority": False,
                "execution_receipt_id": first["execution"].execution_digest,
                "behavior_digest": candidate_behavior})
        first_lineage = first["lineage"]
        attribution = evaluate_capability_attribution_from_db(
            conn, capability_id="capability:r3-state-shift-routing",
            baseline_memory_digest=memory_delta.baseline_memory_digest,
            candidate_memory_digest=memory_delta.candidate_memory_digest,
            baseline_policy_snapshot_id=baseline_policy.policy_snapshot_id,
            candidate_policy_snapshot_id=candidate_policy.policy_snapshot_id,
            runtime_id=runtime_id,
            baseline_behavior_digest=baseline_behavior,
            candidate_behavior_digest=candidate_behavior,
            target_gain=False, no_regression=False,
            heldout={"verdict": "UNKNOWN", "disjoint_lineage": False,
                     "evidence_id": None},
            ablation={
                "gain_without_memory": False, "gain_with_memory": False,
                "policy_snapshot_id": baseline_policy.policy_snapshot_id,
                "policy_load_receipt_id": baseline_load.receipt_id,
                "runtime_receipt_id": "not_run_until_r3_7",
                "behavior_digest": baseline_behavior,
            },
            shadow_update_receipt=shadow_receipt,
            # Multiple target contexts can legitimately yield the same typed
            # route.  The expanded attribution validator expects receipt
            # identity coverage, not one duplicate copy per case.
            routing_receipts=list({
                result["route"].routing_receipt_id: result["route"]
                for result in case_results.values()
            }.values()),
            state_resolution_receipt={
                "resolution_id": evaluation_state.resolution_id,
                "input_memory_digest": evaluation_state.input_memory_digest,
                "resolution_digest": evaluation_state.resolution_digest,
                "relation_count": len(evaluation_state.relation_ids),
                "unresolved_conflicts": list(
                    evaluation_state.unresolved_conflicts),
            },
            candidate_lineage={**first_lineage.to_dict(),
                               "receipt_digest": first_lineage.receipt_digest},
            strict_memory_delta=True, strict_expanded=False)
        if attribution.promotable:
            raise P14StateShiftAttributionError(
                "R3-6 attribution cannot become promotable before C6-C8")

        gates = {
            "C1_memory_changed": memory_delta.eligible,
            "C2_state_knowledge_relation_changed": (
                f"knowledge:{child.object_id}" in memory_delta.changed_ids and
                set(shadow_receipt.created_relation_ids) ==
                set(memory_delta.added_relation_ids) and
                before_state.resolution_id != p13_state.resolution_id),
            "C3_new_state_loadable": p13_state_loadable,
            "C4_route_changed": all(
                result["pre_route"].decision == "NO_SKILL" and
                result["pre_route"].no_skill_reason == "STATE_SHIFT" and
                result["route"].decision in {"CONSIDER", "APPLY"} and
                result["route"].routing_receipt_id !=
                result["pre_route"].routing_receipt_id
                for result in case_results.values()),
            "C5_candidate_changed_and_executed": all(
                result["candidate"].candidate_digest !=
                result["old_candidate"].candidate_digest and
                result["execution"].candidate_digest ==
                result["candidate"].candidate_digest and
                result["execution"].source == "structured_memory" and
                result["execution"].outcome in {"PASS", "PARTIAL"} and
                result["lineage"].eligible
                for result in case_results.values()),
        }
        if not all(gates.values()):
            _write(output / "r3_6_failed_gate_debug.json", {
                "gates": gates,
                "cases": {case_id: {
                    "pre_candidate_digest": result[
                        "old_candidate"].candidate_digest,
                    "candidate_digest": result["candidate"].candidate_digest,
                    "route": result["route"].to_dict(),
                    "execution": {
                        **result["execution"].to_dict(),
                        "execution_digest": result[
                            "execution"].execution_digest,
                    },
                    "lineage_eligible": result["lineage"].eligible,
                } for case_id, result in sorted(case_results.items())},
                "evaluation_only": True,
                "canonical_memory_mutation": "none",
                "production_runtime_imported": False,
            })
            raise P14StateShiftAttributionError(
                f"P14 C1-C5 gates did not close: {gates}")
        projection_digest = _logical_digest(conn)
        payload_cases = {case_id: {
            "pre_route": {**result["pre_route"].to_dict(),
                          "routing_receipt_id":
                          result["pre_route"].routing_receipt_id},
            "candidate_route": {**result["route"].to_dict(),
                                "routing_receipt_id":
                                result["route"].routing_receipt_id},
            "pre_candidate": result["old_candidate"].to_dict(),
            "candidate": result["candidate"].to_dict(),
            "asset_selection": result["selection"].to_dict(),
            "runtime_binding": result["binding"].to_dict(),
            "execution": {**result["execution"].to_dict(),
                          "execution_digest":
                          result["execution"].execution_digest},
            "candidate_lineage": {**result["lineage"].to_dict(),
                                  "receipt_digest":
                                  result["lineage"].receipt_digest},
        } for case_id, result in sorted(case_results.items())}
        final = {
            "version": REPORT_VERSION,
            "campaign_id": shadow_receipt.campaign_id,
            "p13_shadow_update_report": {
                "path": str(report_path), "sha256": _sha256(report_path),
                "report_digest": report["report_digest"],
                "receipt_digest": shadow_receipt.receipt_digest,
            },
            "memory_delta": {**memory_delta.to_dict(),
                             "receipt_digest": memory_delta.receipt_digest},
            "p13_exact_state": {
                "before_resolution_id": before_state.resolution_id,
                "after_resolution_id": p13_state.resolution_id,
                "candidate_memory_digest":
                shadow_receipt.staging_digest_after,
                "loadable": p13_state_loadable,
            },
            "evaluation_knowledge_authority": {
                **authority.to_dict(),
                "authority_receipt_id": authority.authority_receipt_id,
                "receipt_digest": authority.receipt_digest,
                "scope": "disposable_projection_only",
            },
            "cases": payload_cases,
            "r3_6_gates": gates,
            "r3_6_complete": True,
            "standard_capability_attribution": attribution.to_dict(),
            "standard_capability_claim_promotable": attribution.promotable,
            "standard_gate_boundary": (
                "Legacy standard C5 means target gain; R3-6 only proves the "
                "Revision3 candidate-changed-and-executed gate. C6-C8 are "
                "reserved for the frozen R3-7 held-out/Delta-M campaign."),
            "policy_snapshots": {
                "baseline": baseline_policy.to_dict(),
                "candidate": candidate_policy.to_dict(),
                "baseline_load": {**baseline_load.to_dict(),
                                  "receipt_id": baseline_load.receipt_id},
                "candidate_load": {**candidate_load.to_dict(),
                                   "receipt_id": candidate_load.receipt_id},
            },
            "source_integrity": {
                "path": str(source_path), "sha256_before": source_sha,
                "sha256_after": _sha256(source_path),
                "logical_digest": source_logical,
                "unchanged": source_sha == _sha256(source_path),
                "projection_path": str(projection_path),
                "projection_digest": projection_digest,
            },
            "remaining_gates": [
                "C6_heldout_transfer", "C7_heldout_non_regression",
                "C8_delta_memory_ablation"],
            "promotion_attempted": False,
            "evaluation_only": True,
            "canonical_memory_mutation": "none",
            "production_runtime_imported": False,
            "production_integration": "not_attempted",
            "memory_docs_submitted": False,
        }
        final["report_digest"] = _digest(final)
        projection = db.connect(projection_path)
        try:
            conn.backup(projection)
        finally:
            db.checkpoint_and_close(projection)
    finally:
        db.now_local = original_now_local
        conn.close()
    if _sha256(source_path) != source_sha:
        raise P14StateShiftAttributionError("P14 changed the P13 source database")
    _write(output / "p14-state-shift-attribution-report.json", final)
    return final


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--shadow-update-report", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = run_p14_state_shift_attribution(
            args.shadow_update_report, artifacts=args.artifacts)
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({
        "artifacts": str(args.artifacts.expanduser().resolve()),
        "r3_6_complete": report["r3_6_complete"],
        "r3_6_gates": report["r3_6_gates"],
        "standard_capability_claim_promotable": report[
            "standard_capability_claim_promotable"],
        "remaining_gates": report["remaining_gates"],
        "report_digest": report["report_digest"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
