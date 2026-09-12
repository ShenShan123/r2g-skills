#!/usr/bin/env python3
"""Consume real interference gates into the existing isolated P13 executor.

The original SQLite is opened read-only. A cold replay verifies actual gate
oracles and the exact child/rollback; the generic witness binder is replayed
against its ORIGINAL manifest so temporary paths cannot change the witness.
Only SPECIALIZE runs in discarded staging. Evaluation-child activation and
production integration remain separate, explicitly unperformed operations.
"""
from __future__ import annotations

import argparse
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

from scripts.build_p13_anti_forgetting_witness import build_p13_anti_forgetting_witness
from scripts.build_p13_interference_anti_forgetting_evidence import build_interference_anti_forgetting_evidence
from scripts.build_p13_interference_policy_views import _self_digest
from scripts.build_p13_interference_source_bound_plan import _digest, _load_json, _reference, _sha256
from scripts.build_r3_orfs_interference_source_bound_inputs import _write
from tehm.capability.delta import memory_delta_from_shadow_update
from tehm.evolution import AntiForgettingWitness, AppliedShadowUpdateReceipt, LocalizedUpdatePlan, apply_localized_update_shadow
from tehm.evolution.interference_revision import MemoryInterferenceEvolutionProposal
from tehm.ids import stable_dumps
from tehm.knowledge import MechanismKnowledge


class SourceBoundInterferenceShadowError(ValueError):
    """Formal P13 consumption lacks matching actual evidence or scope."""


def _pin(path):
    return {"path": str(path), "sha256": _sha256(path)}


def _training_refs(proposal, authority, evidence_level):
    partition = authority.get("learner_partition", {})
    if partition.get("learner_eligible") is not True:
        raise SourceBoundInterferenceShadowError("knowledge revision requires prospective training partition")
    refs = []
    for cid, digest in zip(proposal.case_ids, proposal.paired_receipt_digests, strict=True):
        row = authority["cases"][cid]
        membership = partition["cases"][cid]
        if (membership.get("dataset_split") != "training" or membership.get("role") != "training" or
                membership.get("learner_eligible") is not True):
            raise SourceBoundInterferenceShadowError("knowledge revision references non-training challenge evidence")
        refs.append({"evidence_type": "orfs_p12_physical_interference", "evidence_id": digest,
                     "split": "training", "lineage_id": row["lineage_id"], "evidence_level": evidence_level})
    return refs


def _execution_plan(plan_report, proposal, witness, extra_refs):
    raw = plan_report["source_bound_localized_update_plan"]
    base = LocalizedUpdatePlan.from_dict(raw)
    if (raw.get("plan_digest") != base.plan_digest or base.operation != "SPECIALIZE" or
            base.update_target != "UPDATE_CAUSAL_KNOWLEDGE" or
            base.failure_type != "MEMORY_INTERFERENCE" or base.learner_eligible is not True or
            base.knowledge_refs != (proposal.knowledge_object_id,) or
            base.campaign_id != proposal.campaign_id or witness.eligible is not True or
            not set(plan_report["parent_transition_ids"]) <= set(base.evidence_refs)):
        raise SourceBoundInterferenceShadowError("plan/proposal/witness is not an eligible source-bound specialization")
    return replace(base, evidence_refs=tuple(sorted({*base.evidence_refs, witness.receipt_digest, *extra_refs})))


def run_source_bound_interference_shadow_update(anti_forgetting_evidence, *, output):
    anti_path, output_path = (Path(p).resolve() for p in (anti_forgetting_evidence, output))
    if output_path.exists() or output_path == anti_path:
        raise SourceBoundInterferenceShadowError("shadow output must be new and separate from evidence")
    anti = _load_json(anti_path, "actual anti-forgetting evidence")
    _self_digest(anti, "report_digest")
    if (anti.get("version") != "p13-interference-actual-anti-forgetting-evidence-v1" or
            anti.get("eligible") is not True or anti.get("actual_gate_oracles_cold_replayed") is not True or
            anti.get("canonical_memory_mutation") != "none" or
            anti.get("production_runtime_imported") is not False or
            anti.get("promotion_attempted") is not False):
        raise SourceBoundInterferenceShadowError("anti-forgetting evidence is not eligible for formal P13")
    binder_pin = anti["audit_source_binding"]
    if _sha256(Path(binder_pin["path"])) != binder_pin["sha256"]:
        raise SourceBoundInterferenceShadowError("actual gate binder source drift")
    plan_path, plan = _reference(anti["source_bound_plan"], "source-bound plan")
    _self_digest(plan, "report_digest")
    preflight_path, preflight = _reference(anti["shadow_view_preflight"], "shadow-view preflight")
    _self_digest(preflight, "report_digest")
    witness_path, witness_report = _reference(anti["anti_forgetting_witness"], "typed witness report")
    witness = AntiForgettingWitness.from_dict(witness_report["witness"])
    if (witness.eligible is not True or anti["witness_digest"] != witness.receipt_digest or
            witness_report.get("eligible") is not True or
            witness_report.get("campaign_id") != plan["campaign_id"]):
        raise SourceBoundInterferenceShadowError("typed witness is not bound to this evolution campaign")
    manifest_path, _ = _reference(anti["anti_forgetting_manifest"], "original witness manifest")
    gate_paths = {purpose: _reference(anti["gate_evidence"][purpose], "actual gate evidence")[0]
                  for purpose in ("target_replay", "non_target", "heldout")}
    with tempfile.TemporaryDirectory(prefix="tehm-p13-interference-consumption-") as tmp:
        tmp_path = Path(tmp)
        cold = build_interference_anti_forgetting_evidence(
            plan_path, preflight_path, gate_paths["target_replay"], gate_paths["non_target"],
            gate_paths["heldout"], output_dir=tmp_path / "cold-gates")
        # Output paths differ; compare only content-bound semantic inputs and gates.
        for field in ("source_bound_plan", "shadow_view_preflight", "child_content_digest",
                      "gate_evidence", "gate_passed", "frozen_training_comparison_pins", "eligible",
                      "audit_source_binding"):
            if stable_dumps(cold[field]) != stable_dumps(anti[field]):
                raise SourceBoundInterferenceShadowError("actual anti-forgetting gates do not cold replay")
        _, original_rollback = _reference(anti["rollback_gate"], "original rollback gate")
        _, cold_rollback = _reference(cold["rollback_gate"], "cold rollback gate")
        if stable_dumps(original_rollback) != stable_dumps(cold_rollback):
            raise SourceBoundInterferenceShadowError("actual rollback gate does not cold replay")
        replayed_witness = build_p13_anti_forgetting_witness(
            manifest_path, output=tmp_path / "original-manifest-witness.json")
        if stable_dumps(replayed_witness) != stable_dumps(witness_report):
            raise SourceBoundInterferenceShadowError("original manifest does not replay this typed witness")
    proposal = MemoryInterferenceEvolutionProposal.from_dict(plan["proposal"])
    child = MechanismKnowledge.from_dict(preflight["child_knowledge"])
    parent = MechanismKnowledge.from_dict(plan["parent_knowledge"])
    if (child.content_digest != anti["child_content_digest"] or child.status != "shadow" or
            child.knowledge_id == parent.knowledge_id or child.version != 1):
        raise SourceBoundInterferenceShadowError("P13 replacement differs from the audited shadow child")
    _, authority = _reference(plan["input_authority"], "original training input authority")
    _, acquisition = _reference(plan["parent_acquisitions"], "original parent acquisitions")
    source = Path(plan["source_database"]["path"])
    source_sha = _sha256(source)
    if source_sha != plan["source_database"]["sha256"]:
        raise SourceBoundInterferenceShadowError("source database drift")
    execution_plan = _execution_plan(plan, proposal, witness, (
        _sha256(anti_path), _sha256(witness_path), anti["report_digest"], child.content_digest))
    tids = plan["parent_transition_ids"]
    evidence = {"created_at": "2000-01-01T00:00:00+00:00", "scope": plan["resolved_source_state"]["scope"],
                "transition_ids": tids,
                "transition_campaigns": {tid: plan["parent_training_campaign"] for tid in tids},
                "scoped_learning_replay": {"campaign_id": plan["parent_training_campaign"],
                    "acquisitions": acquisition["acquisitions"], "expected_digest": acquisition["digest"]},
                "parent_object_id": parent.object_id, "knowledge": child.to_dict(),
                "knowledge_evidence_refs": _training_refs(proposal, authority, parent.evidence_level),
                "anti_forgetting": {**witness.to_dict(), "receipt_digest": witness.receipt_digest}}
    conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        with patch("tehm.db.now_local", return_value=evidence["created_at"]):
            applied = apply_localized_update_shadow(execution_plan, conn, evidence)
    finally:
        conn.close()
    typed = AppliedShadowUpdateReceipt.from_dict(applied.to_dict())
    delta = memory_delta_from_shadow_update(typed)
    if (typed.receipt_digest != applied.receipt_digest or not delta.eligible or
            _sha256(source) != source_sha or applied.source_digest_before != plan["source_database"]["logical_digest"] or
            "knowledge:" + child.object_id not in applied.created_object_ids or
            preflight["revision"]["relation_id"] not in applied.created_relation_ids or
            applied.before_resolution_id != plan["resolved_source_state"]["resolution_id"] or
            applied.after_resolution_id != preflight["shadow_state"]["resolution_id"]):
        raise SourceBoundInterferenceShadowError("formal P13 receipt differs from the audited structural revision")
    report = {"version": "p13-interference-source-bound-shadow-update-report-v1",
              "campaign_id": plan["campaign_id"], "source_bound_plan": anti["source_bound_plan"],
              "anti_forgetting_evidence": {**_pin(anti_path), "report_digest": anti["report_digest"]},
              "shadow_view_preflight": anti["shadow_view_preflight"],
              "child_content_digest": child.content_digest, "source_database": plan["source_database"],
              "source_database_sha256_after": _sha256(source), "source_opened_read_only": True,
              "execution_bound_plan": {**execution_plan.to_dict(), "plan_digest": execution_plan.plan_digest},
              "execution_evidence": evidence, "applied_shadow_update_receipt": applied.to_dict(),
              "memory_delta_receipt": {**delta.to_dict(), "receipt_digest": delta.receipt_digest},
              "r3_5_mutation_bound": True, "eligible_for_p14_attribution": True,
              "evaluation_view_activation_performed_by_this_update": False,
              "formal_p14_attribution_created": False, "production_runtime_imported": False,
              "canonical_memory_mutation": "none", "promotion_attempted": False,
              "evaluation_only": True, "learner_audit_support_imported": False,
              "strict_signoff_claimed": False, "statistical_generalization_claimed": False,
              "runner_source_binding": _pin(Path(__file__).resolve()), "memory_docs_submitted": False}
    report["report_digest"] = _digest(report)
    _write(output_path, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anti-forgetting-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = run_source_bound_interference_shadow_update(args.anti_forgetting_evidence, output=args.output)
    except (KeyError, OSError, sqlite3.Error, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"r3_5_mutation_bound": report["r3_5_mutation_bound"],
                      "report_digest": report["report_digest"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
