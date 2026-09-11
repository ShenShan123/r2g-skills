#!/usr/bin/env python3
"""Rebuild an isolated StateShift source snapshot from frozen acquisitions.

The scoped parent evidence is replayed in an in-memory database because its
learner authority is deliberately valid only inside ``scoped_learning_replay``.
The resulting database is then backed up as a sidecar-free, read-only source
snapshot for a later P13 shadow runner.  This command never updates canonical
memory or production runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm import db  # noqa: E402
from tehm.adapters.orfs_scoped import build_flow_feasibility_record  # noqa: E402
from tehm.artifact_store import ArtifactStore  # noqa: E402
from tehm.assets import register_asset_proposal, set_asset_status  # noqa: E402
from tehm.assets.flow_config import build_flow_asset_proposal  # noqa: E402
from tehm.canonical.capture import capture  # noqa: E402
from tehm.causal.intervention import build_intervention_pair  # noqa: E402
from tehm.causal.path_builder import (  # noqa: E402
    build_transition_causal_fragment,
    consolidate_causal_path,
)
from tehm.causal.replication import evaluate_replicated_effect  # noqa: E402
from tehm.evolution import LocalizedUpdatePlan  # noqa: E402
from tehm.evolution.state_rebase import state_semantic_digest  # noqa: E402
from tehm.ids import stable_dumps  # noqa: E402
from tehm.knowledge import (  # noqa: E402
    build_knowledge_from_path,
    record_knowledge_authority,
    register_knowledge,
    set_knowledge_status,
)
from tehm.state import (  # noqa: E402
    StateShiftReceipt,
    build_support_envelope_from_transitions,
    resolve_current_state,
)
from tehm.verified_execution import scoped_learning_replay  # noqa: E402


REPORT_VERSION = "p13-state-shift-source-snapshot-v1"
PLAN_REPORT_VERSION = "p13-state-shift-plan-report-v1"
REPLAY_MATERIALIZED_AT = "2000-01-01T00:00:00+00:00"


class P13StateShiftSourceSnapshotError(ValueError):
    """Frozen parent evidence cannot produce an isolated source snapshot."""


def _sha256(path: Path, *, prefix: bool = True) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    value = digest.hexdigest()
    return "sha256:" + value if prefix else value


def _digest(payload: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(payload).encode()).hexdigest()


def _load(path: Path, name: str) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise P13StateShiftSourceSnapshotError(
            f"cannot read {name}: {path}") from exc
    if not isinstance(payload, dict):
        raise P13StateShiftSourceSnapshotError(f"{name} must be a JSON object")
    return payload


def _content_digest(payload: Mapping, field: str, name: str) -> str:
    supplied = payload.get(field)
    if type(supplied) is not str or not supplied.startswith("sha256:"):
        raise P13StateShiftSourceSnapshotError(f"{name} requires {field}")
    replay = dict(payload)
    replay.pop(field, None)
    if supplied != _digest(replay):
        raise P13StateShiftSourceSnapshotError(f"{name} {field} mismatch")
    return supplied


def _bound_path(raw: object, *, relative_to: Path, name: str) -> Path:
    if type(raw) is not str or not raw.strip():
        raise P13StateShiftSourceSnapshotError(f"{name} path is missing")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = relative_to.parent / path
    path = path.resolve()
    if not path.is_file():
        raise P13StateShiftSourceSnapshotError(f"{name} is not a file")
    return path


def _plan_chain(plan_path: Path) -> tuple[dict, LocalizedUpdatePlan, dict, Path]:
    payload = _load(plan_path, "StateShift plan report")
    if payload.get("version") != PLAN_REPORT_VERSION:
        raise P13StateShiftSourceSnapshotError("plan report version mismatch")
    _content_digest(payload, "report_digest", "plan report")
    if (payload.get("plan_eligible_for_anti_forgetting") is not True or
            payload.get("source_database_present") is not False or
            payload.get("anti_forgetting_present") is not False or
            payload.get("shadow_update_attempted") is not False):
        raise P13StateShiftSourceSnapshotError(
            "plan report is not an eligible pre-snapshot boundary")
    if (payload.get("evaluation_only") is not True or
            payload.get("canonical_memory_mutation") != "none" or
            payload.get("production_runtime_imported") is not False or
            payload.get("production_integration") != "not_attempted" or
            payload.get("memory_docs_submitted") is not False):
        raise P13StateShiftSourceSnapshotError(
            "plan report crosses an authority boundary")
    raw_plan = payload.get("localized_update_plan")
    try:
        plan = LocalizedUpdatePlan.from_dict(raw_plan)
    except (TypeError, ValueError) as exc:
        raise P13StateShiftSourceSnapshotError(
            f"localized plan is invalid: {exc}") from exc
    if (not isinstance(raw_plan, Mapping) or
            raw_plan.get("plan_digest") != plan.plan_digest or
            plan.update_target != "UPDATE_CAUSAL_KNOWLEDGE" or
            plan.operation != "REVISE" or plan.learner_eligible is not True):
        raise P13StateShiftSourceSnapshotError(
            "localized plan is not the admitted StateShift revision")

    admission_binding = payload.get("admission_report")
    if not isinstance(admission_binding, Mapping):
        raise P13StateShiftSourceSnapshotError(
            "plan report lacks admission binding")
    admission_path = _bound_path(
        admission_binding.get("path"), relative_to=plan_path,
        name="admission report")
    if admission_binding.get("sha256") != _sha256(admission_path):
        raise P13StateShiftSourceSnapshotError(
            "plan admission file binding mismatch")
    admission = _load(admission_path, "admission report")
    admission_digest = _content_digest(
        admission, "report_digest", "admission report")
    if admission_binding.get("report_digest") != admission_digest:
        raise P13StateShiftSourceSnapshotError(
            "plan admission content binding mismatch")
    inputs = admission.get("inputs")
    audit_binding = inputs.get("preregistration_audit") if isinstance(
        inputs, Mapping) else None
    if not isinstance(audit_binding, Mapping):
        raise P13StateShiftSourceSnapshotError(
            "admission report lacks preregistration audit binding")
    audit_path = _bound_path(
        audit_binding.get("path"), relative_to=admission_path,
        name="preregistration audit")
    if audit_binding.get("sha256") != _sha256(audit_path):
        raise P13StateShiftSourceSnapshotError(
            "admission preregistration file binding mismatch")
    audit = _load(audit_path, "preregistration audit")
    _content_digest(audit, "audit_digest", "preregistration audit")
    if (audit.get("execution_started") is not False or
            audit.get("production_database_writes") is not False or
            audit.get("promotion_attempted") is not False):
        raise P13StateShiftSourceSnapshotError(
            "preregistration audit crossed an execution boundary")
    return payload, plan, audit, audit_path


def _parent_inputs(audit: Mapping, audit_path: Path) -> tuple[dict, dict, Path, Path]:
    source_freeze = audit.get("source_freeze")
    raw_inputs = source_freeze.get("external_inputs") if isinstance(
        source_freeze, Mapping) else None
    if not isinstance(raw_inputs, Mapping):
        raise P13StateShiftSourceSnapshotError(
            "preregistration source freeze is missing")
    candidates = []
    parent_audits = []
    for raw_path, expected_sha in raw_inputs.items():
        path = _bound_path(
            raw_path, relative_to=audit_path, name="frozen external input")
        supplied = str(expected_sha)
        actual = _sha256(path, prefix=False)
        if supplied.removeprefix("sha256:") != actual:
            raise P13StateShiftSourceSnapshotError(
                f"frozen external input digest mismatch: {path}")
        name = path.name
        if "scoped-support-envelope" in name and "acquisitions" in name:
            candidates.append(path)
        if "scoped-support-envelope" in name and "audit" in name:
            parent_audits.append(path)
    if len(candidates) != 1 or len(parent_audits) != 1:
        raise P13StateShiftSourceSnapshotError(
            "source freeze must bind one parent acquisition and audit")
    acquisition_path = candidates[0]
    parent_audit_path = parent_audits[0]
    acquisition = _load(acquisition_path, "parent acquisitions")
    parent_audit = _load(parent_audit_path, "parent audit")
    acquisitions = acquisition.get("acquisitions")
    if not isinstance(acquisitions, dict) or not acquisitions:
        raise P13StateShiftSourceSnapshotError(
            "parent acquisition mapping is empty")
    if acquisition.get("digest") != _digest(acquisitions):
        raise P13StateShiftSourceSnapshotError(
            "parent acquisition content digest mismatch")
    parent = audit.get("parent_replay")
    if not isinstance(parent, Mapping):
        raise P13StateShiftSourceSnapshotError(
            "preregistration audit lacks parent replay")
    if (parent_audit.get("acquisition_digest") != acquisition["digest"] or
            parent.get("acquisition_digest") != acquisition["digest"]):
        raise P13StateShiftSourceSnapshotError(
            "parent acquisition digest is not consistently bound")
    for key in ("pairs", "path_id", "replication", "knowledge",
                "support_envelope", "authority"):
        if stable_dumps(parent.get(key)) != stable_dumps(parent_audit.get(key)):
            raise P13StateShiftSourceSnapshotError(
                f"preregistration parent replay drifted for {key}")
    parent_audit = dict(parent_audit)
    parent_audit["campaign_id"] = parent.get("campaign_id")
    return acquisition, parent_audit, acquisition_path, parent_audit_path


def _challenge_state_binding(
    audit: Mapping,
    plan_payload: Mapping,
    plan: LocalizedUpdatePlan,
) -> dict:
    """Replay the pre-execution StateShift identity bound by the plan.

    The challenge resolution and the parent training campaign are deliberately
    different namespaces.  This check binds both without relabelling either
    one or pretending that a reconstructed SQLite image has the historical
    resolution identity.
    """
    raw_cases = audit.get("cases")
    if (not isinstance(raw_cases, Sequence) or
            isinstance(raw_cases, (str, bytes)) or not raw_cases):
        raise P13StateShiftSourceSnapshotError(
            "preregistration StateShift cases are missing")
    case_ids = []
    context_digests = []
    resolution_ids = []
    receipt_ids = []
    for item in raw_cases:
        if not isinstance(item, Mapping):
            raise P13StateShiftSourceSnapshotError(
                "preregistration StateShift case is malformed")
        case_id = item.get("case_id")
        registration = item.get("registration")
        try:
            shift = StateShiftReceipt.from_dict(item.get("state_shift"))
        except (TypeError, ValueError, KeyError) as exc:
            raise P13StateShiftSourceSnapshotError(
                f"preregistration StateShift receipt is invalid: {exc}") from exc
        if (type(case_id) is not str or not case_id or
                not isinstance(registration, Mapping) or
                shift.reason != "STATE_SHIFT" or shift.transferable is not False or
                shift.knowledge_object_id not in plan.knowledge_refs or
                shift.current_context_digest != registration.get(
                    "current_context_digest")):
            raise P13StateShiftSourceSnapshotError(
                "preregistration StateShift identity does not match plan")
        case_ids.append(case_id)
        context_digests.append(shift.current_context_digest)
        resolution_ids.append(shift.current_resolution_id)
        receipt_ids.append(shift.receipt_id)
    if len(set(case_ids)) != len(case_ids):
        raise P13StateShiftSourceSnapshotError(
            "preregistration StateShift case IDs are duplicated")
    if (sorted(resolution_ids) != sorted(
            plan_payload.get("proposal_state_resolution_ids") or ()) or
            sorted(context_digests) != sorted(
                plan_payload.get("proposal_state_context_digests") or ()) or
            plan.state_resolution_id not in resolution_ids):
        raise P13StateShiftSourceSnapshotError(
            "preregistration StateShift identities do not match proposal")
    return {
        "case_ids": sorted(case_ids),
        "receipt_ids": sorted(receipt_ids),
        "plan_resolution_id": plan.state_resolution_id,
        "proposal_resolution_ids": sorted(resolution_ids),
        "current_context_digests": sorted(context_digests),
    }


def _logical_digest(conn: sqlite3.Connection) -> str:
    return _digest("\n".join(conn.iterdump()))


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'tehm_%' "
        "ORDER BY name"
    ).fetchall()
    return {
        str(row[0]): int(conn.execute(
            f'SELECT COUNT(*) FROM "{row[0]}"').fetchone()[0])
        for row in rows
    }


def _rebuild_parent(
    acquisition: Mapping,
    parent_audit: Mapping,
    *,
    source_db: Path,
    artifacts: Path,
) -> dict:
    acquisitions = acquisition["acquisitions"]
    campaign_id = parent_audit.get("campaign_id")
    if type(campaign_id) is not str or not campaign_id:
        raise P13StateShiftSourceSnapshotError(
            "parent replay campaign is invalid")
    target_scope = (parent_audit.get("authority") or {}).get("target_scope")
    if type(target_scope) is not str or not target_scope:
        raise P13StateShiftSourceSnapshotError(
            "parent replay target scope is invalid")
    pairs_by_lineage: dict[str, dict[str, str]] = defaultdict(dict)
    for transition_id, item in acquisitions.items():
        if (type(transition_id) is not str or not isinstance(item, Mapping) or
                type(item.get("lineage_id")) is not str or
                item.get("role") not in {"control", "treatment"}):
            raise P13StateShiftSourceSnapshotError(
                "parent acquisition identity is malformed")
        lineage = item["lineage_id"]
        role = item["role"]
        if role in pairs_by_lineage[lineage]:
            raise P13StateShiftSourceSnapshotError(
                "parent acquisition roles are duplicated")
        pairs_by_lineage[lineage][role] = transition_id
    if (len(pairs_by_lineage) < 2 or any(
            set(pair) != {"control", "treatment"}
            for pair in pairs_by_lineage.values())):
        raise P13StateShiftSourceSnapshotError(
            "parent acquisitions require exact pairs across two lineages")

    original_now_local = db.now_local
    db.now_local = lambda: REPLAY_MATERIALIZED_AT
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    file_conn = None
    try:
        db.ensure_schema(conn)
        store = ArtifactStore(artifacts)
        for expected_id, item in acquisitions.items():
            record = build_flow_feasibility_record(**dict(item))
            receipt = capture(
                conn, store, record,
                dataset_campaign_id=campaign_id,
                dataset_split="training",
                dataset_learner_eligible=True,
            )
            if receipt.transition_id != expected_id:
                raise P13StateShiftSourceSnapshotError(
                    "parent transition identity changed during replay")

        with scoped_learning_replay(
            conn,
            campaign_id=campaign_id,
            acquisitions=acquisitions,
            expected_digest=acquisition["digest"],
        ):
            pairs = []
            treatments = []
            for lineage in sorted(pairs_by_lineage):
                pair = pairs_by_lineage[lineage]
                controlled = build_intervention_pair(
                    conn, pair["control"], pair["treatment"],
                    campaign_id=campaign_id,
                    target_scope=target_scope,
                )
                if controlled.validity_status != "VALID_CONTROLLED_PAIR":
                    raise P13StateShiftSourceSnapshotError(
                        "parent intervention pair is not valid")
                pairs.append(controlled.to_dict())
                treatments.append(pair["treatment"])
            fragments = [
                build_transition_causal_fragment(
                    conn, transition_id, campaign_id=campaign_id)
                for transition_id in acquisitions
            ]
            path = consolidate_causal_path(
                conn, fragments, campaign_id=campaign_id, status="shadow")
            replication = evaluate_replicated_effect(
                conn, path.path_id, campaign_id=campaign_id)
            if not replication.eligible:
                raise P13StateShiftSourceSnapshotError(
                    "parent replicated effect is not eligible")
            knowledge = build_knowledge_from_path(conn, path.path_id)
            register_knowledge(conn, knowledge, target_scope=target_scope)
            authority = record_knowledge_authority(
                conn, knowledge, target_scope=target_scope)
            if not authority.eligible:
                raise P13StateShiftSourceSnapshotError(
                    "replayed parent did not regain scoped authority")
            set_knowledge_status(
                conn,
                knowledge_id=knowledge.knowledge_id,
                version=knowledge.version,
                target_scope=target_scope,
                status="validated",
                authority_receipt=authority,
                provenance={"purpose": "p13_state_shift_source_snapshot"},
            )
            envelope = build_support_envelope_from_transitions(
                conn, knowledge, treatments,
                campaign_id=campaign_id)
            proposal = build_flow_asset_proposal(
                conn, knowledge.object_id,
                campaign_id=campaign_id,
                target_scope=target_scope)
            asset = register_asset_proposal(conn, proposal)
            for status in ("shadow", "candidate"):
                set_asset_status(
                    conn, asset_id=asset.asset_id,
                    target_scope=target_scope, status=status)

            source_scope = {
                "mechanism_family": knowledge.mechanism_family,
                "target_scope": target_scope,
            }
            if knowledge.compatibility_profile is not None:
                source_scope["compatibility_profile"] = (
                    knowledge.compatibility_profile)
            source_state = resolve_current_state(
                conn, source_scope, mode="shadow", persist=False)
            expected_state = {
                "active_rules": (),
                "active_causal_paths": (path.path_id,),
                "active_knowledge_claims": (knowledge.object_id,),
                "active_assets": (asset.asset_id,),
                "active_capabilities": (),
                "relation_ids": (),
                "shadow_relation_ids": (),
                "unresolved_conflicts": (),
            }
            for field, expected in expected_state.items():
                if tuple(getattr(source_state, field)) != expected:
                    raise P13StateShiftSourceSnapshotError(
                        f"replayed source state has unexpected {field}")
            if source_state.suppressed:
                raise P13StateShiftSourceSnapshotError(
                    "replayed source state unexpectedly suppresses memory")

        expected_pairs = parent_audit.get("pairs")
        if stable_dumps(pairs) != stable_dumps(expected_pairs):
            raise P13StateShiftSourceSnapshotError(
                "replayed intervention pairs differ from parent audit")
        checks = {
            "path_id": (path.path_id, parent_audit.get("path_id")),
            "replication": (
                replication.to_dict(), parent_audit.get("replication")),
            "knowledge": (knowledge.to_dict(), parent_audit.get("knowledge")),
            "support_envelope": (
                envelope.to_dict(), parent_audit.get("support_envelope")),
            "authority": (authority.to_dict(), parent_audit.get("authority")),
        }
        for name, (actual, expected) in checks.items():
            if stable_dumps(actual) != stable_dumps(expected):
                raise P13StateShiftSourceSnapshotError(
                    f"replayed {name} differs from parent audit")
        logical_digest = _logical_digest(conn)
        counts = _table_counts(conn)
        file_conn = sqlite3.connect(str(source_db))
        conn.backup(file_conn)
        file_conn.commit()
        file_conn.close()
        file_conn = None
    finally:
        if file_conn is not None:
            file_conn.close()
        conn.close()
        db.now_local = original_now_local
    sidecars = [Path(str(source_db) + suffix) for suffix in ("-wal", "-shm")]
    if any(path.exists() for path in sidecars):
        raise P13StateShiftSourceSnapshotError(
            "source snapshot retained SQLite sidecars")
    read_only = db.connect_read_only(source_db)
    try:
        if _logical_digest(read_only) != logical_digest:
            raise P13StateShiftSourceSnapshotError(
                "source snapshot logical digest changed during backup")
        if _table_counts(read_only) != counts:
            raise P13StateShiftSourceSnapshotError(
                "source snapshot row counts changed during backup")
    finally:
        read_only.close()
    if any(path.exists() for path in sidecars):
        raise P13StateShiftSourceSnapshotError(
            "read-only verification created SQLite sidecars")
    return {
        "campaign_id": campaign_id,
        "target_scope": target_scope,
        "acquisition_digest": acquisition["digest"],
        "transition_ids": sorted(acquisitions),
        "lineage_count": len(pairs_by_lineage),
        "pair_ids": sorted(item["pair_id"] for item in pairs),
        "path_id": path.path_id,
        "knowledge_object_id": knowledge.object_id,
        "knowledge_content_digest": knowledge.content_digest,
        "support_envelope_digest": envelope.envelope_digest,
        "authority_receipt_digest": authority.receipt_digest,
        "logical_database_digest": logical_digest,
        "table_counts": counts,
        "asset_id": asset.asset_id,
        "replay_materialized_at": REPLAY_MATERIALIZED_AT,
        "resolved_source_state": source_state.to_dict(),
        "resolved_source_semantic_digest": state_semantic_digest(source_state),
    }


def build_p13_state_shift_source_snapshot(
    plan_report: Path | str,
    *,
    output_dir: Path | str,
) -> dict:
    """Rebuild and freeze the admitted parent as an isolated source DB."""
    plan_path = Path(plan_report).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    source_db = output / "source-tehm.sqlite"
    artifacts = output / "source-artifacts"
    report_path = output / "source-snapshot-report.json"
    if output.exists():
        raise P13StateShiftSourceSnapshotError(
            f"immutable source snapshot output already exists: {output}")
    plan_payload, plan, audit, audit_path = _plan_chain(plan_path)
    acquisition, parent_audit, acquisition_path, parent_audit_path = (
        _parent_inputs(audit, audit_path))
    challenge_state = _challenge_state_binding(audit, plan_payload, plan)
    output.mkdir(parents=True)
    replay = _rebuild_parent(
        acquisition, parent_audit,
        source_db=source_db, artifacts=artifacts)
    if (replay["knowledge_object_id"] not in plan.knowledge_refs or
            replay["knowledge_object_id"] != plan_payload.get("knowledge_parent")):
        raise P13StateShiftSourceSnapshotError(
            "source snapshot Knowledge does not match localized plan")
    source_resolution = replay["resolved_source_state"]["resolution_id"]
    exact_resolution_match = source_resolution == plan.state_resolution_id
    exact_campaign_match = replay["campaign_id"] == plan.campaign_id
    report = {
        "version": REPORT_VERSION,
        "campaign_id": plan.campaign_id,
        "plan_report": {
            "path": str(plan_path), "sha256": _sha256(plan_path),
            "report_digest": plan_payload["report_digest"],
            "plan_digest": plan.plan_digest,
        },
        "preregistration_audit": {
            "path": str(audit_path), "sha256": _sha256(audit_path),
            "audit_digest": audit["audit_digest"],
        },
        "parent_acquisitions": {
            "path": str(acquisition_path),
            "sha256": _sha256(acquisition_path),
            "acquisition_digest": acquisition["digest"],
        },
        "parent_audit": {
            "path": str(parent_audit_path),
            "sha256": _sha256(parent_audit_path),
        },
        "source_database": {
            "path": str(source_db), "sha256": _sha256(source_db),
            "bytes": source_db.stat().st_size,
            "logical_digest": replay["logical_database_digest"],
            "sidecar_free": True,
            "role": "isolated_scoped_replay_source",
        },
        "source_artifacts": {
            "path": str(artifacts),
            "file_count": sum(path.is_file() for path in artifacts.rglob("*")),
        },
        "replay": replay,
        "campaign_binding": {
            "plan_campaign_id": plan.campaign_id,
            "training_evidence_campaign_id": replay["campaign_id"],
            "exact_match": exact_campaign_match,
            "training_membership_relabelled": False,
        },
        "state_binding": {
            **challenge_state,
            "source_resolution_id": source_resolution,
            "source_semantic_digest": replay[
                "resolved_source_semantic_digest"],
            "exact_resolution_match": exact_resolution_match,
            "resolution_rebase_required": not exact_resolution_match,
            "resolution_rebase_authorized": False,
        },
        "source_database_present": True,
        "source_database_mutated_after_freeze": False,
        "scoped_replay_required_for_shadow": True,
        "anti_forgetting_present": False,
        "shadow_update_attempted": False,
        "shadow_execution_ready": False,
        "remaining_gates": [
            "anti_forgetting_witness",
            *([] if exact_campaign_match else [
                "cross_campaign_training_evidence_verification"]),
            *([] if exact_resolution_match else [
                "state_resolution_rebase_authority"]),
        ],
        "evaluation_only": True,
        "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
        "production_integration": "not_attempted",
        "memory_docs_submitted": False,
    }
    report["report_digest"] = _digest(report)
    with report_path.open("x") as stream:
        stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-report", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build_p13_state_shift_source_snapshot(
            args.plan_report, output_dir=args.output_dir)
    except (OSError, sqlite3.Error, P13StateShiftSourceSnapshotError,
            TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({
        "output": str(args.output_dir.expanduser().resolve()),
        "campaign_id": report["campaign_id"],
        "source_database_sha256": report["source_database"]["sha256"],
        "logical_database_digest": report["source_database"]["logical_digest"],
        "knowledge_object_id": report["replay"]["knowledge_object_id"],
        "report_digest": report["report_digest"],
        "anti_forgetting_present": report["anti_forgetting_present"],
        "shadow_update_attempted": report["shadow_update_attempted"],
        "canonical_memory_mutation": report["canonical_memory_mutation"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
