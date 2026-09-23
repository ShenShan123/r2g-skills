"""Build a read-only Revision4 research M0 from two audited RC1 seed pairs."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from contracts import MemoryQuery
from tehm import db
from tehm.adapters.research_seed_scoped import (
    build_research_seed_record, verify_research_seed_acquisition,
)
from tehm.artifact_store import ArtifactStore
from tehm.assets import register_asset_proposal, set_asset_status
from tehm.assets.flow_config import build_flow_asset_proposal
from tehm.canonical.capture import capture
from tehm.causal.intervention import build_intervention_pair
from tehm.causal.path_builder import (
    build_transition_causal_fragment, consolidate_causal_path,
)
from tehm.causal.replication import evaluate_replicated_effect
from tehm.evaluation.research_epoch import verify_research_epoch
from tehm.ids import stable_dumps
from tehm.knowledge import (
    build_knowledge_from_path, record_knowledge_authority, register_knowledge,
    get_knowledge_by_object_id, set_knowledge_status,
)
from tehm.retrieval.asset_selector import select_knowledge_grounded_assets
from tehm.retrieval.memory_router import route_memory
from tehm.retrieval.structured_candidate import build_structured_candidate
from tehm.state import build_support_envelope_from_transitions
from tehm.state.resolver import resolve_current_state
from tehm.sync import export_bundle, verify_bundle
from tehm.verified_execution import scoped_learning_replay


SCHEMA = "tehm-r4-research-seed-m0-spec-v1"
REPORT_SCHEMA = "tehm-r4-research-seed-m0-report-v1"


class ResearchSeedM0Error(ValueError):
    """A seed bundle cannot pass the current read-only M0 gates."""


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ResearchSeedM0Error(f"cannot read JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ResearchSeedM0Error(f"JSON object required: {path}")
    return value


def _current_source_identity() -> dict[str, Any]:
    repo = Path(__file__).resolve().parents[3]
    try:
        head = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"],
            check=True, capture_output=True, text=True, timeout=60).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain=v1",
             "--untracked-files=all"],
            check=True, capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise ResearchSeedM0Error(
            "cannot verify the current M0 builder source identity") from exc
    return {"repo": str(repo), "git_head": head, "git_dirty": bool(status)}


def _validate_epoch(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
            "path", "epoch_digest", "git_head"}:
        raise ResearchSeedM0Error("research seed M0 epoch binding is invalid")
    raw_path = value.get("path")
    if not isinstance(raw_path, str) or not raw_path or not Path(raw_path).is_absolute():
        raise ResearchSeedM0Error("research seed M0 epoch path must be absolute")
    epoch_path = Path(raw_path).resolve()
    checked = verify_research_epoch(epoch_path)
    if (checked.get("valid") is not True
            or checked.get("research_evaluation_ready") is not True
            or checked.get("status") != "FROZEN"
            or checked.get("git_dirty") is not False
            or checked.get("blockers") != []
            or checked.get("epoch_digest") != value.get("epoch_digest")
            or checked.get("git_head") != value.get("git_head")):
        raise ResearchSeedM0Error("research seed M0 requires the bound clean epoch")
    current = _current_source_identity()
    if current["git_dirty"] is not False or current["git_head"] != checked["git_head"]:
        raise ResearchSeedM0Error(
            "current M0 builder source differs from the bound clean epoch")
    return {
        "path": str(epoch_path),
        "epoch_id": checked["epoch_id"],
        "epoch_digest": checked["epoch_digest"],
        "git_head": checked["git_head"],
        "repo": current["repo"],
        "toolchain_manifest_digest": checked["toolchain_manifest_digest"],
        "input_memory_bundle_digest": checked["memory_bundle_digest"],
    }


def _validate_spec(
    path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    payload = _json(path)
    if payload.get("schema") != SCHEMA:
        raise ResearchSeedM0Error("research seed M0 schema mismatch")
    campaign = payload.get("campaign_id")
    target_scope = payload.get("target_scope")
    timestamp = payload.get("materialized_at")
    if (not isinstance(campaign, str) or not campaign
            or target_scope != "flow_feasibility"
            or not isinstance(timestamp, str) or not timestamp):
        raise ResearchSeedM0Error("research seed M0 campaign contract is invalid")
    if (payload.get("model_call_limit") != 0
            or payload.get("online_memory_update") is not False
            or payload.get("production_authority") is not False):
        raise ResearchSeedM0Error("research seed M0 authority boundary changed")
    epoch = _validate_epoch(payload.get("epoch"))
    pairs = payload.get("pairs")
    if not isinstance(pairs, list) or len(pairs) != 2:
        raise ResearchSeedM0Error("research seed M0 requires exactly two pairs")
    checked: list[dict[str, Any]] = []
    lineages: set[str] = set()
    groups: set[str] = set()
    designs: set[str] = set()
    for item in pairs:
        if not isinstance(item, Mapping):
            raise ResearchSeedM0Error("research seed M0 pair must be an object")
        acquisition = dict(item)
        if acquisition.get("role") is not None:
            raise ResearchSeedM0Error("M0 pair spec must not preselect one arm role")
        result = verify_research_seed_acquisition(acquisition)
        if (result["expected_toolchain_manifest_digest"] !=
                epoch["toolchain_manifest_digest"]):
            raise ResearchSeedM0Error(
                "research seed pair and source epoch use different toolchains")
        for value, seen, label in (
            (result["lineage_id"], lineages, "lineage"),
            (result["source_group"], groups, "source group"),
            (result["design_id"], designs, "design"),
        ):
            if value in seen:
                raise ResearchSeedM0Error(f"research seed M0 {label} is duplicated")
            seen.add(value)
        checked.append(acquisition)
    return payload, checked, epoch


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    names = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'tehm_%' "
        "ORDER BY name").fetchall()
    return {str(row[0]): int(conn.execute(
        f'SELECT COUNT(*) FROM "{row[0]}"').fetchone()[0]) for row in names}


def build_research_seed_m0(*, spec: str | Path, output: str | Path) -> dict[str, Any]:
    """Rebuild, gate and export one immutable research-only M0 bundle."""
    spec_path = Path(spec).expanduser().resolve()
    destination = Path(output).expanduser().resolve()
    if destination.exists():
        raise ResearchSeedM0Error(f"refusing to overwrite research M0: {destination}")
    payload, pair_inputs, epoch = _validate_spec(spec_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    original_now = db.now_local
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        db.now_local = lambda: payload["materialized_at"]
        db.ensure_schema(conn)
        with tempfile.TemporaryDirectory(
                prefix=destination.name + ".tmp.", dir=destination.parent) as scratch_name:
            scratch = Path(scratch_name)
            artifact_root = scratch / "artifacts"
            artifact_root.mkdir()
            store = ArtifactStore(artifact_root)
            acquisitions: dict[str, dict[str, Any]] = {}
            pairs_by_lineage: dict[str, dict[str, str]] = defaultdict(dict)
            treatments: list[str] = []
            for base in pair_inputs:
                for role in ("control", "treatment"):
                    acquisition = {**base, "role": role}
                    record = build_research_seed_record(acquisition)
                    receipt = capture(
                        conn, store, record,
                        materialized_at=payload["materialized_at"],
                        dataset_campaign_id=payload["campaign_id"],
                        dataset_split="training", dataset_learner_eligible=True,
                    )
                    acquisitions[receipt.transition_id] = acquisition
                    pairs_by_lineage[record.lineage_id][role] = receipt.transition_id
                    if role == "treatment":
                        treatments.append(receipt.transition_id)
            if (len(acquisitions) != 4 or len(pairs_by_lineage) != 2
                    or any(set(item) != {"control", "treatment"}
                           for item in pairs_by_lineage.values())):
                raise ResearchSeedM0Error("research seed M0 capture denominator changed")
            acquisition_digest = _digest(acquisitions)
            with scoped_learning_replay(
                    conn, campaign_id=payload["campaign_id"],
                    acquisitions=acquisitions,
                    expected_digest=acquisition_digest):
                pair_receipts = []
                for lineage in sorted(pairs_by_lineage):
                    ids = pairs_by_lineage[lineage]
                    paired = build_intervention_pair(
                        conn, ids["control"], ids["treatment"],
                        campaign_id=payload["campaign_id"],
                        target_scope=payload["target_scope"])
                    if paired.validity_status != "VALID_CONTROLLED_PAIR":
                        raise ResearchSeedM0Error(
                            f"controlled intervention rejected for {lineage}")
                    pair_receipts.append(paired.to_dict())
                fragments = [build_transition_causal_fragment(
                    conn, transition_id, campaign_id=payload["campaign_id"])
                    for transition_id in sorted(acquisitions)]
                path = consolidate_causal_path(
                    conn, fragments, campaign_id=payload["campaign_id"],
                    status="shadow")
                replication = evaluate_replicated_effect(
                    conn, path.path_id, campaign_id=payload["campaign_id"])
                if not replication.eligible:
                    raise ResearchSeedM0Error(
                        f"replicated seed effect rejected: {replication.reason}")
                knowledge = build_knowledge_from_path(conn, path.path_id)
                register_knowledge(
                    conn, knowledge, target_scope=payload["target_scope"])
                authority = record_knowledge_authority(
                    conn, knowledge, target_scope=payload["target_scope"])
                if not authority.eligible:
                    raise ResearchSeedM0Error(
                        f"Knowledge authority rejected: {authority.reason}")
                set_knowledge_status(
                    conn, knowledge_id=knowledge.knowledge_id,
                    version=knowledge.version, target_scope=payload["target_scope"],
                    status="validated", authority_receipt=authority,
                    provenance={"purpose": "revision4_read_only_research_m0"})
                validated_knowledge = get_knowledge_by_object_id(
                    conn, knowledge.object_id, target_scope=payload["target_scope"])
                envelope = build_support_envelope_from_transitions(
                    conn, knowledge, treatments,
                    campaign_id=payload["campaign_id"])
                proposal = build_flow_asset_proposal(
                    conn, knowledge.object_id, campaign_id=payload["campaign_id"],
                    target_scope=payload["target_scope"])
                asset = register_asset_proposal(conn, proposal)
                for status in ("shadow", "candidate"):
                    set_asset_status(
                        conn, asset_id=asset.asset_id,
                        target_scope=payload["target_scope"], status=status)

                measurement = knowledge.intervention["measurement_contract"]
                query = MemoryQuery(query_plan={
                    "mechanism_family": knowledge.mechanism_family,
                    "compatibility_profile": knowledge.compatibility_profile,
                    "target_scope": payload["target_scope"],
                    "flow_design_id": "revision4-m0-consumption-preflight",
                    "flow_config": {"CORE_UTILIZATION": "95"},
                    "measurement_contract_digest": measurement["contract_digest"],
                })
                routing = route_memory(
                    conn, query, no_memory_budget=1, memory_budget=1,
                    persist_state=False, commit=False)
                selection = select_knowledge_grounded_assets(
                    conn, query, routing=routing)
                if selection.receipt.decision != "SELECT":
                    raise ResearchSeedM0Error(
                        "M0 asset selection rejected: "
                        f"{','.join(selection.receipt.abstain_reasons)}")
                candidate = build_structured_candidate(
                    query, routing, selection,
                    selection.metadata["runtime_binding"])
                resolved = resolve_current_state(
                    conn, {"mechanism_family": knowledge.mechanism_family,
                           "target_scope": payload["target_scope"]},
                    mode="shadow", persist=False, commit=False)

            acquisition_path = scratch / "parent-acquisitions.json"
            acquisition_payload = {
                "schema": "tehm-r4-research-seed-parent-acquisitions-v1",
                "campaign_id": payload["campaign_id"],
                "acquisitions": acquisitions, "digest": acquisition_digest,
            }
            acquisition_path.write_text(
                json.dumps(acquisition_payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8")
            report = {
                "schema": REPORT_SCHEMA, "spec_digest": _digest(payload),
                "campaign_id": payload["campaign_id"],
                "target_scope": payload["target_scope"],
                "research_epoch": epoch,
                "parent_acquisition_digest": acquisition_digest,
                "transition_ids": sorted(acquisitions),
                "lineages": sorted(pairs_by_lineage),
                "source_groups": sorted(item["source_group"] for item in pair_inputs),
                "controlled_pairs": pair_receipts,
                "causal_path_id": path.path_id,
                "replication": replication.to_dict(),
                "knowledge": validated_knowledge.to_dict(),
                "knowledge_authority": authority.to_dict(),
                "support_envelope": envelope.to_dict(),
                "asset": asset.to_dict(),
                "asset_status": "candidate",
                "consumption_preflight": {
                    "routing": routing.to_dict(),
                    "selection": selection.receipt.to_dict(),
                    "candidate": candidate.to_dict(),
                },
                "resolved_shadow_state": resolved.to_dict(),
                "table_counts": _table_counts(conn),
                "m0_status": "BUILT_READ_ONLY_RESEARCH",
                "scoped_replay_required": True,
                "online_memory_update": False,
                "production_authority": False,
                "model_calls": 0, "model_tokens": 0,
                "claim_boundary": (
                    "Constructed flow-feasibility training memory only; not natural "
                    "failure, strict signoff, S1 effect, S2 agent, or production evidence."),
            }
            report["report_digest"] = _digest(report)
            report_path = scratch / "m0-build-report.json"
            report_path.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8")
            db_path = scratch / "tehm.sqlite"
            file_conn = sqlite3.connect(str(db_path))
            try:
                conn.backup(file_conn)
                file_conn.commit()
            finally:
                file_conn.close()
            db_path.chmod(0o444)
            bundle_tmp = scratch / "bundle"
            manifest = export_bundle(
                output=bundle_tmp, db_path=db_path, artifact_root=artifact_root,
                evidence_files=(
                    (spec_path, "research/preregistration.json"),
                    (acquisition_path, "research/parent-acquisitions.json"),
                    (report_path, "research/m0-build-report.json"),
                    (Path(epoch["path"]) / "research-epoch.json",
                     "research/source-epoch.json"),
                    (Path(epoch["path"]) / "artifact-manifest.json",
                     "research/source-epoch-artifact-manifest.json"),
                ),
                metadata={
                    "purpose": "Revision4 read-only research M0",
                    "campaign_id": payload["campaign_id"],
                    "target_scope": payload["target_scope"],
                    "production_authority": False,
                    "online_memory_update": False,
                    "scoped_replay_required": True,
                })
            checked_bundle = verify_bundle(bundle_tmp)
            if not checked_bundle.get("ok"):
                raise ResearchSeedM0Error(
                    f"research M0 bundle verification failed: {checked_bundle.get('detail')}")
            os.replace(bundle_tmp, destination)
            return {
                **report, "bundle_path": str(destination),
                "bundle_digest": manifest["bundle_digest"],
                "manifest_digest": manifest["manifest_digest"],
                "bundle_verification": checked_bundle["detail"],
            }
    finally:
        conn.close()
        db.now_local = original_now


__all__ = ["ResearchSeedM0Error", "build_research_seed_m0"]
