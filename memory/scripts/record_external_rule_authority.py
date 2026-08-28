#!/usr/bin/env python3
"""Record a DB-bound rule authority receipt from external audit evidence.

The external observations and staging database are read-only inputs.  The
authority database receives only the normal rule-authority evidence/receipt
ledger rows; this command never imports transitions into canonical memory and
never changes rule lifecycle status.  A receipt that is missing any gate is
still recorded as an auditable, ineligible attempt.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MEMORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MEMORY_ROOT))

from tehm import db as tehm_db  # noqa: E402
from tehm.lifecycle import (  # noqa: E402
    record_rule_authority_from_external_observations,
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--authority-db", type=Path, required=True,
                    help="writable TEHM DB containing the candidate rule/trial")
    ap.add_argument("--observations", type=Path, required=True)
    ap.add_argument("--staging-db", type=Path, required=True,
                    help="closed/checkpointed campaign staging DB")
    ap.add_argument("--campaign-id", required=True,
                    help="staging campaign membership to bind")
    ap.add_argument("--rule-id", required=True)
    ap.add_argument("--target-scope", default="route")
    ap.add_argument("--trial-id", required=True)
    ap.add_argument("--status-version", type=int, default=None)
    ap.add_argument("--case-id", dest="case_ids", action="append", required=True)
    ap.add_argument("--transfer-receipt-id", dest="transfer_ids",
                    action="append", default=None,
                    help="optional replay-verified L4 receipt (repeatable)")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args(argv)

    conn = tehm_db.connect(args.authority_db.resolve())
    tehm_db.ensure_schema(conn)
    try:
        receipt = record_rule_authority_from_external_observations(
            conn,
            rule_id=args.rule_id,
            target_scope=args.target_scope,
            trial_id=args.trial_id,
            expected_status_version=args.status_version,
            observations_path=args.observations.resolve(),
            staging_db=args.staging_db.resolve(),
            campaign_id=args.campaign_id,
            case_ids=args.case_ids,
            causal_transfer_receipt_ids=args.transfer_ids,
        )
        payload = receipt.to_dict()
    finally:
        conn.close()

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "authority_receipt_id": receipt.authority_receipt_id,
        "eligible": receipt.eligible,
        "gate_status": receipt.gate_status,
        "missing": list(receipt.missing),
        "failed": list(receipt.failed),
        "not_established": list(receipt.not_established),
        "promotion_attempted": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
