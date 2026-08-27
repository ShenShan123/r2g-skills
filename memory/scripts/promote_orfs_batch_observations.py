#!/usr/bin/env python3
"""Authority-gated import of eligible ORFS support observations to canonical.

This command is intentionally absent from ``run_orfs_batch0.py --phase all``.
It requires an independent lifecycle authority receipt bound to the external
receipt chain, staging DB, and pre-import canonical DB digest.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MEMORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MEMORY_ROOT))

from tehm.batch_lane import import_support_to_canonical  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--observations", type=Path, required=True)
    ap.add_argument("--staging-db", type=Path, required=True)
    ap.add_argument("--canonical-db", type=Path, required=True)
    ap.add_argument("--canonical-artifacts", type=Path, required=True)
    ap.add_argument("--campaign-id", required=True)
    ap.add_argument("--authority-receipt", type=Path, required=True)
    args = ap.parse_args(argv)
    authority = json.loads(args.authority_receipt.read_text())
    result = import_support_to_canonical(
        observations_path=args.observations,
        staging_db=args.staging_db,
        canonical_db=args.canonical_db,
        canonical_artifacts=args.canonical_artifacts,
        campaign_id=args.campaign_id,
        authority=authority,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
