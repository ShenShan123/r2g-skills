#!/usr/bin/env python3
"""Build an external, lineage-grouped Parametric shadow calibration report.

Input JSON is either a list of observations or ``{"samples": [...],
"training_lineages": [...]}``.  The output is a report only: it never opens
the TEHM SQLite store and it cannot promote a Parametric proposal.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.parametric.calibration import calibrate_lineage_grouped  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args(argv)
    payload = json.loads(args.input.read_text())
    if isinstance(payload, list):
        samples, training = payload, []
    elif isinstance(payload, dict):
        samples, training = payload.get("samples") or [], payload.get("training_lineages") or []
    else:
        raise SystemExit("input must be a JSON list or object")
    report = calibrate_lineage_grouped(samples, training_lineages=training)
    report["input"] = str(args.input.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": report.get("status"),
                      "lineage_groups": report.get("lineage_group_count", 0),
                      "promotion_eligible": report.get("promotion_eligible")},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
