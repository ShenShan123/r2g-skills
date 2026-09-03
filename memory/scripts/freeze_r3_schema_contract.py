#!/usr/bin/env python3
"""Freeze or replay the Revision3 P16 TEHM schema contract."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.schema_contract import (  # noqa: E402
    SchemaContractError, freeze_schema_contract, replay_schema_contract,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", type=Path, help="schema.sql (defaults to shipped schema)")
    parser.add_argument("--db", type=Path, help="optional existing TEHM DB to bind read-only")
    parser.add_argument("--output", type=Path, required=True,
                        help="external JSON freeze report")
    parser.add_argument("--replay", action="store_true",
                        help="replay the existing report instead of creating one")
    args = parser.parse_args(argv)
    try:
        if args.replay:
            receipt = replay_schema_contract(args.output, schema_path=args.schema, db_path=args.db)
            result = {"mode": "replay", "receipt_id": receipt.receipt_id,
                      "receipt_digest": receipt.receipt_digest,
                      "db_schema_version": receipt.db_schema_version}
        else:
            report = freeze_schema_contract(schema_path=args.schema, db_path=args.db,
                                            output=args.output)
            result = {"mode": "freeze", "receipt_id": report["receipt_id"],
                      "receipt_digest": report["receipt_digest"],
                      "db_schema_version": report["schema_contract"]["db_schema_version"]}
    except (OSError, SchemaContractError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
