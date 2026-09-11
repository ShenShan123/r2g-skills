#!/usr/bin/env python3
"""Apply one aggregate StateShift support expansion in disposable staging.

This runner is reason-specific: it consumes the source-bound aggregate plan,
typed support-expansion receipt, and an eligible file-bound anti-forgetting
witness.  Parent scoped ORFS transitions are replayed only inside the RAM
staging copy.  The source SQLite, canonical evidence, lifecycle authority, and
production runtime remain unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.evolution import (  # noqa: E402
    AntiForgettingWitness, AppliedShadowUpdateReceipt, LocalizedUpdatePlan,
    StateShiftSupportExpansionReceipt, apply_localized_update_shadow,
)
from tehm.ids import stable_dumps  # noqa: E402
from tehm.knowledge import MechanismKnowledge  # noqa: E402
from tehm.state import SupportEnvelope  # noqa: E402


REPORT_VERSION = "p13-state-shift-shadow-update-report-v1"
SHADOW_MATERIALIZED_AT = "2000-01-01T00:00:00+00:00"


class P13StateShiftShadowUpdateError(ValueError):
    """The reason-specific P13 mutation chain is incomplete or unsafe."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _load(path: Path, name: str) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise P13StateShiftShadowUpdateError(
            f"cannot read {name}: {path}") from exc
    if not isinstance(value, dict):
        raise P13StateShiftShadowUpdateError(f"{name} must be an object")
    return value


def _content_digest(payload: Mapping, field: str, name: str) -> str:
    supplied = payload.get(field)
    replay = dict(payload)
    replay.pop(field, None)
    if type(supplied) is not str or supplied != _digest(replay):
        raise P13StateShiftShadowUpdateError(f"{name} {field} mismatch")
    return supplied


def _path(raw: object, *, relative_to: Path, name: str) -> Path:
    if type(raw) is not str or not raw.strip():
        raise P13StateShiftShadowUpdateError(f"{name} path is missing")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = relative_to.parent / path
    path = path.resolve()
    if not path.is_file():
        raise P13StateShiftShadowUpdateError(f"{name} is not a file")
    return path


def _logical_digest(path: Path) -> str:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return _digest("\n".join(conn.iterdump()))
    finally:
        conn.close()


def _authority_closed(payload: Mapping, name: str) -> None:
    if (payload.get("evaluation_only") is not True or
            payload.get("canonical_memory_mutation") != "none" or
            payload.get("production_runtime_imported") is not False or
            payload.get("production_integration") not in {
                None, "not_attempted"} or
            payload.get("memory_docs_submitted") not in {None, False}):
        raise P13StateShiftShadowUpdateError(
            f"{name} crossed an authority boundary")


def _anti_forgetting(path: Path, *, expansion_path: Path,
                     campaign_id: str,
                     expansion_receipt_id: str) -> AntiForgettingWitness:
    report = _load(path, "anti-forgetting witness report")
    if (report.get("version") != "p13-anti-forgetting-witness-report-v1" or
            report.get("campaign_id") != campaign_id or
            report.get("case_id") != "state-shift-support-expansion:u50" or
            report.get("eligible") is not True or
            report.get("canonical_memory_mutation") != "none" or
            report.get("production_runtime_imported") is not False or
            report.get("production_integration") != "not_attempted" or
            report.get("memory_docs_submitted") is not False):
        raise P13StateShiftShadowUpdateError(
            "anti-forgetting witness report authority or identity mismatch")
    try:
        witness = AntiForgettingWitness.from_dict(report.get("witness"))
    except (TypeError, ValueError) as exc:
        raise P13StateShiftShadowUpdateError(
            f"anti-forgetting witness is invalid: {exc}") from exc
    target = report.get("evidence", {}).get("target_replay")
    if (witness.eligible is not True or
            report.get("witness", {}).get("receipt_digest") !=
            witness.receipt_digest or
            not isinstance(target, Mapping) or
            Path(str(target.get("path"))).expanduser().resolve() !=
            expansion_path or
            target.get("sha256") != _sha256(expansion_path) or
            target.get("receipt_id") != expansion_receipt_id or
            witness.target_replay_receipt_id != expansion_receipt_id or
            witness.target_replay_digest != _sha256(expansion_path)):
        raise P13StateShiftShadowUpdateError(
            "anti-forgetting target replay differs from support expansion")
    return witness


def run_p13_state_shift_shadow_update(
        support_expansion_report: Path | str,
        anti_forgetting_report: Path | str, *, output: Path | str) -> dict:
    expansion_path = Path(support_expansion_report).expanduser().resolve()
    anti_path = Path(anti_forgetting_report).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()
    if output_path in {expansion_path, anti_path} or output_path.exists():
        raise P13StateShiftShadowUpdateError(
            "shadow update output must be a new file distinct from inputs")
    expansion = _load(expansion_path, "support expansion report")
    expansion_digest = _content_digest(
        expansion, "report_digest", "support expansion report")
    _authority_closed(expansion, "support expansion report")
    if (expansion.get("support_expansion_derived") is not True or
            expansion.get("target_replay", {}).get("passed") is not True or
            expansion.get("eligible_for_anti_forgetting") is not True or
            expansion.get("shadow_update_attempted") is not False):
        raise P13StateShiftShadowUpdateError(
            "support expansion is not ready for anti-forgetting consumption")
    try:
        child = MechanismKnowledge.from_dict(expansion.get("child_knowledge"))
        parent_envelope = SupportEnvelope.from_dict(
            expansion.get("parent_support_envelope"))
        child_envelope = SupportEnvelope.from_dict(
            expansion.get("child_support_envelope"))
        receipt = StateShiftSupportExpansionReceipt.from_dict(
            expansion.get("support_expansion_receipt"))
    except (KeyError, TypeError, ValueError) as exc:
        raise P13StateShiftShadowUpdateError(
            f"support expansion typed payload is invalid: {exc}") from exc
    if (receipt.child_knowledge_digest != child.content_digest or
            receipt.parent_support_envelope_digest !=
            parent_envelope.envelope_digest or
            receipt.child_support_envelope_digest !=
            child_envelope.envelope_digest):
        raise P13StateShiftShadowUpdateError(
            "support expansion receipt typed binding mismatch")
    witness = _anti_forgetting(
        anti_path, expansion_path=expansion_path,
        campaign_id=receipt.campaign_id,
        expansion_receipt_id=receipt.receipt_id)

    source_bound_ref = expansion.get("source_bound_plan_report")
    if not isinstance(source_bound_ref, Mapping):
        raise P13StateShiftShadowUpdateError(
            "support expansion source-bound plan binding is missing")
    source_bound_path = _path(
        source_bound_ref.get("path"), relative_to=expansion_path,
        name="source-bound plan report")
    if _sha256(source_bound_path) != source_bound_ref.get("sha256"):
        raise P13StateShiftShadowUpdateError(
            "source-bound plan file binding mismatch")
    source_bound = _load(source_bound_path, "source-bound plan report")
    source_bound_digest = _content_digest(
        source_bound, "report_digest", "source-bound plan report")
    _authority_closed(source_bound, "source-bound plan report")
    if source_bound_digest != source_bound_ref.get("report_digest"):
        raise P13StateShiftShadowUpdateError(
            "source-bound plan content binding mismatch")
    raw_plan = source_bound.get("source_bound_localized_update_plan")
    try:
        base_plan = LocalizedUpdatePlan.from_dict(raw_plan)
    except (TypeError, ValueError) as exc:
        raise P13StateShiftShadowUpdateError(
            f"source-bound plan is invalid: {exc}") from exc
    if (not isinstance(raw_plan, Mapping) or
            raw_plan.get("plan_digest") != base_plan.plan_digest or
            base_plan.operation != "REVISE" or
            base_plan.update_target != "UPDATE_CAUSAL_KNOWLEDGE" or
            base_plan.knowledge_refs != (receipt.parent_knowledge_object_id,) or
            base_plan.campaign_id != receipt.campaign_id):
        raise P13StateShiftShadowUpdateError(
            "source-bound plan is not the aggregate StateShift revision")

    snapshot_ref = expansion.get("source_snapshot_report")
    if not isinstance(snapshot_ref, Mapping):
        raise P13StateShiftShadowUpdateError(
            "support expansion source snapshot binding is missing")
    snapshot_path = _path(snapshot_ref.get("path"), relative_to=expansion_path,
                          name="source snapshot report")
    if _sha256(snapshot_path) != snapshot_ref.get("sha256"):
        raise P13StateShiftShadowUpdateError(
            "source snapshot file binding mismatch")
    snapshot = _load(snapshot_path, "source snapshot report")
    snapshot_digest = _content_digest(
        snapshot, "report_digest", "source snapshot report")
    if snapshot_digest != snapshot_ref.get("report_digest"):
        raise P13StateShiftShadowUpdateError(
            "source snapshot content binding mismatch")
    replay = snapshot.get("replay")
    resolved_state = replay.get("resolved_source_state") if isinstance(
        replay, Mapping) else None
    training_campaign = replay.get("campaign_id") if isinstance(
        replay, Mapping) else None
    if (not isinstance(resolved_state, Mapping) or
            type(training_campaign) is not str or
            base_plan.state_resolution_id != resolved_state.get(
                "resolution_id")):
        raise P13StateShiftShadowUpdateError(
            "source-bound plan resolution does not match source snapshot")
    acquisition_ref = snapshot.get("parent_acquisitions")
    if not isinstance(acquisition_ref, Mapping):
        raise P13StateShiftShadowUpdateError(
            "source snapshot parent acquisition binding is missing")
    acquisition_path = _path(
        acquisition_ref.get("path"), relative_to=snapshot_path,
        name="parent acquisitions")
    if _sha256(acquisition_path) != acquisition_ref.get("sha256"):
        raise P13StateShiftShadowUpdateError(
            "parent acquisition file binding mismatch")
    acquisition = _load(acquisition_path, "parent acquisitions")
    acquisitions = acquisition.get("acquisitions")
    acquisition_digest = acquisition.get("digest")
    if (not isinstance(acquisitions, dict) or
            acquisition_digest != _digest(acquisitions) or
            acquisition_digest != acquisition_ref.get("acquisition_digest")):
        raise P13StateShiftShadowUpdateError(
            "parent acquisition content binding mismatch")

    source = expansion.get("source_database")
    if not isinstance(source, Mapping):
        raise P13StateShiftShadowUpdateError(
            "support expansion source database binding is missing")
    source_path = _path(source.get("path"), relative_to=expansion_path,
                        name="source database")
    source_sha_before = _sha256(source_path)
    if (source_sha_before != source.get("sha256") or
            _logical_digest(source_path) != source.get("logical_digest")):
        raise P13StateShiftShadowUpdateError(
            "source database differs from support expansion binding")

    execution_plan = replace(base_plan, evidence_refs=tuple(sorted({
        *base_plan.evidence_refs,
        expansion_digest,
        receipt.receipt_digest,
        witness.receipt_digest,
        _sha256(anti_path),
    })))
    transition_ids = tuple(parent_envelope.source_transition_ids)
    if not transition_ids or not set(transition_ids) <= set(
            execution_plan.evidence_refs):
        raise P13StateShiftShadowUpdateError(
            "execution-bound plan lacks parent training transitions")
    knowledge_evidence = tuple({
        "evidence_type": "orfs_p12_target_execution",
        "evidence_id": receipt.target_execution_digests[case_id],
        "split": "training",
        "lineage_id": receipt.case_lineages[case_id],
        "evidence_level": child.evidence_level,
    } for case_id in sorted(receipt.case_lineages))
    evidence = {
        "created_at": SHADOW_MATERIALIZED_AT,
        "scope": dict(resolved_state["scope"]),
        "transition_ids": list(transition_ids),
        "transition_campaigns": {
            transition_id: training_campaign
            for transition_id in transition_ids
        },
        "scoped_learning_replay": {
            "campaign_id": training_campaign,
            "acquisitions": acquisitions,
            "expected_digest": acquisition_digest,
        },
        "parent_object_id": receipt.parent_knowledge_object_id,
        "knowledge": child.to_dict(),
        "knowledge_evidence_refs": list(knowledge_evidence),
        "anti_forgetting": {
            **witness.to_dict(), "receipt_digest": witness.receipt_digest,
        },
        "provenance": {
            "authority": "isolated_state_shift_shadow_revision",
            "campaign_id": receipt.campaign_id,
            "source_bound_plan_digest": base_plan.plan_digest,
            "execution_bound_plan_digest": execution_plan.plan_digest,
            "support_expansion_receipt_digest": receipt.receipt_digest,
            "anti_forgetting_witness_digest": witness.receipt_digest,
            "evaluation_only": True,
        },
    }
    conn = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        applied = apply_localized_update_shadow(
            execution_plan, conn, evidence)
    finally:
        conn.close()
    source_sha_after = _sha256(source_path)
    if (source_sha_after != source_sha_before or
            applied.canonical_rows_changed is not False or
            applied.production_authority_changed is not False or
            applied.staging_discarded is not True or
            (not applied.created_object_ids and
             not applied.created_relation_ids) or
            applied.before_resolution_id == applied.after_resolution_id):
        raise P13StateShiftShadowUpdateError(
            "applied shadow update did not establish the R3-5 mutation boundary")
    replayed = AppliedShadowUpdateReceipt.from_dict(applied.to_dict())
    if replayed.receipt_digest != applied.receipt_digest:
        raise P13StateShiftShadowUpdateError(
            "AppliedShadowUpdateReceipt replay mismatch")

    report = {
        "version": REPORT_VERSION,
        "campaign_id": receipt.campaign_id,
        "support_expansion_report": {
            "path": str(expansion_path), "sha256": _sha256(expansion_path),
            "report_digest": expansion_digest,
            "receipt_digest": receipt.receipt_digest,
        },
        "anti_forgetting_report": {
            "path": str(anti_path), "sha256": _sha256(anti_path),
            "witness_digest": witness.receipt_digest,
        },
        "source_bound_plan": {
            "path": str(source_bound_path),
            "report_digest": source_bound_digest,
            "base_plan_digest": base_plan.plan_digest,
            "execution_bound_plan": execution_plan.to_dict(),
            "execution_bound_plan_digest": execution_plan.plan_digest,
        },
        "source_database": {
            "path": str(source_path),
            "sha256_before": source_sha_before,
            "sha256_after": source_sha_after,
            "logical_digest": source["logical_digest"],
            "opened_read_only": True,
        },
        "applied_shadow_update_receipt": applied.to_dict(),
        "r3_5_gates": {
            "evolution_reason_bound": True,
            "routing_bound": True,
            "paired_evidence_bound": True,
            "learner_partition_bound": True,
            "anti_forgetting_bound": True,
            "source_plan_bound": True,
            "source_database_bound": True,
            "canonical_rows_changed": False,
            "production_authority_changed": False,
            "staging_discarded": True,
            "resolution_changed": True,
            "created_shadow_objects": bool(applied.created_object_ids),
            "created_shadow_relations": bool(applied.created_relation_ids),
        },
        "r3_5_complete": True,
        "eligible_for_p14_attribution": True,
        "promotion_attempted": False,
        "evaluation_only": True,
        "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
        "production_integration": "not_attempted",
        "memory_docs_submitted": False,
    }
    report["report_digest"] = _digest(report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x") as stream:
        stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--support-expansion-report", type=Path, required=True)
    parser.add_argument("--anti-forgetting-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = run_p13_state_shift_shadow_update(
            args.support_expansion_report, args.anti_forgetting_report,
            output=args.output)
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        parser.error(str(exc))
    receipt = report["applied_shadow_update_receipt"]
    print(json.dumps({
        "output": str(args.output.expanduser().resolve()),
        "campaign_id": report["campaign_id"],
        "r3_5_complete": report["r3_5_complete"],
        "applied_shadow_update_receipt_digest": receipt["receipt_digest"],
        "created_object_ids": receipt["created_object_ids"],
        "created_relation_ids": receipt["created_relation_ids"],
        "before_resolution_id": receipt["before_resolution_id"],
        "after_resolution_id": receipt["after_resolution_id"],
        "canonical_rows_changed": receipt["canonical_rows_changed"],
        "production_authority_changed": receipt[
            "production_authority_changed"],
        "staging_discarded": receipt["staging_discarded"],
        "report_digest": report["report_digest"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
