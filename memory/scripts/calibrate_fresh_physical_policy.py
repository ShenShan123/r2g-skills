#!/usr/bin/env python3
"""Fit a read-only calibration policy from prior and fresh external samples.

This command deliberately does not record the fresh samples in TEHM.  It reads
the canonical snapshot through SQLite's read-only URI, binds the training
lineage firewall, and writes only a serializable policy/report for a later
prospective shadow campaign.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

MEMORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MEMORY_ROOT))

from tehm import db as tehm_db  # noqa: E402
from tehm.physical.calibration import calibrate_retrieval  # noqa: E402
from tehm.physical.memory import PhysicalEffectMemory  # noqa: E402
from tehm.sync import canonical_json  # noqa: E402


VERSION = "fresh-physical-calibration-policy-v1"


def read(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def digest(value) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def training_lineages(manifest: dict) -> list[str]:
    firewall = manifest.get("firewall") or {}
    values = firewall.get("training_lineages") or []
    if not isinstance(values, list) or any(not isinstance(x, str) or not x for x in values):
        raise ValueError("training manifest firewall.training_lineages is invalid")
    return sorted(set(values))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, required=True,
                    help="read-only copy of canonical closed_loop/tehm.sqlite")
    ap.add_argument("--training-manifest", type=Path, required=True)
    ap.add_argument("--base-samples", type=Path, required=True)
    ap.add_argument("--fresh-samples", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--platform", default="sky130hs")
    ap.add_argument("--family", default="DENSITY_RELIEF")
    ap.add_argument("--tier", default="research")
    args = ap.parse_args(argv)

    base = read(args.base_samples)
    fresh = read(args.fresh_samples)
    samples = list(base.get("samples") or []) + list(fresh.get("samples") or [])
    selected = [row for row in samples
                if row.get("platform") == args.platform
                and row.get("family") == args.family
                and (row.get("expected_tier") or
                     (row.get("graph_context") or {}).get("dataset_tier")) == args.tier]
    if not selected:
        raise ValueError("no samples match requested platform/family/tier")
    lineages = sorted({str(row.get("lineage_id") or "") for row in selected})
    if "" in lineages:
        raise ValueError("selected samples contain missing lineage_id")
    training = training_lineages(read(args.training_manifest))
    overlap = sorted(set(lineages) & set(training))
    if overlap:
        raise ValueError(f"calibration sample/training lineage overlap: {overlap}")

    conn = tehm_db.connect_read_only(args.db.resolve())
    try:
        memory = PhysicalEffectMemory(conn)
        count_before = memory.count()
        policy = calibrate_retrieval(
            memory, family=args.family, heldout_samples=selected,
            training_lineages=training, min_samples=3,
            min_unique_contexts=3, target_coverage=0.80,
            distance_ceiling=3.0)
        count_after = memory.count()
    finally:
        conn.close()
    if count_after != count_before:
        raise RuntimeError(f"read-only calibration changed physical count: {count_before}->{count_after}")

    report = {
        "version": VERSION,
        "policy_scope": {
            "platform": args.platform, "family": args.family, "tier": args.tier,
        },
        "policy": policy,
        "sample_count": len(selected),
        "sample_lineages": lineages,
        "sample_sources": {
            "base_samples": str(args.base_samples.resolve()),
            "fresh_samples": str(args.fresh_samples.resolve()),
            "base_digest": digest(base), "fresh_digest": digest(fresh),
        },
        "training_lineages": training,
        "firewall": {"disjoint": not overlap, "overlap": overlap},
        "physical_memory_count_before": count_before,
        "physical_memory_count_after": count_after,
        "physical_memory_mutation": 0,
        "parametric_view_status": "NOT_IMPLEMENTED",
        "promotion_eligible": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json(report))
    print(json.dumps({
        "ok": True, "status": policy.get("status"),
        "sample_count": len(selected), "sample_lineages": lineages,
        "physical_memory_mutation": 0,
        "output": str(args.output.resolve()),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
