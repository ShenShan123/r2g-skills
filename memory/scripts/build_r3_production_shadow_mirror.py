#!/usr/bin/env python3
"""Build or replay the fail-closed Revision3 P17 shadow-mirror receipt."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.evaluation.production_shadow_mirror import (  # noqa: E402
    ProductionShadowMirrorError, build_shadow_mirror_report,
    replay_shadow_mirror_report,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--readiness-report", type=Path,
                        help="P15 readiness JSON used to prepare a mirror")
    parser.add_argument("--comparisons", type=Path,
                        help="optional JSON list/object of base/shadow comparisons")
    parser.add_argument("--allowlist", action="append", default=[],
                        help="optional case ID allowlist (repeatable)")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--replay", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.replay:
            receipt = replay_shadow_mirror_report(args.output)
            result = {
                "mode": "replay",
                "receipt_id": receipt.receipt_id,
                "receipt_digest": receipt.receipt_digest,
                "mirror_status": receipt.mirror_status,
                "readiness_eligible": receipt.readiness_eligible,
                "comparison_count": receipt.comparison_count,
            }
        else:
            if args.readiness_report is None:
                parser.error("--readiness-report is required unless --replay is set")
            report = build_shadow_mirror_report(
                args.readiness_report, output=args.output,
                comparisons_path=args.comparisons,
                allowlist=args.allowlist, force=args.force)
            receipt = report["receipt"]
            result = {
                "mode": "build",
                "receipt_id": receipt["receipt_id"],
                "receipt_digest": receipt["receipt_digest"],
                "mirror_status": receipt["mirror_status"],
                "readiness_eligible": receipt["readiness_eligible"],
                "comparison_count": receipt["comparison_count"],
            }
    except (OSError, ProductionShadowMirrorError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
