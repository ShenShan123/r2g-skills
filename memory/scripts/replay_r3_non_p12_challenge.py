#!/usr/bin/env python3
"""Replay a Revision3 non-P12 challenge report fail-closed."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.evaluation.non_p12_challenge import (  # noqa: E402
    NonP12ChallengeReplayError, replay_capability_gap_challenge,
    replay_repeated_failure_challenge,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("capability-gap", "repeated-failure"),
                        required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        replay = (replay_capability_gap_challenge
                  if args.kind == "capability-gap"
                  else replay_repeated_failure_challenge)
        result = replay(args.report)
    except (OSError, NonP12ChallengeReplayError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
