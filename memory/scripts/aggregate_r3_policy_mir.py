#!/usr/bin/env python3
"""Aggregate independent Revision3 routed-policy MIR cohort receipts.

The command emits an evaluation-only ``r3-policy-mir-v2`` receipt.  It is an
aggregation/indexing step, not a production gate or authority operation.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.evaluation.policy_mir import (  # noqa: E402
    PolicyMIRError, build_routed_policy_mir, replay_routed_policy_mir,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", type=Path, action="append",
                        help="frozen typed cohort.json; repeat for independent cohorts")
    parser.add_argument("--policy-arm", choices=("APPLICABILITY_GATED", "CAUSAL_NO_SKILL"),
                        help="routed arm (required when freezing a new aggregate)")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replay", action="store_true",
                        help="replay an existing v2 report at --output")
    args = parser.parse_args(argv)
    try:
        if args.replay:
            report = json.loads(args.output.read_text())
            raw = report.get("policy_mir")
            metrics = replay_routed_policy_mir(raw, base=args.output.parent)
            result = {"mode": "replay", "receipt_digest": raw.get("receipt_digest"),
                      "case_count": metrics["case_count"],
                      "cohort_count": metrics["cohort_count"],
                      "harmful_cases": metrics["harmful_cases"]}
        else:
            if not args.cohort or args.policy_arm is None:
                parser.error("--cohort and --policy-arm are required unless --replay is used")
            payload = build_routed_policy_mir(
                args.cohort, policy_arm=args.policy_arm, output=args.output)
            result = {"mode": "freeze", "receipt_digest": payload["receipt_digest"],
                      "case_count": payload["case_count"],
                      "cohort_count": payload["cohort_count"],
                      "harmful_cases": payload["harmful_cases"],
                      "upper_ci": payload["upper_ci"],
                      "production_integration": "not_attempted"}
    except (OSError, PolicyMIRError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
