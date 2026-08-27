#!/usr/bin/env python3
"""Materialize real ORFS staging evidence into the v4 causal shadow lane."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.causal.orfs import build_orfs_causal_shadow  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-db", type=Path, required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", default="training")
    args = parser.parse_args(argv)
    report = build_orfs_causal_shadow(
        args.staging_db, campaign_id=args.campaign_id,
        output_dir=args.output, split=args.split)
    print(json.dumps({
        "campaign_id": report["campaign_id"],
        "schema_version": report["schema_version"],
        "fragments": len(report["fragments"]),
        "paths": len(report["paths"]),
        "replication": report["replication"],
        "promotion_eligible": report["promotion_eligible"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
