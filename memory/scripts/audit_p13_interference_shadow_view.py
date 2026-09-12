#!/usr/bin/env python3
"""Audit an exact proposed child through real lifecycle/router APIs in RAM.

This is a preflight, NOT an AntiForgettingWitness or AppliedShadowUpdateReceipt.
It proves only query scoping, eligible evaluation-view lifecycle, actual route
changes and rollback. Target/non-target/held-out execution remain required.
"""
from __future__ import annotations

import argparse
import copy
import json
import sqlite3
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts import MemoryQuery
from scripts.build_p13_interference_source_bound_plan import (
    _digest, _load_json, _reference, _sha256, build_source_bound_plan,
)
from tehm.evolution.anti_forgetting import raw_evidence_digest
from tehm.evolution.apply_update import _connection_digest
from tehm.evolution.interference_revision import MemoryInterferenceEvolutionProposal
from tehm.ids import stable_dumps
from tehm.knowledge import (
    MechanismKnowledge, record_knowledge_authority, revise_knowledge, set_knowledge_status,
)
from tehm.knowledge.registry import get_knowledge_by_object_id
from tehm.retrieval.asset_selector import select_knowledge_grounded_assets
from tehm.retrieval.memory_router import route_memory
from tehm.retrieval.structured_candidate import build_structured_candidate
from scripts.build_r3_orfs_interference_source_bound_inputs import _runtime_binding
from tehm.state import resolve_current_state
from tehm.verified_execution import scoped_learning_replay


class InterferenceShadowViewError(ValueError):
    """A scope, authority, generation, or rollback gate failed."""


def _child(parent: MechanismKnowledge, proposal: MemoryInterferenceEvolutionProposal):
    negative = {stable_dumps(entry): entry for entry in (
        *parent.negative_applicability, *proposal.negative_applicability)}
    return replace(parent, knowledge_id="mk_interference_" + proposal.proposal_digest.split(":")[1][:20],
                   version=1, status="shadow",
                   negative_applicability=tuple(negative[key] for key in sorted(negative)),
                   known_failure_modes=tuple(sorted({*parent.known_failure_modes,
                                                      "utility_contract_scoped_memory_interference"})))


def _query(frozen: dict, utility_facts: dict | None) -> MemoryQuery:
    payload = copy.deepcopy(frozen)
    if utility_facts is not None:
        if set(utility_facts) != {"utility_contract_id", "utility_contract_digest"}:
            raise InterferenceShadowViewError("query adapter accepts utility pins only")
        payload["query_plan"].update(utility_facts)
    return MemoryQuery(**payload)


def _route_candidate(conn, query):
    route = route_memory(conn, query, no_memory_budget=2, memory_budget=1,
                         persist_state=False, commit=False)
    candidate = None
    if route.decision in {"CONSIDER", "APPLY"}:
        selection = select_knowledge_grounded_assets(conn, query, routing=route, candidate_budget=1)
        candidate = build_structured_candidate(
            query, route, selection, _runtime_binding(selection.metadata["runtime_binding"]))
    return route, candidate


def _activate_evaluation_child(ram, parent, proposal, scope, authority):
    """Use the real lifecycle in caller-owned RAM, never production authority.

    The caller must scope parent execution replay and own a rollback savepoint.
    Paired evidence is zipped with its actual case lineage, not cross-producted.
    """
    if ram.execute("PRAGMA database_list").fetchall()[0][2]:
        raise InterferenceShadowViewError("evaluation child activation requires RAM database")
    child = _child(parent, proposal)
    with patch("tehm.db.now_local", return_value="2000-01-01T00:00:00+00:00"):
        evidence = [{"evidence_type": "orfs_p12_physical_interference",
                     "evidence_id": digest, "split": "training",
                     "lineage_id": authority["cases"][cid]["lineage_id"],
                     "evidence_level": parent.evidence_level}
                    for cid, digest in zip(proposal.case_ids, proposal.paired_receipt_digests,
                                           strict=True)]
        revision = revise_knowledge(
            ram, parent_object_id=parent.object_id, replacement=child, operation="SPECIALIZE",
            target_scope=scope["target_scope"], evidence_refs=evidence,
            created_at="2000-01-01T00:00:00+00:00", commit=False)
        shadow_state = resolve_current_state(ram, scope, mode="shadow", persist=False)
        set_knowledge_status(ram, knowledge_id=child.knowledge_id, version=child.version,
                             target_scope=scope["target_scope"], status="candidate", commit=False)
        candidate_claim = get_knowledge_by_object_id(ram, child.object_id, target_scope=scope["target_scope"])
        ledger = record_knowledge_authority(ram, candidate_claim, target_scope=scope["target_scope"])
        if not ledger.eligible:
            raise InterferenceShadowViewError("proposed child authority gates failed")
        set_knowledge_status(ram, knowledge_id=child.knowledge_id, version=child.version,
                             target_scope=scope["target_scope"], status="validated",
                             authority_receipt=ledger, commit=False)
    evaluation_state = resolve_current_state(ram, scope, mode="shadow", persist=False)
    return child, revision, ledger, shadow_state, evaluation_state


def audit_shadow_view(source_bound_plan: Path | str, *, output: Path | str) -> dict:
    plan_path, output_path = (Path(p).expanduser().resolve() for p in (source_bound_plan, output))
    if output_path.exists() or output_path == plan_path:
        raise InterferenceShadowViewError("output must be new and separate")
    plan = _load_json(plan_path, "source-bound plan")
    unsigned = dict(plan)
    supplied = unsigned.pop("report_digest", None)
    if supplied != _digest(unsigned) or plan.get("physical_utility_contract_bound") is not True:
        raise InterferenceShadowViewError("plan lacks physical contract binding")
    bundle_path, _ = _reference(plan["reason_bundle"], "reason bundle")
    with tempfile.TemporaryDirectory(prefix="tehm-shadow-view-replay-") as tmp:
        replay = build_source_bound_plan(bundle_path, output=Path(tmp) / "plan.json")
    if stable_dumps(replay) != stable_dumps(plan):
        raise InterferenceShadowViewError("source-bound plan does not replay")
    _, authority = _reference(plan["input_authority"], "input authority")
    _, acquisition = _reference(plan["parent_acquisitions"], "parent acquisitions")
    acquisitions, acquisition_digest = acquisition["acquisitions"], acquisition["digest"]
    if acquisition_digest != plan["parent_acquisitions"]["digest"]:
        raise InterferenceShadowViewError("acquisition content drift")
    source = Path(plan["source_database"]["path"])
    if _sha256(source) != plan["source_database"]["sha256"]:
        raise InterferenceShadowViewError("source database drift")
    frozen = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    ram = sqlite3.connect(":memory:")
    ram.row_factory = sqlite3.Row
    try:
        frozen.backup(ram)
    finally:
        frozen.close()
    parent = MechanismKnowledge.from_dict(plan["parent_knowledge"])
    proposal = MemoryInterferenceEvolutionProposal.from_dict(plan["proposal"])
    child = _child(parent, proposal)
    utility_facts = plan["utility_context_adapter"]["facts"]
    scope = plan["resolved_source_state"]["scope"]
    cases, controls, restored = {}, {}, {}
    source_logical = _connection_digest(ram)
    if source_logical != plan["source_database"]["logical_digest"]:
        ram.close()
        raise InterferenceShadowViewError("source logical database digest drift")
    raw_before = raw_evidence_digest(ram)
    try:
        with scoped_learning_replay(ram, campaign_id=plan["parent_training_campaign"],
                                    acquisitions=acquisitions, expected_digest=acquisition_digest):
            for cid, audit in sorted(authority["cases"].items()):
                route, candidate = _route_candidate(ram, _query(audit["query"], utility_facts))
                if candidate is None or candidate.candidate_digest != audit["candidate"]["candidate_digest"]:
                    raise InterferenceShadowViewError(f"{cid} utility adapter changed frozen Mt candidate")
                cases[cid] = {"before_route": route.to_dict(), "before_candidate": candidate.to_dict()}
            ram.execute("SAVEPOINT interference_shadow_view")
            child, revision, ledger, shadow_state, evaluation_state = _activate_evaluation_child(
                ram, parent, proposal, scope, authority)
            for cid, audit in sorted(authority["cases"].items()):
                route, candidate = _route_candidate(ram, _query(audit["query"], utility_facts))
                if route.decision != "INAPPLICABLE" or candidate is not None:
                    raise InterferenceShadowViewError(f"{cid} actual contract-scoped veto did not fire")
                cases[cid].update(after_route=route.to_dict(), after_candidate=None)
                control, _ = _route_candidate(ram, _query(audit["query"], None))
                if control.decision not in {"CONSIDER", "APPLY"}:
                    raise InterferenceShadowViewError(f"{cid} exclusion leaked into unscoped feasibility query")
                controls[cid] = control.to_dict()
            raw_after = raw_evidence_digest(ram)
            ram.execute("ROLLBACK TO interference_shadow_view")
            ram.execute("RELEASE interference_shadow_view")
            if _connection_digest(ram) != source_logical or raw_after != raw_before:
                raise InterferenceShadowViewError("rollback or canonical preservation failed")
            for cid, audit in sorted(authority["cases"].items()):
                route, candidate = _route_candidate(ram, _query(audit["query"], utility_facts))
                if candidate is None or candidate.to_dict() != cases[cid]["before_candidate"]:
                    raise InterferenceShadowViewError(f"{cid} rollback did not restore exact Mt candidate")
                restored[cid] = route.to_dict()
    finally:
        ram.close()
    if _sha256(source) != plan["source_database"]["sha256"]:
        raise InterferenceShadowViewError("source database changed")
    payload = {
        "version": "p13-interference-shadow-view-preflight-v1", "campaign_id": plan["campaign_id"],
        "source_bound_plan": {"path": str(plan_path), "sha256": _sha256(plan_path), "report_digest": supplied},
        "child_knowledge": child.to_dict(), "child_content_digest": child.content_digest,
        "revision": revision.to_dict(), "evaluation_view_authority": ledger.to_dict(),
        "shadow_state": shadow_state.to_dict(), "evaluation_state": evaluation_state.to_dict(),
        "utility_context_adapter": plan["utility_context_adapter"],
        "case_routes": cases, "unscoped_query_controls": controls, "rollback_routes": restored,
        "preflight_passed": True, "raw_evidence_preserved": True,
        "source_database_unchanged": True, "staging_discarded": True,
        "target_execution_performed": False, "non_target_execution_performed": False,
        "heldout_execution_performed": False, "anti_forgetting_witness_created": False,
        "applied_shadow_update_receipt_created": False,
        "evaluation_only": True, "canonical_memory_mutation": "none",
        "production_runtime_imported": False, "promotion_attempted": False,
        "memory_docs_submitted": False,
    }
    payload["report_digest"] = _digest(payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-bound-plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        payload = audit_shadow_view(args.source_bound_plan, output=args.output)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"report_digest": payload["report_digest"], "preflight_passed": True}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
