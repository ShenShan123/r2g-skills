#!/usr/bin/env python3
"""Wrap a replayed routed-policy MIR aggregate for P15-B interference gating.

The wrapper is still evaluation-only.  It adds the typed challenge-lane
context expected by production readiness while retaining the aggregate's
content-addressed cohort references; it does not alter canonical memory or
production authority.
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
    PolicyMIRError, replay_routed_policy_mir,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-mir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        policy_path = args.policy_mir.expanduser().resolve()
        report = json.loads(policy_path.read_text())
        raw = report.get("policy_mir")
        metrics = replay_routed_policy_mir(raw, base=policy_path.parent)
        wrapped = {
            "reason": "MEMORY_INTERFERENCE",
            "canonical_memory_mutation": "none",
            "production_authority_changed": False,
            "production_runtime_imported": False,
            "memory_docs_submitted": False,
            "case_count": metrics["case_count"],
            "policy_mir": raw,
        }
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(wrapped, indent=2, sort_keys=True) + "\n")
    except (OSError, json.JSONDecodeError, PolicyMIRError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"output": str(output), "case_count": metrics["case_count"],
                      "receipt_digest": raw["receipt_digest"],
                      "production_integration": "not_attempted"},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
