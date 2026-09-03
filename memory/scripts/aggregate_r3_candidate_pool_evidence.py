#!/usr/bin/env python3
"""Aggregate independently frozen Revision3 candidate-pool receipts."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.evaluation.candidate_pool_aggregate import (  # noqa: E402
    CandidatePoolAggregateError, build_candidate_pool_aggregate,
    replay_candidate_pool_aggregate,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, action="append",
                        help="per-cohort candidate_pool.json; repeat for each cohort")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replay", action="store_true",
                        help="replay an existing aggregate at --output")
    args = parser.parse_args(argv)
    try:
        if args.replay:
            raw = json.loads(args.output.read_text())
            metrics = replay_candidate_pool_aggregate(raw, base=args.output.parent)
            result = {"mode": "replay", "receipt_digest": raw.get("receipt_digest"),
                      "cohort_count": metrics["cohort_count"],
                      "case_count": metrics["cases"]}
        else:
            if not args.evidence:
                parser.error("--evidence is required unless --replay is used")
            payload = build_candidate_pool_aggregate(
                args.evidence, output=args.output)
            result = {"mode": "freeze", "receipt_digest": payload["receipt_digest"],
                      "cohort_count": payload["cohort_count"],
                      "case_count": payload["case_count"],
                      "candidate_diversity": payload["metrics"]["candidate_diversity"],
                      "production_integration": "not_attempted"}
    except (OSError, CandidatePoolAggregateError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
