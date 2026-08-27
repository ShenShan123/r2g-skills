#!/usr/bin/env python3
"""Build evaluation-only L2/L3 causal evidence from completed ORFS pairs."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.causal.orfs import build_orfs_controlled_replication  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-db", type=Path, required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pair", action="append", nargs=4, metavar=(
        "BEFORE", "AFTER", "LINEAGE", "CONFIG_JSON"), required=True,
        help="one before/after project pair and JSON config edit mapping")
    parser.add_argument(
        "--split", choices=("training",), default="training",
        help="learner split for controlled L2/L3 replication (training only)")
    parser.add_argument("--min-lineages", type=int, default=2)
    args = parser.parse_args(argv)
    pairs = [
        {"before_project": before, "after_project": after,
         "lineage_id": lineage, "config_edits": json.loads(config_json)}
        for before, after, lineage, config_json in args.pair
    ]
    report = build_orfs_controlled_replication(
        args.staging_db, pairs=pairs, campaign_id=args.campaign_id,
        output_dir=args.output, split=args.split,
        min_lineages=args.min_lineages)
    print(json.dumps({
        "campaign_id": report["campaign_id"],
        "pair_count": report["pair_count"],
        "path_evidence_level": report["path"]["evidence_level"],
        "replication": report["replication"],
        "rule_evidence": report["rule_evidence"],
        "promotion_eligible": report["promotion_eligible"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
