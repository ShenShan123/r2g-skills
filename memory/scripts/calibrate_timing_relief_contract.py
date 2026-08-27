#!/usr/bin/env python3
"""Fit an action-bound conformal policy from isolated physical support.

The support DB is opened read-only and the calibration observations remain
external.  No calibration sample is imported, no canonical memory is opened
writable, and no promotion decision is produced.
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

from tehm.physical.calibration import calibrate_retrieval  # noqa: E402
from tehm.physical.effects import extract_deltas  # noqa: E402
from tehm.physical.memory import PhysicalEffectMemory  # noqa: E402
from tehm.physical.utility_contracts import (  # noqa: E402
    contract_action,
    timing_relief_budgeted_v1,
    timing_relief_budgeted_v2_50_to_45,
    utility_contract_digest,
)


def _ppa(metrics: dict) -> dict:
    return {"summary": {
        "timing": {"setup_wns": metrics.get("wns_ns"),
                   "setup_tns": metrics.get("tns_ns")},
        "area": {"design_area_um2": metrics.get("area_um2")},
        "power": {"total_power_w": metrics.get("power_w")},
    }}


def _contract(path: Path) -> dict:
    payload = json.loads(path.read_text())
    factories = {
        "TIMING_RELIEF_BUDGETED_V1": timing_relief_budgeted_v1,
        "TIMING_RELIEF_BUDGETED_V2_50_TO_45": timing_relief_budgeted_v2_50_to_45,
    }
    factory = factories.get(payload.get("contract_id"))
    if factory is None:
        raise ValueError(f"unsupported contract: {payload.get('contract_id')}")
    contract = factory()
    if payload.get("utility_contract_digest") != utility_contract_digest(contract):
        raise ValueError("contract specification digest mismatch")
    return contract


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--contract-spec", type=Path, required=True)
    ap.add_argument("--support-db", type=Path, required=True)
    ap.add_argument("--support-manifest", type=Path, required=True)
    ap.add_argument("--calibration-observations", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args(argv)
    contract = _contract(args.contract_spec)
    action = contract_action(contract)
    support_manifest = json.loads(args.support_manifest.read_text())
    training_lineages = [str(item["lineage_id"])
                         for item in support_manifest.get("items") or ()]
    rows = [json.loads(line) for line in args.calibration_observations.read_text().splitlines()
            if line.strip()]
    samples = []
    for row in rows:
        before, after = row.get("before") or {}, row.get("after") or {}
        samples.append({
            "lineage_id": row["lineage_id"],
            "family": row["family"],
            "action": action,
            "graph_context": before.get("graph"),
            "observed_deltas": extract_deltas(
                _ppa(before.get("ppa_metrics") or {}),
                _ppa(after.get("ppa_metrics") or {})),
        })
    before_canonical = _canonical_snapshots()
    conn = sqlite3.connect(f"file:{args.support_db.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        memory = PhysicalEffectMemory(conn)
        policy = calibrate_retrieval(
            memory, family=contract["action_signature"]["transformation_family"],
            heldout_samples=samples, training_lineages=training_lineages,
            k=5, min_unique_contexts=3, min_samples=3,
            target_coverage=0.8, per_metric_target_coverage=0.8,
            distance_ceiling=3.0, distance_quantile=0.95,
            uncertainty_quantile=0.95,
            interval_method="split_conformal_residual_v1")
    finally:
        conn.close()
    after_canonical = _canonical_snapshots()
    if before_canonical != after_canonical:
        raise RuntimeError("canonical memory changed during read-only calibration")
    report = {
        "version": "timing-relief-calibration-v1",
        "status": policy.get("status"),
        "contract_id": contract["contract_id"],
        "utility_contract_digest": utility_contract_digest(contract),
        "policy": policy,
        "support_db": str(args.support_db.resolve()),
        "support_db_sha256": hashlib.sha256(args.support_db.read_bytes()).hexdigest(),
        "support_manifest": str(args.support_manifest.resolve()),
        "support_manifest_sha256": hashlib.sha256(
            args.support_manifest.read_bytes()).hexdigest(),
        "calibration_observations": str(args.calibration_observations.resolve()),
        "calibration_observations_sha256": hashlib.sha256(
            args.calibration_observations.read_bytes()).hexdigest(),
        "training_lineages": training_lineages,
        "calibration_lineages": [row["lineage_id"] for row in rows],
        "canonical_memory_mutation": "none",
        "promotion_eligible": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "status": report["status"],
        "contract_id": report["contract_id"],
        "calibration_sample_count": len(rows),
        "empirical_coverage": (policy.get("calibration") or {}).get("empirical_coverage"),
        "promotion_eligible": False,
    }, sort_keys=True))
    return 0


def _canonical_snapshots() -> list[dict]:
    from tehm.batch_lane import canonical_snapshots
    return canonical_snapshots()


if __name__ == "__main__":
    raise SystemExit(main())
