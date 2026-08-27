"""ORFS staging-to-causal-shadow integration.

The input is an already verified external/staging database.  This adapter
works on a SQLite backup copy, upgrades that copy to v4, and emits only
shadow causal fragments/paths.  It never mutates canonical memory or grants
runtime authority.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import copy
from dataclasses import replace
from collections import defaultdict
from pathlib import Path

from tehm import db
from tehm.artifact_store import ArtifactStore
from tehm.adapters.orfs_pair import build_orfs_pair_record
from tehm.canonical.capture import capture

from .intervention import build_intervention_pair
from .path_builder import build_transition_causal_fragment, consolidate_causal_path
from .replication import evaluate_replicated_effect
from .authority import evaluate_causal_rule_evidence

ORFS_CAUSAL_SHADOW_VERSION = "orfs-causal-shadow-v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _backup_database(source: Path, destination: Path) -> None:
    """Copy a possibly-WAL source without touching its journal files."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    source_conn = sqlite3.connect(
        f"file:{source.resolve()}?mode=ro", uri=True, timeout=30)
    destination_conn = sqlite3.connect(str(destination), timeout=30)
    try:
        source_conn.backup(destination_conn)
        destination_conn.commit()
    finally:
        destination_conn.close()
        source_conn.close()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def build_orfs_causal_shadow(
    staging_db: Path | str,
    *,
    campaign_id: str,
    output_dir: Path | str,
    split: str = "training",
) -> dict:
    """Build a v4 causal shadow report from an existing ORFS staging DB."""
    source = Path(staging_db).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"staging database not found: {source}")
    if not campaign_id:
        raise ValueError("campaign_id is required")
    if split not in {"training", "calibration", "heldout", "ab"}:
        raise ValueError(f"invalid dataset split: {split!r}")

    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    derived_db = output / "tehm.sqlite"
    _backup_database(source, derived_db)

    source_sha = _sha256(source)
    before_source = sqlite3.connect(
        f"file:{source}?mode=ro", uri=True, timeout=30)
    before_source.row_factory = sqlite3.Row
    source_counts = {
        "transitions": int(before_source.execute(
            "SELECT COUNT(*) FROM tehm_transitions").fetchone()[0]),
        "causal_nodes": (int(before_source.execute(
            "SELECT COUNT(*) FROM tehm_causal_nodes").fetchone()[0])
                         if _table_exists(before_source, "tehm_causal_nodes") else 0),
        "causal_paths": (int(before_source.execute(
            "SELECT COUNT(*) FROM tehm_causal_paths").fetchone()[0])
                         if _table_exists(before_source, "tehm_causal_paths") else 0),
    }
    before_source.close()

    conn = db.connect(derived_db)
    db.ensure_schema(conn)
    rows = conn.execute(
        """SELECT t.transition_id
             FROM tehm_transitions t
             JOIN tehm_dataset_membership dm ON dm.transition_id=t.transition_id
            WHERE dm.campaign_id=? AND dm.split=? AND dm.learner_eligible=1
            ORDER BY t.transition_id""",
        (campaign_id, split),
    ).fetchall()
    if not rows:
        conn.close()
        raise ValueError(
            f"no learner-eligible {split} transitions for campaign {campaign_id!r}")

    fragments = [
        build_transition_causal_fragment(
            conn, row["transition_id"], campaign_id=campaign_id)
        for row in rows
    ]
    groups = defaultdict(list)
    for fragment in fragments:
        groups[(fragment.mechanism_family,
                fragment.compatibility_profile)].append(fragment)

    paths = []
    replications = []
    rule_evidence = []
    for _, group in sorted(groups.items(), key=lambda item: str(item[0])):
        path = consolidate_causal_path(
            conn, group, campaign_id=campaign_id, status="shadow")
        paths.append(path)
        replications.append(evaluate_replicated_effect(
            conn, path.path_id, campaign_id=campaign_id).to_dict())
        rule_evidence.append(evaluate_causal_rule_evidence(
            conn, path.path_id, campaign_id=campaign_id,
            required_level="L2_CONTROLLED_INTERVENTION",
            min_lineages=2).to_dict())

    after_counts = {
        "transitions": int(conn.execute(
            "SELECT COUNT(*) FROM tehm_transitions").fetchone()[0]),
        "causal_nodes": int(conn.execute(
            "SELECT COUNT(*) FROM tehm_causal_nodes").fetchone()[0]),
        "causal_edges": int(conn.execute(
            "SELECT COUNT(*) FROM tehm_causal_edges").fetchone()[0]),
        "causal_paths": int(conn.execute(
            "SELECT COUNT(*) FROM tehm_causal_paths").fetchone()[0]),
    }
    schema_version = conn.execute(
        "SELECT value FROM tehm_meta WHERE key='schema_version'").fetchone()[0]
    conn.close()

    report = {
        "version": ORFS_CAUSAL_SHADOW_VERSION,
        "campaign_id": campaign_id,
        "split": split,
        "source_staging_db": str(source),
        "source_staging_sha256": source_sha,
        "derived_db": str(derived_db),
        "derived_db_sha256": _sha256(derived_db),
        "schema_version": schema_version,
        "canonical_memory_mutation": "none",
        "source_counts": source_counts,
        "derived_counts": after_counts,
        "transition_count_preserved": (
            source_counts["transitions"] == after_counts["transitions"]),
        "fragments": [fragment.to_dict() for fragment in fragments],
        "paths": [path.to_dict() for path in paths],
        "replication": replications,
        "rule_evidence": rule_evidence,
        "promotion_eligible": False,
        "authority_note": (
            "ordinary ORFS observations produce L1 shadow evidence; "
            "controlled-pair and independent authority receipts are required "
            "before any L2/L3 or runtime promotion"),
    }
    report_path = output / "causal_shadow_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def _control_record(treatment):
    """Turn the same completed baseline run into an explicit no-op control.

    The control is an executed observation of the *baseline oracle*, not an
    invented clean result.  In particular, a route-failed baseline must stay a
    ``FAIL`` control so the intervention pair records fail->pass rather than
    neutral->pass.  This is important for causal attribution and for later
    capability gates (C4/C5), while the control still reuses exactly the same
    source context as the treatment arm.
    """
    verification = copy.deepcopy(treatment.verification)
    refs = [ref for ref in verification.get("evidence_refs", [])
            if isinstance(ref, dict) and ref.get("side") == "before"]
    if refs:
        verification["evidence_refs"] = refs
    payload = treatment.action.get("payload") or {}
    target = str(payload.get("recheck") or "route")
    report = (treatment.before.get("reports") or {}).get(target) or {}
    status = report.get("tier") or report.get("status")
    baseline_ok = status in {"clean", "complete", "pass", "met"}
    predicates = (treatment.before.get("failure_signature") or {}).get("predicates") or {}
    returncode = predicates.get("flow_returncode")
    baseline_failed = (not baseline_ok and (
        status in {"fail", "mismatch", "error"} or
        isinstance(returncode, int) and returncode != 0 or
        bool(treatment.before.get("failure_signature"))))
    baseline_verdict = "FAIL" if baseline_failed else ("PASS" if baseline_ok else "UNKNOWN")
    if baseline_failed:
        delta = {
            "original_failure": "PRESENT",
            "first_divergence": {"before": 1, "after": 1},
            "failing_tests": {"before": 1, "after": 1},
            "created_regressions": [], "newly_observed_failures": [],
            "experiment_kind": "OBSERVATION", "utility_verdict": "NEUTRAL",
        }
    else:
        delta = {
            "original_failure": "UNKNOWN",
            "first_divergence": {"before": 0, "after": 0},
            "failing_tests": {"before": 0, "after": 0},
            "created_regressions": [], "newly_observed_failures": [],
            "experiment_kind": "OBSERVATION", "utility_verdict": "NEUTRAL",
        }
    verification["verdict"] = baseline_verdict
    verification["oracle_complete"] = bool(
        baseline_verdict != "UNKNOWN" and verification.get("oracle_complete"))
    return replace(
        treatment,
        record_id=treatment.record_id + ":control",
        action={
            "domain": "flow.BASELINE_CONTROL",
            "transformation_family": treatment.action.get(
                "transformation_family") or "BASELINE_CONTROL",
            "payload": {"control": True, "config_edits": {}, "recheck": target},
        },
        after=copy.deepcopy(treatment.before),
        observation_delta=delta,
        verification=verification,
        episode={
            "episode_id": "episode:" + treatment.record_id + ":control",
            "mechanism_family": treatment.episode.get("mechanism_family")
            if treatment.episode else "BASELINE_CONTROL",
            "lineage_id": treatment.lineage_id,
            "terminal_status": "VERIFIED_OBSERVATION",
        },
    )


def build_orfs_controlled_replication(
    staging_db: Path | str,
    *,
    pairs: list[dict],
    campaign_id: str,
    output_dir: Path | str,
    split: str = "training",
    min_lineages: int = 2,
) -> dict:
    """Build L2 pairs and an L3 replicated path from real ORFS runs.

    Each item in ``pairs`` must contain ``before_project``, ``after_project``,
    ``lineage_id``, and ``config_edits``.  The baseline project is represented
    twice: once as a no-op control transition and once as the source of the
    treatment transition.  This makes the matched source context explicit;
    no synthetic oracle result is created.
    """
    if not pairs:
        raise ValueError("at least one ORFS control/treatment pair is required")
    lineages = [str(item.get("lineage_id") or "") for item in pairs]
    if any(not lineage for lineage in lineages):
        raise ValueError("every ORFS pair requires lineage_id")
    if len(set(lineages)) != len(lineages):
        raise ValueError("ORFS replication pairs require unique lineages")
    if len(set(lineages)) < max(1, int(min_lineages)):
        raise ValueError("insufficient disjoint ORFS lineages for replication")

    source = Path(staging_db).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"staging database not found: {source}")
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    derived_db = output / "tehm.sqlite"
    _backup_database(source, derived_db)
    conn = db.connect(derived_db)
    db.ensure_schema(conn)
    store = ArtifactStore(output / "artifacts")

    pair_receipts = []
    fragments = []
    for item in pairs:
        treatment = build_orfs_pair_record(
            Path(item["before_project"]), Path(item["after_project"]),
            lineage_id=str(item["lineage_id"]),
            target_check=str(item.get("target_check") or "route"),
            config_edits=dict(item["config_edits"]),
            transformation_family=str(
                item.get("transformation_family") or "DENSITY_RELIEF"),
            rerun_from=str(item.get("rerun_from") or "floorplan"))
        control = _control_record(treatment)
        control_capture = capture(
            conn, store, control, dataset_campaign_id=campaign_id,
            dataset_split=split, dataset_learner_eligible=True)
        treatment_capture = capture(
            conn, store, treatment, dataset_campaign_id=campaign_id,
            dataset_split=split, dataset_learner_eligible=True)
        intervention = build_intervention_pair(
            conn, control_capture.transition_id,
            treatment_capture.transition_id, target_scope="flow.signoff",
            lineage_id=str(item["lineage_id"]), campaign_id=campaign_id)
        control_fragment = build_transition_causal_fragment(
            conn, control_capture.transition_id, campaign_id=campaign_id)
        treatment_fragment = build_transition_causal_fragment(
            conn, treatment_capture.transition_id, campaign_id=campaign_id)
        fragments.extend((control_fragment, treatment_fragment))
        pair_receipts.append({
            "lineage_id": str(item["lineage_id"]),
            "control_transition_id": control_capture.transition_id,
            "treatment_transition_id": treatment_capture.transition_id,
            "control_outcome": control_capture.outcome,
            "treatment_outcome": treatment_capture.outcome,
            "intervention": intervention.to_dict(),
        })

    path = consolidate_causal_path(
        conn, fragments, campaign_id=campaign_id, status="shadow")
    replication = evaluate_replicated_effect(
        conn, path.path_id, campaign_id=campaign_id,
        min_lineages=max(1, int(min_lineages)))
    path_report = path.to_dict()
    path_report["evidence_level"] = replication.evidence_level
    stored_path = conn.execute(
        "SELECT support_json FROM tehm_causal_paths WHERE path_id=?",
        (path.path_id,)).fetchone()
    if stored_path:
        try:
            path_report["support"] = json.loads(stored_path["support_json"])
        except (TypeError, json.JSONDecodeError):
            pass
    rule_evidence = evaluate_causal_rule_evidence(
        conn, path.path_id, campaign_id=campaign_id,
        required_level="L2_CONTROLLED_INTERVENTION",
        min_lineages=max(1, int(min_lineages)))
    counts = {
        "transitions": int(conn.execute(
            "SELECT COUNT(*) FROM tehm_transitions").fetchone()[0]),
        "causal_nodes": int(conn.execute(
            "SELECT COUNT(*) FROM tehm_causal_nodes").fetchone()[0]),
        "causal_edges": int(conn.execute(
            "SELECT COUNT(*) FROM tehm_causal_edges").fetchone()[0]),
        "causal_paths": int(conn.execute(
            "SELECT COUNT(*) FROM tehm_causal_paths").fetchone()[0]),
    }
    schema_version = conn.execute(
        "SELECT value FROM tehm_meta WHERE key='schema_version'").fetchone()[0]
    conn.close()
    report = {
        "version": "orfs-controlled-replication-v1",
        "campaign_id": campaign_id,
        "split": split,
        "source_staging_db": str(source),
        "source_staging_sha256": _sha256(source),
        "derived_db": str(derived_db),
        "derived_db_sha256": _sha256(derived_db),
        "schema_version": schema_version,
        "canonical_memory_mutation": "none",
        "unique_lineages": sorted(lineages),
        "pair_count": len(pair_receipts),
        "pairs": pair_receipts,
        "fragments": [fragment.to_dict() for fragment in fragments],
        "path": path_report,
        "replication": replication.to_dict(),
        "rule_evidence": rule_evidence.to_dict(),
        "derived_counts": counts,
        "promotion_eligible": False,
        "authority_note": (
            "L3 replicated causal evidence is evaluation-only; capability and "
            "rule promotion still require their independent authority gates"),
    }
    (output / "controlled_replication_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


__all__ = [
    "ORFS_CAUSAL_SHADOW_VERSION", "build_orfs_causal_shadow",
    "build_orfs_controlled_replication",
]
