#!/usr/bin/env python3
"""Build shadow-only conformal evidence from completed ORFS campaigns.

This command consumes campaign manifests and an immutable authority snapshot.
It grades each calibration pair with the existing physical-effect memory,
computes lineage-grouped split-conformal coverage, and writes one external
observation chain per campaign.  It never writes the authority database or
canonical memory; the lifecycle projector remains a separate explicit step.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import sys
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm import db as tehm_db  # noqa: E402
from tehm.batch_lane import (  # noqa: E402
    build_external_observation,
    write_external_observations,
)
from tehm.ids import stable_dumps  # noqa: E402
from tehm.parametric.calibration import calibrate_exact_groups  # noqa: E402
from tehm.physical.effects import extract_deltas  # noqa: E402
from tehm.physical.memory import PhysicalEffectMemory, _action_signature  # noqa: E402
from tehm.physical.orfs_preflight import (  # noqa: E402
    ROUTING_LAYER_ADJUSTMENT, inspect_routing_layer_adjustment,
    parse_orfs_config, preflight_digest)


VERSION = "tehm-orfs-calibration-evidence-v1"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _database_digest(conn: sqlite3.Connection) -> str:
    dump = "\n".join(str(line) for line in conn.iterdump()).encode()
    return hashlib.sha256(dump).hexdigest()


def _authority_lineages(conn: sqlite3.Connection) -> set[str]:
    """Return lineages represented by the immutable authority snapshot.

    Calibration observations must be source-disjoint from the retrieval
    support used to make their predictions.  The lineage is authoritative on
    ``tehm_states`` (rather than inferred from a campaign filename or a
    provenance string), so an old/malformed snapshot without that table is a
    hard error instead of silently disabling the firewall.
    """
    tables = {
        str(row[0]) for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "tehm_states" not in tables:
        raise ValueError("authority snapshot is missing tehm_states lineage table")
    return {
        str(row[0]) for row in conn.execute(
            "SELECT DISTINCT lineage_id FROM tehm_states "
            "WHERE lineage_id IS NOT NULL AND lineage_id <> ''")
    }


def _source_disjoint_overlap(authority_lineages, sample_lineages) -> list[str]:
    """Return the exact lineage overlap between authority and calibration rows."""
    authority = {str(value) for value in authority_lineages if str(value)}
    samples = {str(value) for value in sample_lineages if str(value)}
    return sorted(authority & samples)


def _slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip("-")
    return value or "campaign"


def _finite(value) -> bool:
    return (type(value) in (int, float) and
            not isinstance(value, bool) and math.isfinite(float(value)))


def _bind_routing_preflight(row: dict, item: Mapping, *, orfs_root) -> dict | None:
    """Attach and enforce the executed semantics of a routing calibration row."""
    config_edits = item.get("config_edits") or {}
    if ROUTING_LAYER_ADJUSTMENT not in config_edits:
        return None
    record = row.get("record")
    if not isinstance(record, dict) or not isinstance(record.get("verification"), dict):
        raise ValueError(f"{row.get('case_id')}: calibration record verifier is missing")
    before_project = Path(item["before_project"])
    preflight = inspect_routing_layer_adjustment(
        item.get("platform", ""), config_edits,
        config=parse_orfs_config(before_project / "constraints" / "config.mk"),
        project_dir=before_project, orfs_root=orfs_root)
    preflight["digest"] = preflight_digest(preflight)
    if preflight.get("status") != "EFFECTIVE":
        raise ValueError(
            f"{row.get('case_id')}: routing execution preflight is not effective: "
            f"{preflight.get('status')}:{preflight.get('reason')}")
    record["verification"]["execution_preflight"] = preflight
    return preflight


def _conformal_for_sample(sample: Mapping, radii: Mapping) -> dict:
    """Derive one row-level coverage ratio from the frozen group radii."""
    covered = total = 0
    for metric, radius in sorted(radii.items()):
        predicted = sample["predicted"].get(metric)
        observed = sample["observed_deltas"].get(metric)
        if not (_finite(predicted) and _finite(observed) and _finite(radius)):
            continue
        total += 1
        covered += int(float(predicted) - float(radius) <= float(observed)
                      <= float(predicted) + float(radius))
    if total <= 0:
        raise ValueError(
            f"{sample['case_id']}: no finite metric for conformal evidence")
    return {
        "covered": covered,
        "total": total,
        "coverage": covered / total,
    }


def build(manifests: list[Path], *, authority_db: Path, output_root: Path,
          training_lineages=(), target_coverage: float = .8,
          min_lineages: int = 3, min_samples_per_metric: int = 3,
          max_harmful_rate: float = 0.0) -> dict:
    """Build observations and a calibration report without DB mutation."""
    manifests = [Path(path).expanduser().resolve() for path in manifests]
    if not manifests:
        raise ValueError("at least one campaign manifest is required")
    output_root = Path(output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    db_path = Path(authority_db).expanduser().resolve()

    observations = []
    samples = []
    seen_cases = set()
    conn = tehm_db.connect_read_only(db_path)
    authority_lineages = set()
    try:
        physical = PhysicalEffectMemory(conn)
        authority_digest = _database_digest(conn)
        authority_lineages = _authority_lineages(conn)
        for manifest_path in manifests:
            raw = json.loads(manifest_path.read_text())
            items = raw.get("items")
            if not isinstance(items, list) or len(items) != 1:
                raise ValueError(f"{manifest_path}: expected one manifest item")
            item = dict(items[0])
            # Campaign manifests use the dataset vocabulary (``training``),
            # while the external-observation envelope deliberately reserves
            # ``support`` for learner-admissible calibration rows.  This
            # command is read-only and never imports the row; map only the
            # envelope role, preserving the source manifest unchanged.
            requested_split = item.get("dataset_split")
            item["split"] = (
                "support" if requested_split == "training" else requested_split)
            item["orfs_root"] = raw.get("orfs_root")
            row = build_external_observation(item)
            case_id = row["case_id"]
            if case_id in seen_cases:
                raise ValueError(f"duplicate calibration case: {case_id}")
            seen_cases.add(case_id)
            if row["classification"] != "ELIGIBLE_POSITIVE":
                raise ValueError(f"{case_id}: external observation is incomplete")
            record = row["record"]
            _bind_routing_preflight(row, item, orfs_root=raw.get("orfs_root"))
            observed = extract_deltas(
                record["before"]["reports"]["ppa"],
                record["after"]["reports"]["ppa"])
            graph = dict(row["before"].get("graph") or {})
            graph["platform"] = row["platform"]
            graph["dataset_tier"] = "strict_clean"
            prediction = physical.predict(
                family=row["family"], graph_context=graph,
                action=record["action"], k=5, min_unique_contexts=3,
                max_distance=1.0e12)
            if prediction.get("abstained"):
                raise ValueError(
                    f"{case_id}: physical prediction abstained: "
                    f"{prediction.get('abstain_reasons')}")
            samples.append({
                "case_id": case_id,
                "lineage_id": row["lineage_id"],
                "platform": row["platform"],
                "family": row["family"],
                "dataset_tier": graph["dataset_tier"],
                "action_signature": _action_signature(record["action"]),
                "predicted": prediction["mean_deltas"],
                "observed_deltas": observed,
                "prediction_summary": {
                    "support": prediction.get("support"),
                    "unique_graph_contexts": prediction.get(
                        "unique_graph_contexts"),
                    "nearest_distance": prediction.get("nearest_distance"),
                },
            })
            observations.append({
                "manifest": manifest_path,
                "row": row,
            })
    finally:
        conn.close()

    calibration_lineages = [sample["lineage_id"] for sample in samples]
    overlap = _source_disjoint_overlap(authority_lineages, calibration_lineages)
    if overlap:
        raise ValueError(
            "calibration rows are not source-disjoint from authority support: "
            + ", ".join(overlap))

    calibration = calibrate_exact_groups(
        samples, training_lineages=training_lineages,
        target_coverage=target_coverage, min_lineages=min_lineages,
        min_samples_per_metric=min_samples_per_metric,
        max_harmful_rate=max_harmful_rate)
    if calibration.get("status") != "ready_for_shadow":
        raise ValueError(
            "calibration is not ready_for_shadow: "
            f"{calibration.get('status')}")
    calibration_digest = "sha256:" + hashlib.sha256(
        stable_dumps(calibration).encode()).hexdigest()
    groups = calibration.get("groups") or {}
    if len(groups) != 1:
        raise ValueError(
            "one exact calibration group is required for this action cohort")
    radii = next(iter(groups.values())).get("conformal", {}).get("radii", {})
    method = next(iter(groups.values())).get("conformal", {}).get("method")

    output_summaries = []
    for item, sample in zip(observations, samples):
        row = item["row"]
        conformal = _conformal_for_sample(sample, radii)
        row["record"]["verification"]["conformal"] = {
            **conformal,
            "method": method,
            "calibration_digest": calibration_digest,
        }
        body = {key: value for key, value in row.items()
                if key not in {"receipt_id", "receipt_sha256"}}
        row["receipt_id"] = "orfs-observation:" + hashlib.sha256(
            stable_dumps(body).encode()).hexdigest()[:24]
        campaign = _slug(sample["case_id"].split(":", 2)[1])
        path = output_root / campaign / "external" / "observations.jsonl"
        summary = write_external_observations(path, [row])
        output_summaries.append({
            "case_id": sample["case_id"],
            "lineage_id": sample["lineage_id"],
            "manifest": str(item["manifest"]),
            "manifest_sha256": _sha256_file(item["manifest"]),
            "observations_path": str(path),
            "observations_sha256": summary["sha256"],
            "receipt_id": row["receipt_id"],
            "conformal": conformal,
        })

    report = {
        "version": VERSION,
        "authority_db": str(db_path),
        "authority_db_sha256": authority_digest,
        "source_disjoint": {
            "authority_lineages": sorted(authority_lineages),
            "calibration_lineages": sorted(set(calibration_lineages)),
            "overlap": overlap,
            "disjoint": not overlap,
            "authority_lineage_source": "tehm_states.lineage_id",
        },
        "calibration_digest": calibration_digest,
        "calibration": calibration,
        "samples": samples,
        "observations": output_summaries,
        "canonical_memory_mutation": "none",
        "promotion_eligible": False,
    }
    report["report_sha256"] = "sha256:" + hashlib.sha256(
        stable_dumps(report).encode()).hexdigest()
    report_path = output_root / "routing_conformal_report.json"
    report_path.write_text(stable_dumps(report) + "\n")
    return {"report": report, "report_path": str(report_path)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", action="append", type=Path, required=True)
    parser.add_argument("--authority-db", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--training-lineage", action="append", default=[])
    parser.add_argument("--target-coverage", type=float, default=.8)
    parser.add_argument("--min-lineages", type=int, default=3)
    parser.add_argument("--min-samples-per-metric", type=int, default=3)
    parser.add_argument("--max-harmful-rate", type=float, default=0.0)
    args = parser.parse_args(argv)
    result = build(
        args.manifest, authority_db=args.authority_db,
        output_root=args.output_root, training_lineages=args.training_lineage,
        target_coverage=args.target_coverage, min_lineages=args.min_lineages,
        min_samples_per_metric=args.min_samples_per_metric,
        max_harmful_rate=args.max_harmful_rate)
    report = result["report"]
    print(json.dumps({
        "version": VERSION,
        "status": report["calibration"]["status"],
        "sample_count": report["calibration"]["sample_count"],
        "calibration_digest": report["calibration_digest"],
        "report": result["report_path"],
        "canonical_memory_mutation": "none",
        "promotion_eligible": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
