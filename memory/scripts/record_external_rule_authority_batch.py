#!/usr/bin/env python3
"""Record rule authority from a manifest of frozen audit sources.

Each source points at one hash-chained external-observation JSONL and one
closed/checkpointed campaign-local staging DB.  The sources are read-only;
only the authority evidence/receipt ledger in ``--authority-db`` is written.
This command never imports transitions, changes lifecycle status, or promotes
a rule.  Missing gates therefore remain an auditable ``NOT_ESTABLISHED``
attempt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path

MEMORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MEMORY_ROOT))

from tehm import db as tehm_db  # noqa: E402
from tehm.ids import stable_dumps  # noqa: E402
from tehm.lifecycle import (  # noqa: E402
    record_rule_authority_from_external_observation_sources,
)

MANIFEST_VERSION = "external-authority-sources-v1"


def _resolve(path_value, *, base: Path) -> Path:
    if not isinstance(path_value, (str, Path)) or not str(path_value).strip():
        raise ValueError("source path is required")
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _load_sources(path: Path) -> tuple[list[dict], str]:
    path = path.resolve()
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"source manifest unreadable: {path}") from exc
    if not isinstance(raw, Mapping):
        raise ValueError("source manifest must be an object")
    if raw.get("version") != MANIFEST_VERSION:
        raise ValueError("source manifest version mismatch")
    values = raw.get("sources")
    if not isinstance(values, list) or not values:
        raise ValueError("source manifest requires a non-empty sources list")
    sources = []
    for index, value in enumerate(values):
        if not isinstance(value, Mapping):
            raise ValueError(f"source {index} must be an object")
        observations = value.get("observations_path", value.get("observations"))
        staging_db = value.get("staging_db")
        campaign_id = value.get("campaign_id")
        case_ids = value.get("case_ids")
        if not isinstance(campaign_id, str) or not campaign_id.strip():
            raise ValueError(f"source {index} campaign_id is required")
        if (not isinstance(case_ids, list) or not case_ids or
                any(not isinstance(case, str) or not case.strip()
                    for case in case_ids)):
            raise ValueError(f"source {index} case_ids must be non-empty strings")
        sources.append({
            "observations_path": _resolve(observations, base=path.parent),
            "staging_db": _resolve(staging_db, base=path.parent),
            "campaign_id": campaign_id.strip(),
            "case_ids": [case.strip() for case in case_ids],
        })
    # Bind the manifest's exact source selection in the operator output.  The
    # authority receipt independently binds each observation/staging digest.
    manifest_digest = hashlib.sha256(
        stable_dumps(raw).encode()).hexdigest()
    return sources, manifest_digest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authority-db", type=Path, required=True,
                        help="writable TEHM DB containing candidate rule/trial")
    parser.add_argument("--source-manifest", type=Path, required=True,
                        help=f"JSON manifest ({MANIFEST_VERSION})")
    parser.add_argument("--rule-id", required=True)
    parser.add_argument("--target-scope", default="route")
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--status-version", type=int, default=None)
    parser.add_argument("--transfer-receipt-id", dest="transfer_ids",
                        action="append", default=None,
                        help="optional replay-verified L4 receipt (repeatable)")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        sources, manifest_digest = _load_sources(args.source_manifest)
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))

    conn = tehm_db.connect(args.authority_db.resolve())
    tehm_db.ensure_schema(conn)
    try:
        receipt = record_rule_authority_from_external_observation_sources(
            conn,
            rule_id=args.rule_id,
            target_scope=args.target_scope,
            trial_id=args.trial_id,
            expected_status_version=args.status_version,
            sources=sources,
            causal_transfer_receipt_ids=args.transfer_ids,
        )
        payload = receipt.to_dict()
    finally:
        conn.close()

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "version": MANIFEST_VERSION,
        "source_manifest": str(args.source_manifest.resolve()),
        "source_manifest_sha256": manifest_digest,
        "source_count": len(sources),
        "authority_receipt": payload,
        "promotion_attempted": False,
    }, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "authority_receipt_id": receipt.authority_receipt_id,
        "eligible": receipt.eligible,
        "gate_status": receipt.gate_status,
        "missing": list(receipt.missing),
        "failed": list(receipt.failed),
        "not_established": list(receipt.not_established),
        "source_count": len(sources),
        "source_manifest_sha256": manifest_digest,
        "promotion_attempted": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
