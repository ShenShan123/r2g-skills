#!/usr/bin/env python3
"""Audit Revision3 P15-B evidence before any production shadow discussion."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.evaluation.production_readiness import (  # noqa: E402
    ProductionReadinessError, build_production_readiness,
    replay_production_readiness,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibration-report", type=Path)
    parser.add_argument("--interference-summary", type=Path)
    parser.add_argument("--anti-forgetting", type=Path)
    parser.add_argument("--heldout-delta-m", type=Path)
    parser.add_argument("--authority-report", type=Path)
    parser.add_argument("--candidate-pool-evidence", type=Path)
    parser.add_argument("--efficacy-evidence", type=Path)
    parser.add_argument("--mir-sample-plan", type=Path,
                        help="optional replayable MIR sample-size governance receipt")
    parser.add_argument("--schema-contract", type=Path)
    parser.add_argument("--max-mir-upper-ci", type=float, default=0.0,
                        help="explicit MIR Wilson upper-CI policy threshold (default: 0.0)")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replay", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.replay:
            receipt = replay_production_readiness(args.output)
            result = {"mode": "replay", "receipt_id": receipt.receipt_id,
                      "receipt_digest": receipt.receipt_digest,
                      "eligible": receipt.eligible,
                      "gate_status": receipt.gate_status}
        else:
            required = (args.calibration_report, args.interference_summary)
            if any(path is None for path in required):
                parser.error("--calibration-report and --interference-summary are required")
            report = build_production_readiness(
                calibration_report=args.calibration_report,
                interference_summary=args.interference_summary,
                anti_forgetting=args.anti_forgetting,
                heldout_delta_m=args.heldout_delta_m,
                authority_report=args.authority_report,
                candidate_pool_evidence=args.candidate_pool_evidence,
                efficacy_evidence=args.efficacy_evidence,
                mir_sample_plan=args.mir_sample_plan,
                schema_contract=args.schema_contract,
                max_mir_upper_ci=args.max_mir_upper_ci,
                output=args.output)
            receipt = report["receipt"]
            result = {"mode": "freeze", "receipt_id": receipt["receipt_id"],
                      "receipt_digest": receipt["receipt_digest"],
                      "eligible": receipt["eligible"],
                      "gate_status": receipt["gate_status"],
                      "production_integration": "not_attempted"}
    except (OSError, ProductionReadinessError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    # A readiness audit is allowed to be a negative result.  Exit 0 means the
    # report was generated/replayed; the receipt, never the exit code, carries
    # the fail-closed eligibility decision.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
