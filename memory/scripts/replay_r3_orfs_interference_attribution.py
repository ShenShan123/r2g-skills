#!/usr/bin/env python3
"""Read-only replay for a Revision3 ORFS P14 attribution artifact."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.evaluation.orfs_interference_attribution_replay import replay  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True,
                        help="P14 attribution output directory")
    parser.add_argument("--shadow-artifacts", type=Path, required=True,
                        help="immutable P13 ORFS shadow output directory")
    parser.add_argument("--challenge-artifacts", type=Path, required=True,
                        help="immutable P12/P13 ORFS challenge output directory")
    args = parser.parse_args(argv)
    try:
        result = replay(args.artifacts,
                        shadow_artifacts=args.shadow_artifacts,
                        challenge_artifacts=args.challenge_artifacts)
    except Exception as exc:
        print(f"replay failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
