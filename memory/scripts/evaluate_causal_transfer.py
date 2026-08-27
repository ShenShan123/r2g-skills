#!/usr/bin/env python3
"""Evaluate a frozen causal path against explicit held-out transitions.

This is an evaluation-only seam.  The database is opened immutable/read-only,
the transfer evaluator performs no persistence, and the report explicitly
cannot be used as a promotion receipt.  ORFS callers should pass
``--require-full-oracle`` so both held-out arms carry the pinned complete
two-arm receipt; generic RTL callers can use the default executable verifier
contract.
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

from tehm import db  # noqa: E402
from tehm.batch_lane import canonical_snapshots  # noqa: E402
from tehm.causal import evaluate_transfer_supported_mechanism  # noqa: E402


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def evaluate(
    database: Path,
    *,
    path_id: str,
    transfer_transition_ids: list[str],
    training_campaign_id: str,
    transfer_campaign_id: str | None,
    require_full_oracle: bool,
) -> dict:
    database = database.resolve()
    if not database.is_file():
        raise FileNotFoundError(f"TEHM database not found: {database}")
    before_sha = _sha(database)
    conn = db.connect_read_only(database)
    try:
        receipt = evaluate_transfer_supported_mechanism(
            conn,
            path_id,
            transfer_transition_ids,
            training_campaign_id=training_campaign_id,
            transfer_campaign_id=transfer_campaign_id,
            require_full_oracle=require_full_oracle,
        )
        report = {
            "version": "tehm-causal-transfer-audit-v1",
            "database": str(database),
            "database_sha256": before_sha,
            "path_id": path_id,
            "training_campaign_id": training_campaign_id,
            "transfer_campaign_id": transfer_campaign_id or training_campaign_id,
            "transfer_transition_ids": [str(item) for item in transfer_transition_ids],
            "require_full_oracle": bool(require_full_oracle),
            "receipt": receipt.to_dict(),
            "eligible": bool(receipt.eligible),
            "promotion_eligible": False,
            "canonical_memory_mutation": "none",
            "read_only": True,
            "canonical_snapshots": canonical_snapshots(),
        }
    finally:
        conn.close()
    after_sha = _sha(database)
    report["database_unchanged"] = before_sha == after_sha
    if not report["database_unchanged"]:
        raise RuntimeError("transfer audit changed its read-only input database")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--path-id", required=True)
    parser.add_argument("--training-campaign-id", required=True)
    parser.add_argument("--transfer-campaign-id")
    parser.add_argument("--transfer-transition-id", action="append", required=True)
    parser.add_argument("--require-full-oracle", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = evaluate(
        args.database,
        path_id=args.path_id,
        transfer_transition_ids=args.transfer_transition_id,
        training_campaign_id=args.training_campaign_id,
        transfer_campaign_id=args.transfer_campaign_id,
        require_full_oracle=args.require_full_oracle,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "eligible": report["eligible"],
        "reason": report["receipt"]["reason"],
        "evidence_level": report["receipt"]["evidence_level"],
        "database_unchanged": report["database_unchanged"],
        "promotion_eligible": report["promotion_eligible"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
