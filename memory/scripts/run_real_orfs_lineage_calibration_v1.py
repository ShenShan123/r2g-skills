#!/usr/bin/env python3
"""Extract real ORFS/PPA pairs and fit exact lineage-grouped shadow calibration.

The pair manifest must name two successful preserved ``run-meta.json`` files
and provide an externally produced point prediction.  This command does not
open SQLite, record physical effects, or promote a rule.  Incomplete PPA
records stay in the report as excluded evidence rather than being imputed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.parametric.calibration import calibrate_exact_groups  # noqa: E402
from tehm.physical.orfs_ppa import build_orfs_pair, extract_orfs_ppa  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pairs", type=Path, required=True,
                    help="JSON list of before/after run-meta pair descriptors")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args(argv)
    payload = json.loads(args.pairs.read_text())
    rows = (payload.get("pairs", []) if isinstance(payload, dict)
            else payload if isinstance(payload, list) else None)
    if not isinstance(rows, list):
        raise SystemExit("pairs input must be a JSON list or {'pairs': [...]} object")
    samples, excluded = [], []
    for index, item in enumerate(rows):
        try:
            before = extract_orfs_ppa(item["before_run_meta"],
                                      report=item.get("before_report"),
                                      route_report=item.get("before_route_report"),
                                      drc_report=item.get("before_drc_report"))
            after = extract_orfs_ppa(item["after_run_meta"],
                                     report=item.get("after_report"),
                                     route_report=item.get("after_route_report"),
                                     drc_report=item.get("after_drc_report"))
            pair = build_orfs_pair(
                before, after, lineage_id=item["lineage_id"],
                platform=item["platform"], family=item["family"],
                dataset_tier=item["dataset_tier"], action=item["action"],
                predicted=item.get("predicted"))
            if not pair["complete"]:
                excluded.append({"index": index, "reason": "incomplete_ppa",
                                 "missing_before": before["provenance"]["missing_metrics"],
                                 "missing_after": after["provenance"]["missing_metrics"]})
                continue
            if pair["predicted"] is None:
                excluded.append({"index": index, "reason": "missing_external_prediction"})
                continue
            samples.append(pair)
        except (KeyError, TypeError, ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
            excluded.append({"index": index, "reason": f"invalid_pair:{exc}"})
    training = payload.get("training_lineages", []) if isinstance(payload, dict) else []
    calibration = calibrate_exact_groups(samples, training_lineages=training)
    report = {
        "version": "real-orfs-lineage-calibration-v1",
        "source_pairs": str(args.pairs.resolve()),
        "sample_count": len(samples), "excluded_count": len(excluded),
        "excluded": excluded, "samples": samples,
        "calibration": calibration,
        "mutation": {"sqlite_opened": False, "canonical_memory": "unchanged",
                      "lifecycle": "unchanged", "promotion_eligible": False},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": calibration.get("status"),
                      "samples": len(samples), "excluded": len(excluded),
                      "promotion_eligible": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
