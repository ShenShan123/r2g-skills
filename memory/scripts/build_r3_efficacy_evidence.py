#!/usr/bin/env python3
"""Build or replay typed Revision3 efficacy evidence."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.evaluation.efficacy_evidence import (  # noqa: E402
    EfficacyEvidenceError, build_efficacy_evidence, replay_efficacy_evidence,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before-cohort", type=Path)
    parser.add_argument("--after-cohort", type=Path)
    parser.add_argument("--policy-arm", choices=("APPLICABILITY_GATED", "CAUSAL_NO_SKILL"),
                        default="CAUSAL_NO_SKILL")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replay", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.replay:
            raw = json.loads(args.output.read_text())
            metrics = replay_efficacy_evidence(raw, base=args.output.parent)
            result = {"mode": "replay", "receipt_digest": raw.get("receipt_digest"),
                      "paired_cases": metrics["paired_cases"],
                      "harm_reduction_observed": metrics["harm_reduction_observed"]}
        else:
            if args.before_cohort is None or args.after_cohort is None:
                parser.error("--before-cohort and --after-cohort are required unless --replay is used")
            report = build_efficacy_evidence(
                before_cohort=args.before_cohort, after_cohort=args.after_cohort,
                policy_arm=args.policy_arm, output=args.output)
            result = {"mode": "freeze", "receipt_digest": report["receipt_digest"],
                      "paired_cases": report["metrics"]["paired_cases"],
                      "baseline_harmful_activation_rate": report["metrics"]["baseline_harmful_activation_rate"],
                      "memory_harmful_activation_rate": report["metrics"]["memory_harmful_activation_rate"],
                      "harm_reduction_observed": report["metrics"]["harm_reduction_observed"],
                      "production_integration": "not_attempted"}
    except (OSError, EfficacyEvidenceError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
