#!/usr/bin/env python3
"""Freeze a completed all-PASS P12 report as Revision3 Validation Cohort V0."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.evaluation.validation_freeze import (  # noqa: E402
    ValidationFreezeError, freeze_validation_cohort, replay_validation_freeze,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort-report", type=Path)
    parser.add_argument("--trigger-report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replay", action="store_true",
                        help="replay an existing freeze report instead of creating one")
    args = parser.parse_args(argv)
    try:
        if args.replay:
            receipt = replay_validation_freeze(args.output)
            result = {"mode": "replay", "receipt_id": receipt.receipt_id,
                      "receipt_digest": receipt.receipt_digest,
                      "lane": receipt.lane, "expected_action": receipt.expected_action,
                      "triggered_count": receipt.triggered_count}
        else:
            if args.cohort_report is None or args.trigger_report is None:
                parser.error("--cohort-report and --trigger-report are required unless --replay")
            report = freeze_validation_cohort(
                args.cohort_report, args.trigger_report, output=args.output)
            receipt = report["freeze_receipt"]
            result = {"mode": "freeze", "receipt_id": receipt["receipt_id"],
                      "receipt_digest": receipt["receipt_digest"],
                      "lane": report["lane"], "expected_action": report["expected_action"],
                      "triggered_count": report["triggered_count"]}
    except (OSError, ValidationFreezeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
