#!/usr/bin/env python3
"""Plan routed-policy MIR sample sizes from a typed aggregate.

The input aggregate is replayed before its known/harmful counts are used.  The
output is an evaluation-only planning receipt; it does not change the MIR
threshold, canonical memory, production authority, or runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.evaluation.mir_sample_plan import (  # noqa: E402
    DEFAULT_MIR_SAMPLE_THRESHOLDS, MIRError, build_mir_sample_plan,
    replay_mir_sample_plan,
)
from tehm.evaluation.policy_mir import (  # noqa: E402
    PolicyMIRError, replay_routed_policy_mir,
)


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MIRError(f"MIR aggregate is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise MIRError("MIR aggregate report must be an object")
    return payload


def _aggregate(path: Path) -> tuple[int, int, dict]:
    payload = _load(path)
    raw = payload.get("policy_mir") or payload
    if not isinstance(raw, dict):
        raise MIRError("MIR aggregate payload is malformed")
    try:
        metrics = replay_routed_policy_mir(raw, base=path.parent)
    except PolicyMIRError as exc:
        raise MIRError(f"MIR aggregate cannot replay: {exc}") from exc
    evidence = {
        "path": str(path.resolve()), "sha256": _file_digest(path),
        "receipt_digest": raw.get("receipt_digest"),
        "version": raw.get("version"), "policy_arm": raw.get("policy_arm"),
        "known_cases": metrics["total_cases"],
        "harmful_cases": metrics["harmful_cases"],
        "upper_ci": metrics["upper_ci"],
    }
    return metrics["total_cases"], metrics["harmful_cases"], evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mir-aggregate", type=Path,
                        help="typed r3-policy-mir-v2 aggregate/report")
    parser.add_argument("--output", type=Path, required=True,
                        help="external JSON planning receipt")
    parser.add_argument("--threshold", type=float, action="append", dest="thresholds",
                        help="explicit upper-CI threshold; repeatable (default: 0.0, 0.10, 0.05, 0.02, 0.01)")
    parser.add_argument("--max-search-cases", type=int, default=1_000_000)
    parser.add_argument("--replay", action="store_true",
                        help="replay the existing planning receipt instead of rebuilding")
    args = parser.parse_args(argv)
    try:
        if args.replay:
            receipt = replay_mir_sample_plan(args.output)
            result = {"mode": "replay", "receipt_id": receipt.receipt_id,
                      "receipt_digest": receipt.receipt_digest,
                      "current_known_cases": receipt.current_known_cases,
                      "current_upper_ci": receipt.current_upper_ci}
        else:
            if args.mir_aggregate is None:
                raise MIRError("--mir-aggregate is required when building a plan")
            mir_path = args.mir_aggregate.expanduser().resolve()
            if not mir_path.is_file():
                raise MIRError(f"MIR aggregate is not a file: {mir_path}")
            known, harmful, evidence = _aggregate(mir_path)
            report = build_mir_sample_plan(
                current_known_cases=known, current_harmful_cases=harmful,
                thresholds=(tuple(args.thresholds)
                            if args.thresholds is not None
                            else DEFAULT_MIR_SAMPLE_THRESHOLDS),
                max_search_cases=args.max_search_cases,
                current_evidence=evidence, output=args.output)
            result = {"mode": "build", "receipt_id": report["receipt_id"],
                      "receipt_digest": report["receipt_digest"],
                      "current_known_cases": known,
                      "current_harmful_cases": harmful,
                      "current_upper_ci": report["mir_sample_plan"]["current_upper_ci"]}
    except (MIRError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
