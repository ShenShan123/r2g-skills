"""Build and cold-verify generation-3 R5 RTL TRAIN M−/M+/Mremove bundles.

This is researcher-assisted static Memory construction, not autonomous online
evolution, held-out transfer, production promotion, or Mremove attribution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from contracts import MemoryQuery
from tehm import db
from tehm.adapters.research_r5_rtl_scoped_v3 import (
    CAMPAIGN, CASES, FAMILY, PROFILE, acquisition, build_record,
    verify_acquisition,
)
from tehm.artifact_store import ArtifactStore
from tehm.assets.authority import record_asset_authority, verify_asset_authority
from tehm.assets.r5_train_evidence_v5 import (
    CASE_ORDER, expected_rollback_binding,
)
from tehm.adapters import research_r5_rtl_scoped_v3 as train_v3
from tehm.adapters.research_r5_rtl_scoped_v3 import oracle_for as _oracle_v3
from tehm.assets.registry import get_asset, get_asset_status, set_asset_status
from tehm.assets.skid_binding_v5 import with_skid_payload_binding_v5
from tehm.assets.structural_binding import bind_rtl_asset_to_source
from tehm.assets.synthesis import build_rtl_asset_proposal, register_asset_proposal
from tehm.assets.validation import validate_rtl_rewrite_asset
from tehm.canonical.capture import capture
from tehm.causal.intervention import build_intervention_pair
from tehm.causal.path_builder import (
    build_transition_causal_fragment, consolidate_causal_path,
)
from tehm.causal.replication import evaluate_replicated_effect
from tehm.ids import stable_dumps
from tehm.knowledge import (
    build_knowledge_from_path, get_knowledge_by_object_id,
    get_knowledge_status, record_knowledge_authority, register_knowledge, set_knowledge_status,
    verify_knowledge_authority,
)
from tehm.retrieval.asset_selector import select_knowledge_grounded_assets
from tehm.retrieval.memory_router import route_memory
from tehm.rtl.skid_payload_action_v5 import payload_from_source_v5
from tehm.sync import export_bundle, verify_bundle
from tehm.verified_execution import require_verified_transition, scoped_learning_replay


SCHEMA = "tehm-r5-rtl-train-readonly-m0-gen3-spec-v1"
REPORT_SCHEMA = "tehm-r5-rtl-train-readonly-m0-gen3-report-v1"
ROOT = Path("/data1/zhangdy/RTL/RTL_testbench/_r5_pilot")
EPOCH_DIR = ROOT / "epochs"
MEMORY_DIR = ROOT / "memory"
ASSET_GATE = MEMORY_DIR / "asset-authority-v5-ram-r2.json"
KNOWLEDGE_GATE = ROOT / "dev/measurement-v3-train-ram-r1.json"


def _source(checked: dict, case: str) -> str:
    if checked.get("case_id") != case:
        raise ValueError("generation-3 TRAIN case/source mismatch")
    return train_v3._source(checked)


def _oracle(checked: dict, *, obligation: str):
    record = build_record(acquisition(checked["case_id"], "treatment"))
    return _oracle_v3(checked, record, obligation)


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _digest(value: object) -> str:
    return _sha(stable_dumps(value).encode())


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _repo() -> Path:
    return Path(__file__).resolve().parents[3]


def _software() -> dict:
    repo = _repo()
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True, timeout=20).stdout.strip()
    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain=v1",
                             "--untracked-files=all"], check=True,
                            capture_output=True, text=True, timeout=20).stdout
    if status:
        raise ValueError("R5 M0 requires a clean software epoch")
    return {"git_head": head, "builder_sha256": _sha(Path(__file__).read_bytes())}


def _spec() -> dict:
    for path in (ASSET_GATE, KNOWLEDGE_GATE):
        if not path.is_file():
            raise ValueError("R5 M0 frozen gate receipt missing: " + str(path))
        value = json.loads(path.read_bytes())
        if value.get("valid") is not True:
            raise ValueError("R5 M0 frozen gate receipt invalid: " + str(path))
    asset_gate = json.loads(ASSET_GATE.read_bytes())
    knowledge_gate = json.loads(KNOWLEDGE_GATE.read_bytes())
    if (asset_gate.get("role") != "RAM_ONLY_R5_V5_TRAIN_ASSET_AUTHORITY_NOT_M_PLUS" or
            asset_gate.get("asset_authority_eligible_in_ram") is not True or
            asset_gate.get("asset_promoted") is not False or
            asset_gate.get("memory_m_plus_constructed") is not False or
            asset_gate.get("heldout_transfer") is not False):
        raise ValueError("generation-3 v5 TRAIN Asset gate role drift")
    if (knowledge_gate.get("role") != "TRAIN_RAM_ONLY_NOT_M_PLUS_NOT_TRANSFER" or
            knowledge_gate.get("case_count") != 20 or
            knowledge_gate.get("memory_m_plus_constructed") is not False or
            knowledge_gate.get("target_instance_witness_validated") is not False or
            knowledge_gate.get("shared_contract_digest") !=
            train_v3._digest(train_v3.MEASUREMENT_CONTRACT)):
        raise ValueError("generation-3 TRAIN Knowledge gate role drift")
    if asset_gate.get("rollback_digest") != expected_rollback_binding()["receipt_digest"]:
        raise ValueError("generation-3 Asset gate rollback drift")
    return {
        "schema": SCHEMA, "role": "RESEARCHER_ASSISTED_TRAIN_READ_ONLY_M0",
        "campaign_id": CAMPAIGN, "target_scope": PROFILE,
        "materialized_at": datetime.now(timezone.utc).isoformat(),
        "software": _software(),
        "gate_receipts": {
            "asset_authority_preflight_sha256": _sha(ASSET_GATE.read_bytes()),
            "knowledge_preflight_sha256": _sha(KNOWLEDGE_GATE.read_bytes()),
        },
        "model_call_limit": 0, "online_memory_update": False,
        "production_authority": False, "heldout_transfer": False,
    }


def prepare(path: Path) -> dict:
    target = path.resolve(strict=False)
    if target.parent != EPOCH_DIR or target.exists() or target.is_symlink():
        raise ValueError("R5 M0 spec must be a new direct child of epochs")
    spec = _spec()
    _write_json(target, spec)
    return {"prepared": True, "spec": str(target),
            "spec_sha256": _sha(target.read_bytes()), "git_head": spec["software"]["git_head"]}


def _read_spec(path: Path) -> dict:
    source = path.resolve(strict=True)
    if source.parent != EPOCH_DIR or source.is_symlink():
        raise ValueError("R5 M0 spec is outside evaluator epoch directory")
    value = json.loads(source.read_bytes())
    if (value.get("schema") != SCHEMA or value.get("campaign_id") != CAMPAIGN or
            value.get("target_scope") != PROFILE or value.get("software") != _software() or
            value.get("gate_receipts") != {
                "asset_authority_preflight_sha256": _sha(ASSET_GATE.read_bytes()),
                "knowledge_preflight_sha256": _sha(KNOWLEDGE_GATE.read_bytes())} or
            value.get("model_call_limit") != 0 or
            value.get("online_memory_update") is not False or
            value.get("production_authority") is not False or
            value.get("heldout_transfer") is not False or
            not isinstance(value.get("materialized_at"), str)):
        raise ValueError("R5 M0 frozen spec or software epoch drift")
    return value


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'tehm_%' "
        "ORDER BY name").fetchall()
    return {str(row[0]): int(conn.execute(
        f'SELECT COUNT(*) FROM "{row[0]}"').fetchone()[0]) for row in rows}


def _semantic_rows(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Canonical TEHM rows for Mremove-vs-M− equivalence, not SQLite file bytes."""
    result = {}
    for name in _table_counts(conn):
        rows = conn.execute(f'SELECT * FROM "{name}"').fetchall()
        result[name] = sorted(stable_dumps(dict(row)) for row in rows)
    return result


def _remove_delta(plus: sqlite3.Connection, minus: sqlite3.Connection,
                  removed: sqlite3.Connection) -> dict[str, int]:
    """Remove the complete TRAIN-only dependency closure on a disposable copy."""
    plus_counts, minus_counts = _table_counts(plus), _table_counts(minus)
    if plus_counts.keys() != minus_counts.keys():
        raise ValueError("R5 Mremove schema mismatch")
    plus.backup(removed)
    closure = {}
    for name in sorted(plus_counts):
        delta = plus_counts[name] - minus_counts[name]
        if delta < 0 or (delta > 0 and minus_counts[name] != 0):
            raise ValueError("R5 Mremove has mixed shared/delta rows: " + name)
        if delta:
            removed.execute(f'DELETE FROM "{name}"')
            closure[name] = delta
    if not closure or _semantic_rows(removed) != _semantic_rows(minus):
        raise ValueError("R5 Mremove failed M− semantic equivalence")
    # SQLite backup of a connection with an open write transaction can wait forever.
    removed.commit()
    return closure


def _backup(conn: sqlite3.Connection, path: Path) -> None:
    output = sqlite3.connect(str(path))
    try:
        conn.backup(output)
        output.commit()
    finally:
        output.close()
    path.chmod(0o444)


def _query(knowledge) -> MemoryQuery:
    measurement = knowledge.intervention["measurement_contract"]
    return MemoryQuery(query_plan={
        "mechanism_family": knowledge.mechanism_family,
        "compatibility_profile": knowledge.compatibility_profile,
        "target_scope": PROFILE,
        "transformation_family": "skid_payload_restore_shadow_v5",
        "measurement_contract_digest": measurement["contract_digest"],
        "design_id": "axis_register_train_consumption_preflight",
    })


def _build_plus(conn: sqlite3.Connection, store: ArtifactStore,
                materialized_at: str) -> tuple[dict, dict]:
    checked = {case: verify_acquisition(acquisition(case, "treatment"))
               for case in CASE_ORDER}
    acquisitions: dict[str, dict] = {}
    ids: dict[str, dict[str, str]] = {}
    for case in CASE_ORDER:
        ids[case] = {}
        for role in ("control", "treatment"):
            raw = acquisition(case, role)
            record = build_record(raw)
            receipt = capture(
                conn, store, record, materialized_at=materialized_at,
                dataset_campaign_id=CAMPAIGN, dataset_split="training",
                dataset_learner_eligible=True)
            acquisitions[receipt.transition_id] = raw
            ids[case][role] = receipt.transition_id
    acq_digest = _digest(acquisitions)
    with scoped_learning_replay(
            conn, campaign_id=CAMPAIGN, acquisitions=acquisitions,
            expected_digest=acq_digest):
        for transition_id in acquisitions:
            require_verified_transition(conn, transition_id)
        pairs = [build_intervention_pair(
            conn, ids[case]["control"], ids[case]["treatment"],
            campaign_id=CAMPAIGN, target_scope=PROFILE).to_dict()
            for case in CASE_ORDER]
        if any(pair["validity_status"] != "VALID_CONTROLLED_PAIR" or
               pair["evidence_level"] != "L2_CONTROLLED_INTERVENTION"
               for pair in pairs):
            raise ValueError("R5 M0 controlled TRAIN pair rejected")
        fragments = [build_transition_causal_fragment(
            conn, tid, campaign_id=CAMPAIGN) for tid in sorted(acquisitions)]
        path = consolidate_causal_path(
            conn, fragments, campaign_id=CAMPAIGN, status="shadow")
        replication = evaluate_replicated_effect(
            conn, path.path_id, campaign_id=CAMPAIGN)
        if not replication.eligible or replication.evidence_level != "L3_REPLICATED_EFFECT":
            raise ValueError("R5 M0 replicated TRAIN effect rejected")
        knowledge = build_knowledge_from_path(conn, path.path_id)
        register_knowledge(conn, knowledge, target_scope=PROFILE)
        k_authority = record_knowledge_authority(conn, knowledge, target_scope=PROFILE)
        if not k_authority.eligible or not verify_knowledge_authority(
                conn, k_authority)["eligible"]:
            raise ValueError("R5 M0 Knowledge authority rejected")
        set_knowledge_status(
            conn, knowledge_id=knowledge.knowledge_id,
            version=knowledge.version, target_scope=PROFILE,
            status="validated", authority_receipt=k_authority,
            provenance={"purpose": "revision5_read_only_research_m0"})
        validated_knowledge = get_knowledge_by_object_id(
            conn, knowledge.object_id, target_scope=PROFILE)
        first = checked[CASE_ORDER[0]]
        source = _source(first, CASE_ORDER[0])
        proposal = build_rtl_asset_proposal(
            {}, name="r5-skid-v5-train-validation-draft",
            transformation_family="skid_payload_restore_shadow_v5",
            action_payload_template=payload_from_source_v5(source, first["public_context"]),
            compatibility_profile=PROFILE,
            verifier_obligations=("TRAIN target", "TRAIN preservation"),
            creator="researcher_assisted_reused_dev_train_generation_3",
            mechanism_knowledge_ids=[knowledge.object_id])
        proposal = with_skid_payload_binding_v5(proposal, source, first["public_context"])
        registration = register_asset_proposal(conn, proposal, target_scope=PROFILE)
        asset = get_asset(conn, registration.asset_id)
        if asset is None:
            raise ValueError("R5 M0 Asset registration missing")
        validation_rows, binding_rows = [], []
        for case in CASE_ORDER:
            item = checked[case]
            before = _source(item, case)
            bound = bind_rtl_asset_to_source(
                asset, before, design_id=item["design"],
                public_context=item["public_context"])
            validation = validate_rtl_rewrite_asset(
                bound, before, verifier=_oracle(item, obligation="target"),
                regression_verifier=_oracle(item, obligation="preservation")).to_dict()
            metadata = {"split": "training", "lineage_id": item["repository"],
                        "source_id": case}
            validation_rows.append({"receipt": validation, **metadata})
            binding_rows.append({"asset": bound, **metadata})
        rollback = expected_rollback_binding()
        a_authority = record_asset_authority(
            conn, asset_id=registration.asset_id, target_scope=PROFILE,
            validation_receipts=validation_rows, bindings=binding_rows,
            rollback_receipt={"receipt": rollback, "split": "ab",
                              "source_id": "asset-rollback-v5-r2"},
            min_lineages=2)
        if not a_authority.eligible or not verify_asset_authority(
                conn, a_authority)["eligible"]:
            raise ValueError("R5 M0 Asset strict authority rejected")
        for status in ("shadow", "candidate"):
            set_asset_status(conn, asset_id=registration.asset_id,
                             target_scope=PROFILE, status=status)
        query = _query(knowledge)
        routing = route_memory(
            conn, query, no_memory_budget=1, memory_budget=1,
            persist_state=False, commit=False)
        selection = select_knowledge_grounded_assets(
            conn, query, routing=routing, rtl_source_text=source,
            design_id=first["design"], rtl_public_context=first["public_context"])
        if (routing.decision != "CONSIDER" or
                selection.receipt.decision != "SELECT"):
            raise ValueError("generation-3 M+ TRAIN consumption did not select")
        report = {
            "schema": REPORT_SCHEMA, "role": "TRAIN_READ_ONLY_M_PLUS_NOT_TRANSFER",
            "campaign_id": CAMPAIGN, "target_scope": PROFILE,
            "transition_ids": {case: ids[case] for case in CASE_ORDER},
            "acquisition_digest": acq_digest,
            "controlled_pairs": pairs,
            "causal_path_id": path.path_id,
            "replication": replication.to_dict(),
            "knowledge_object_id": knowledge.object_id,
            "knowledge": validated_knowledge.to_dict(),
            "knowledge_authority": k_authority.to_dict(),
            "asset_id": registration.asset_id,
            "asset_status": get_asset_status(
                conn, asset_id=registration.asset_id, target_scope=PROFILE)["status"],
            "asset_authority": a_authority.to_dict(),
            "train_consumption_preflight": {
                "query_plan": dict(query.query_plan),
                "routing": routing.to_dict(),
                "selection": selection.receipt.to_dict(),
            },
            "model_calls": 0, "model_tokens": 0,
            "online_memory_update": False, "production_authority": False,
            "heldout_transfer": False,
        }
    report["table_counts"] = _table_counts(conn)
    report["report_digest"] = _digest(report)
    return report, acquisitions


def build(*, spec: Path, output: Path) -> dict:
    spec_path = spec.resolve(strict=True)
    payload = _read_spec(spec_path)
    target = output.resolve(strict=False)
    if target.parent != MEMORY_DIR or target.exists() or target.is_symlink():
        raise ValueError("R5 M0 output must be a new direct child of pilot memory")
    original_now = db.now_local
    minus = sqlite3.connect(":memory:")
    plus = sqlite3.connect(":memory:")
    removed = sqlite3.connect(":memory:")
    minus.row_factory = plus.row_factory = removed.row_factory = sqlite3.Row
    try:
        db.now_local = lambda: payload["materialized_at"]
        db.ensure_schema(minus)
        db.ensure_schema(plus)
        with tempfile.TemporaryDirectory(prefix=target.name + ".tmp.",
                                         dir=target.parent) as scratch_name:
            scratch = Path(scratch_name)
            plus_artifacts = scratch / "plus-artifacts"
            minus_artifacts = scratch / "minus-artifacts"
            removed_artifacts = scratch / "mremove-artifacts"
            plus_artifacts.mkdir()
            minus_artifacts.mkdir()
            removed_artifacts.mkdir()
            store = ArtifactStore(plus_artifacts)
            report, acquisitions = _build_plus(plus, store, payload["materialized_at"])
            train_query = MemoryQuery(
                query_plan=report["train_consumption_preflight"]["query_plan"])
            minus_route = route_memory(
                minus, train_query, no_memory_budget=1, memory_budget=1,
                persist_state=False, commit=False)
            minus_selection = select_knowledge_grounded_assets(
                minus, train_query, routing=minus_route)
            report["m_minus_train_consumption_preflight"] = {
                "routing": minus_route.to_dict(),
                "selection": minus_selection.receipt.to_dict(),
            }
            if minus_selection.receipt.decision == "SELECT":
                raise ValueError("empty M− unexpectedly selected an Asset")
            minus_counts = _table_counts(minus)
            delta = {key: report["table_counts"][key] - minus_counts[key]
                     for key in sorted(minus_counts)}
            if any(value < 0 for value in delta.values()) or not any(delta.values()):
                raise ValueError("R5 M0 delta is empty or invalid")
            closure = _remove_delta(plus, minus, removed)
            removed_route = route_memory(
                removed, train_query, no_memory_budget=1, memory_budget=1,
                persist_state=False, commit=False)
            removed_selection = select_knowledge_grounded_assets(
                removed, train_query, routing=removed_route)
            if (removed_route.to_dict() != minus_route.to_dict() or
                    removed_selection.receipt.to_dict() != minus_selection.receipt.to_dict()):
                raise ValueError("R5 Mremove routing/selection not equivalent to M−")
            report["mremove_removed_dependency_tables"] = closure
            report["mremove_table_counts"] = _table_counts(removed)
            report["mremove_train_consumption_preflight"] = {
                "routing": removed_route.to_dict(),
                "selection": removed_selection.receipt.to_dict(),
            }
            report["spec_sha256"] = _sha(spec_path.read_bytes())
            report["spec_filename"] = spec_path.name
            report["software"] = payload["software"]
            report["m_minus_table_counts"] = minus_counts
            report["delta_table_counts"] = delta
            report["report_digest"] = _digest({k: v for k, v in report.items()
                                                if k != "report_digest"})
            research = scratch / "research"
            research.mkdir()
            _write_json(research / "m0-build-report.json", report)
            _write_json(research / "parent-acquisitions.json", {
                "schema": "tehm-r5-rtl-train-gen3-parent-acquisitions-v1",
                "campaign_id": CAMPAIGN, "acquisitions": acquisitions,
                "digest": _digest(acquisitions)})
            _write_json(research / "delta-manifest.json", {
                "schema": "tehm-r5-rtl-train-m0-gen3-delta-v1",
                "source_view": "M_MINUS", "target_view": "M_PLUS",
                "source_knowledge_object_ids": [], "added_knowledge_object_ids": [
                    report["knowledge_object_id"]],
                "source_asset_ids": [], "added_asset_ids": [report["asset_id"]],
                "source_transition_ids": [],
                "added_transition_ids": sorted(acquisitions),
                "added_causal_path_ids": [report["causal_path_id"]],
                "table_row_deltas": delta,
                "removal_view": "MREMOVE",
                "removed_dependency_tables": closure,
                "mremove_equivalent_to_m_minus": True,
                "TRAIN_ONLY": True, "online_evolution": False})
            report["parent_acquisitions_sha256"] = _sha(
                (research / "parent-acquisitions.json").read_bytes())
            report["delta_manifest_sha256"] = _sha(
                (research / "delta-manifest.json").read_bytes())
            report["report_digest"] = _digest({k: v for k, v in report.items()
                                                if k != "report_digest"})
            _write_json(research / "m0-build-report.json", report)
            minus_db = scratch / "m-minus.sqlite"
            plus_db = scratch / "m-plus.sqlite"
            removed_db = scratch / "mremove.sqlite"
            _backup(minus, minus_db)
            _backup(plus, plus_db)
            _backup(removed, removed_db)
            for view, db_path, artifacts in (
                    ("m-minus", minus_db, minus_artifacts),
                    ("m-plus", plus_db, plus_artifacts),
                    ("mremove", removed_db, removed_artifacts)):
                export_bundle(
                    output=scratch / "bundles" / view, db_path=db_path,
                    artifact_root=artifacts,
                    evidence_files=[(spec_path, "research/source-epoch.json")]
                    + ([(research / "parent-acquisitions.json",
                         "research/parent-acquisitions.json")]
                       if view == "m-plus" else [])
                    + ([(research / "delta-manifest.json",
                         "research/delta-manifest.json")]
                       if view == "mremove" else []),
                    metadata={"purpose": "Revision5 bounded read-only RTL TRAIN M0",
                              "view": view.upper().replace("-", "_"),
                              "campaign_id": CAMPAIGN, "target_scope": PROFILE,
                              "software_git_head": payload["software"]["git_head"],
                              "production_authority": False,
                              "online_memory_update": False,
                              "scoped_replay_required": view == "m-plus"})
            for view in ("m-minus", "m-plus", "mremove"):
                checked = verify_bundle(scratch / "bundles" / view)
                if not checked["ok"]:
                    raise ValueError("R5 M0 bundle failed verification: " + checked["detail"])
            os.replace(scratch, target)
        return {"valid": True, "output": str(target),
                "report_digest": report["report_digest"],
                "knowledge_object_id": report["knowledge_object_id"],
                "asset_id": report["asset_id"],
                "m_minus_bundle": str(target / "bundles" / "m-minus"),
                "m_plus_bundle": str(target / "bundles" / "m-plus"),
                "mremove_bundle": str(target / "bundles" / "mremove"),
                "heldout_transfer": False}
    finally:
        db.now_local = original_now
        minus.close()
        plus.close()
        removed.close()


def verify(output: Path) -> dict:
    root = output.resolve(strict=True)
    if root.parent != MEMORY_DIR or root.is_symlink():
        raise ValueError("R5 M0 output outside pilot memory")
    report = json.loads((root / "research" / "m0-build-report.json").read_bytes())
    spec_filename = report.get("spec_filename")
    if (type(spec_filename) is not str or not spec_filename.endswith(".json") or
            Path(spec_filename).name != spec_filename):
        raise ValueError("R5 M0 report epoch filename invalid")
    spec_path = EPOCH_DIR / spec_filename
    spec = _read_spec(spec_path)
    if (report.get("schema") != REPORT_SCHEMA or
            report.get("spec_sha256") != _sha(spec_path.read_bytes()) or
            report.get("software") != spec["software"] or
            report.get("report_digest") != _digest({k: v for k, v in report.items()
                                                    if k != "report_digest"})):
        raise ValueError("R5 M0 report/epoch drift")
    research = root / "research"
    acquisitions_path = research / "parent-acquisitions.json"
    delta_path = research / "delta-manifest.json"
    if (report.get("parent_acquisitions_sha256") != _sha(acquisitions_path.read_bytes()) or
            report.get("delta_manifest_sha256") != _sha(delta_path.read_bytes())):
        raise ValueError("R5 M0 acquisitions or delta sidecar hash drift")
    delta_doc = json.loads(delta_path.read_bytes())
    if (delta_doc.get("schema") != "tehm-r5-rtl-train-m0-gen3-delta-v1" or
            delta_doc.get("TRAIN_ONLY") is not True or
            delta_doc.get("online_evolution") is not False):
        raise ValueError("R5 M0 delta role drift")
    manifests = {}
    for view in ("m-minus", "m-plus", "mremove"):
        checked = verify_bundle(root / "bundles" / view)
        if not checked["ok"]:
            raise ValueError("R5 M0 bundle invalid: " + checked["detail"])
        manifests[view] = checked["manifest"]
    acquisitions_doc = json.loads(acquisitions_path.read_bytes())
    acquisitions = acquisitions_doc["acquisitions"]
    if (acquisitions_doc["campaign_id"] != CAMPAIGN or
            acquisitions_doc["digest"] != _digest(acquisitions)):
        raise ValueError("R5 M0 parent acquisition digest drift")
    def reload(view: str) -> sqlite3.Connection:
        source = sqlite3.connect(
            "file:" + str(root / "bundles" / view / "closed_loop" / "tehm.sqlite")
            + "?mode=ro", uri=True)
        ram = sqlite3.connect(":memory:")
        ram.row_factory = sqlite3.Row
        try:
            source.backup(ram)
        finally:
            source.close()
        return ram
    minus = reload("m-minus")
    plus = reload("m-plus")
    removed = reload("mremove")
    recomputed = sqlite3.connect(":memory:")
    recomputed.row_factory = sqlite3.Row
    try:
        if (_table_counts(minus) != report["m_minus_table_counts"] or
                _table_counts(plus) != report["table_counts"] or
                _table_counts(removed) != report["mremove_table_counts"]):
            raise ValueError("R5 M0 cold-loaded table counts drift")
        loaded_delta = {key: report["table_counts"][key] -
                        report["m_minus_table_counts"][key]
                        for key in sorted(report["m_minus_table_counts"])}
        expected_delta = {
            "schema": "tehm-r5-rtl-train-m0-gen3-delta-v1",
            "source_view": "M_MINUS", "target_view": "M_PLUS",
            "source_knowledge_object_ids": [],
            "added_knowledge_object_ids": [report["knowledge_object_id"]],
            "source_asset_ids": [], "added_asset_ids": [report["asset_id"]],
            "source_transition_ids": [],
            "added_transition_ids": sorted(acquisitions),
            "added_causal_path_ids": [report["causal_path_id"]],
            "table_row_deltas": loaded_delta,
            "removal_view": "MREMOVE",
            "removed_dependency_tables": report["mremove_removed_dependency_tables"],
            "mremove_equivalent_to_m_minus": True,
            "TRAIN_ONLY": True, "online_evolution": False}
        if delta_doc != expected_delta or loaded_delta != report["delta_table_counts"]:
            raise ValueError("R5 M0 cold-loaded dependency delta drift")
        closure = _remove_delta(plus, minus, recomputed)
        if (closure != report["mremove_removed_dependency_tables"] or
                _semantic_rows(removed) != _semantic_rows(minus) or
                _semantic_rows(removed) != _semantic_rows(recomputed)):
            raise ValueError("R5 Mremove cold-loaded rebuild not M− equivalent")
        query = MemoryQuery(
            query_plan=report["train_consumption_preflight"]["query_plan"])
        minus_route = route_memory(
            minus, query, no_memory_budget=1, memory_budget=1,
            persist_state=False, commit=False)
        minus_selection = select_knowledge_grounded_assets(
            minus, query, routing=minus_route)
        if (minus_route.to_dict() != report["m_minus_train_consumption_preflight"]["routing"] or
                minus_selection.receipt.to_dict() !=
                report["m_minus_train_consumption_preflight"]["selection"]):
            raise ValueError("R5 M0 M− cold-loaded routing or selection drift")

        removed_route = route_memory(
            removed, query, no_memory_budget=1, memory_budget=1,
            persist_state=False, commit=False)
        removed_selection = select_knowledge_grounded_assets(
            removed, query, routing=removed_route)
        if (removed_route.to_dict() != minus_route.to_dict() or
                removed_selection.receipt.to_dict() != minus_selection.receipt.to_dict() or
                removed_route.to_dict() !=
                report["mremove_train_consumption_preflight"]["routing"] or
                removed_selection.receipt.to_dict() !=
                report["mremove_train_consumption_preflight"]["selection"]):
            raise ValueError("R5 Mremove cold-loaded routing/selection drift")

        with scoped_learning_replay(
                plus, campaign_id=CAMPAIGN, acquisitions=acquisitions,
                expected_digest=acquisitions_doc["digest"]):
            for transition_id in acquisitions:
                require_verified_transition(plus, transition_id)
            # The eligible receipt is bound to candidate status version 1.
            # Explicit validation consumes it and advances the status to v2;
            # replay the historical receipt on this disposable RAM snapshot.
            knowledge = get_knowledge_by_object_id(
                plus, report["knowledge_object_id"], target_scope=PROFILE)
            status = get_knowledge_status(
                plus, knowledge_id=knowledge.knowledge_id,
                version=knowledge.version, target_scope=PROFILE)
            rebuilt = build_knowledge_from_path(plus, report["causal_path_id"])
            if (knowledge.status != "validated" or status["status_version"] != 2 or
                    status["provenance"] != {"purpose": "revision5_read_only_research_m0"} or
                    report["knowledge_authority"].get("status_version") != 1 or
                    rebuilt.status != "candidate" or
                    rebuilt.version != knowledge.version + 1 or
                    replace(rebuilt, version=knowledge.version).content_digest !=
                    knowledge.content_digest):
                raise ValueError("R5 M0 Knowledge validation lifecycle drift")
            plus.execute("SAVEPOINT r5_m0_knowledge_authority_replay")
            try:
                plus.execute(
                    """UPDATE tehm_mechanism_knowledge_status
                          SET status='candidate', status_version=1
                        WHERE knowledge_id=? AND version=? AND target_scope=?""",
                    (knowledge.knowledge_id, knowledge.version, PROFILE))
                k_check = verify_knowledge_authority(
                    plus, report["knowledge_authority"])
            finally:
                plus.execute("ROLLBACK TO SAVEPOINT r5_m0_knowledge_authority_replay")
                plus.execute("RELEASE SAVEPOINT r5_m0_knowledge_authority_replay")
            if not k_check["eligible"]:
                raise ValueError("R5 M0 cold-loaded historical Knowledge authority rejected: "
                                 + str(k_check["reasons"]))
            if not verify_asset_authority(
                    plus, report["asset_authority"])["eligible"]:
                raise ValueError("R5 M0 cold-loaded Asset authority rejected")
            knowledge = get_knowledge_by_object_id(
                plus, report["knowledge_object_id"], target_scope=PROFILE)
            asset_status = get_asset_status(
                plus, asset_id=report["asset_id"], target_scope=PROFILE)
            if knowledge is None or knowledge.status != "validated" or asset_status["status"] != "candidate":
                raise ValueError("R5 M0 cold-loaded Knowledge/Asset status drift")
            first = verify_acquisition(acquisition(CASE_ORDER[0], "treatment"))
            source = _source(first, CASE_ORDER[0])
            plus_route = route_memory(
                plus, query, no_memory_budget=1, memory_budget=1,
                persist_state=False, commit=False)
            plus_selection = select_knowledge_grounded_assets(
                plus, query, routing=plus_route, rtl_source_text=source,
                design_id=first["design"], rtl_public_context=first["public_context"])
            if (plus_route.to_dict() != report["train_consumption_preflight"]["routing"] or
                    plus_selection.receipt.to_dict() !=
                    report["train_consumption_preflight"]["selection"]):
                raise ValueError("R5 M0 M+ cold-loaded routing or selection drift")


        return {"valid": True, "output": str(root),
                "report_digest": report["report_digest"],
                "m_minus_bundle_digest": manifests["m-minus"]["bundle_digest"],
                "m_plus_bundle_digest": manifests["m-plus"]["bundle_digest"],
                "mremove_bundle_digest": manifests["mremove"]["bundle_digest"],
                "knowledge_object_id": report["knowledge_object_id"],
                "asset_id": report["asset_id"],
                "m_minus_route": minus_route.decision,
                "m_minus_selection": minus_selection.receipt.decision,
                "m_plus_route": plus_route.decision,
                "m_plus_selection": plus_selection.receipt.decision,
                "mremove_route": removed_route.decision,
                "mremove_selection": removed_selection.receipt.decision,
                "m_minus_rows": sum(_table_counts(minus).values()),
                "m_plus_rows": sum(_table_counts(plus).values()),
                "mremove_rows": sum(_table_counts(removed).values()),
                "heldout_transfer": False,
                "mremove_constructed": True}
    finally:
        minus.close()
        plus.close()
        removed.close()
        recomputed.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare").add_argument("--output", type=Path, required=True)
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--spec", type=Path, required=True)
    build_parser.add_argument("--output", type=Path, required=True)
    sub.add_parser("verify").add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = (prepare(args.output) if args.command == "prepare" else
              build(spec=args.spec, output=args.output) if args.command == "build" else
              verify(args.output))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("prepared") or result.get("valid") else 1


if __name__ == "__main__":
    raise SystemExit(main())
