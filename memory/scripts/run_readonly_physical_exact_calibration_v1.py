#!/usr/bin/env python3
"""Fit exact lineage-grouped calibration from preserved ORFS/PPA observations.

The DB is opened through SQLite's read-only URI and is used only for point
prediction.  Held-out samples are never recorded, and all canonical counters
are checked before/after the read.  Graph context supplies the dataset tier
when the legacy sample envelope left it at the top level as ``null``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.parametric.calibration import calibrate_exact_groups  # noqa: E402
from tehm.physical.memory import PhysicalEffectMemory, _action_signature  # noqa: E402


VERSION = "readonly-physical-exact-calibration-v1"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--training-report", type=Path, required=True,
                    help="calibration report containing disjoint training_lineages")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--min-unique-contexts", type=int, default=3)
    args = ap.parse_args(argv)

    sample_payload = json.loads(args.samples.read_text())
    sample_rows = sample_payload.get("samples", [])
    training_payload = json.loads(args.training_report.read_text())
    training_lineages = training_payload.get("training_lineages", [])
    if not isinstance(sample_rows, list) or not isinstance(training_lineages, list):
        raise SystemExit("samples/training report has invalid shape")

    db_path = args.db.resolve()
    uri = "file:" + str(db_path).replace("?", "%3F") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    memory = PhysicalEffectMemory(conn)
    before_count = memory.count()
    rows, excluded = [], []
    for index, sample in enumerate(sample_rows):
        try:
            graph = dict(sample.get("graph_context") or {})
            platform = str(sample.get("platform") or graph.get("platform") or "")
            family = str(sample.get("family") or "")
            tier = str(sample.get("dataset_tier") or
                       graph.get("dataset_tier") or
                       sample.get("expected_tier") or "")
            action = sample.get("action")
            signature = _action_signature(action)
            if not platform or not family or not tier or signature is None:
                raise ValueError("missing platform/family/tier/action_signature")
            graph["platform"] = platform
            graph["dataset_tier"] = tier
            prediction = memory.predict(
                family=family, graph_context=graph, action=action,
                k=max(1, args.k), min_unique_contexts=max(2, args.min_unique_contexts),
                max_distance=1.0e12)
            if prediction.get("abstained"):
                raise ValueError("prediction_abstained:" + ",".join(
                    prediction.get("abstain_reasons") or []))
            rows.append({
                "lineage_id": str(sample.get("lineage_id") or ""),
                "platform": platform, "family": family,
                "dataset_tier": tier, "action": action,
                "action_signature": signature,
                "predicted": prediction.get("mean_deltas") or {},
                "observed_deltas": sample.get("observed_deltas") or {},
                "prediction_audit": {
                    "support": prediction.get("support"),
                    "nearest_distance": prediction.get("nearest_distance"),
                    "query_graph_context_digest": prediction.get(
                        "query_graph_context_digest"),
                    "action_signature": prediction.get("action_signature"),
                },
                "evidence": sample.get("evidence"),
            })
        except (TypeError, ValueError, KeyError) as exc:
            excluded.append({"index": index, "lineage_id": sample.get("lineage_id"),
                             "reason": str(exc)})
    after_count = memory.count()
    conn.close()
    if before_count != after_count:
        raise RuntimeError(f"canonical physical count changed: {before_count}->{after_count}")

    calibration = calibrate_exact_groups(
        rows, training_lineages=training_lineages,
        target_coverage=0.80, min_lineages=3, min_samples_per_metric=3,
        max_harmful_rate=0.0)
    report = {
        "version": VERSION,
        "samples": str(args.samples.resolve()),
        "training_report": str(args.training_report.resolve()),
        "db": str(db_path),
        "source_digests": {
            "samples_sha256": _sha(args.samples),
            "training_report_sha256": _sha(args.training_report),
            "db_sha256": _sha(db_path),
        },
        "training_lineages": sorted(str(x) for x in training_lineages),
        "sample_count": len(sample_rows), "usable_count": len(rows),
        "excluded": excluded,
        "canonical_physical_count_before": before_count,
        "canonical_physical_count_after": after_count,
        "canonical_memory_mutation": "none",
        "calibration": calibration,
        "promotion_eligible": False,
        "parametric_view_status": "NOT_IMPLEMENTED",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": calibration.get("status"),
                      "group_count": calibration.get("group_count"),
                      "usable": len(rows), "excluded": len(excluded),
                      "physical_count": before_count,
                      "promotion_eligible": False}, sort_keys=True))
    return 0


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
