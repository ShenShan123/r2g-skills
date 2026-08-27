#!/usr/bin/env python3
"""Collect preserved successful ORFS/PPA run evidence without TEHM mutation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tehm.physical.orfs_ppa import extract_orfs_ppa  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-meta", action="append", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args(argv)
    rows, failures = [], []
    for path in args.run_meta:
        try:
            rows.append(extract_orfs_ppa(path))
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            failures.append({"run_meta": str(path), "reason": str(exc)})
    report = {"version": "orfs-ppa-inventory-v1", "runs": rows,
              "failures": failures,
              "mutation": {"sqlite_opened": False, "canonical_memory": "unchanged"}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"runs": len(rows), "failures": len(failures),
                      "complete_runs": sum(bool(r.get("complete")) for r in rows)},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
