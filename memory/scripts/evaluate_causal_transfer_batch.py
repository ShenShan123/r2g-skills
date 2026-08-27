#!/usr/bin/env python3
"""Evaluate a manifest of held-out causal-transfer cases.

The single-case transfer evaluator is intentionally read-only.  This batch
wrapper keeps that property for the source database, validates all case and
lineage bindings before the first evaluation, and optionally records each
receipt in a newly-created *isolated* shadow ledger database.  A failed case
stays in the denominator; no aggregate result is a promotion receipt.

The manifest is deliberately small and explicit::

    {
      "version": "causal-transfer-batch-v1",
      "cases": [
        {"case_id": "spi-1", "lineage_id": "heldout:spi-1",
         "transition_ids": ["transition_..."]}
      ]
    }

For ORFS, callers must pass ``--require-full-oracle``.  The optional ledger
contains a backup of the source evidence plus only additive transfer-receipt
rows, so it can be handed to a later C6 authority review without granting
canonical or production authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm import db  # noqa: E402
from tehm.batch_lane import canonical_snapshots  # noqa: E402
from tehm.causal import (  # noqa: E402
    evaluate_transfer_supported_mechanism, record_causal_transfer,
    verify_causal_transfer,
)


BATCH_VERSION = "causal-transfer-batch-v1"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_manifest(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read causal-transfer batch manifest: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError("causal-transfer batch manifest must be a JSON object")
    if value.get("version") != BATCH_VERSION:
        raise ValueError("causal-transfer batch manifest version mismatch")
    return value


def _safe_case_id(value: object, ordinal: int) -> str:
    case_id = str(value or f"case-{ordinal}").strip()
    if (not case_id or case_id in {".", ".."} or "/" in case_id or
            "\\" in case_id):
        raise ValueError(f"case_id is not a safe basename: {case_id!r}")
    return case_id


def _ids(raw: object, *, case_id: str) -> tuple[str, ...]:
    if isinstance(raw, (str, bytes)) or raw is None:
        raise ValueError(f"transfer case {case_id} requires transition_ids")
    try:
        values = tuple(str(value).strip() for value in raw)
    except TypeError as exc:
        raise ValueError(
            f"transfer case {case_id} transition_ids must be a sequence") from exc
    if not values or any(not value for value in values):
        raise ValueError(f"transfer case {case_id} transition_ids are malformed")
    if len(set(values)) != len(values):
        raise ValueError(f"transfer case {case_id} contains duplicate transition_ids")
    return tuple(sorted(values))


def validate_manifest(manifest: dict, *, min_transfer_lineages: int) -> list[dict]:
    """Validate the complete batch before opening the source for evaluation."""
    if min_transfer_lineages < 1:
        raise ValueError("min_transfer_lineages must be positive")
    raw_cases = manifest.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("causal-transfer batch requires a non-empty cases list")
    seen_cases: set[str] = set()
    seen_lineages: set[str] = set()
    seen_transitions: set[str] = set()
    cases: list[dict] = []
    for ordinal, raw in enumerate(raw_cases):
        if not isinstance(raw, dict):
            raise ValueError(f"transfer case {ordinal} must be an object")
        case_id = _safe_case_id(raw.get("case_id"), ordinal)
        lineage_id = str(raw.get("lineage_id") or "").strip()
        if not lineage_id:
            raise ValueError(f"transfer case {case_id} lacks lineage_id")
        if case_id in seen_cases:
            raise ValueError(f"duplicate transfer case_id: {case_id}")
        if lineage_id in seen_lineages:
            raise ValueError(f"duplicate transfer lineage_id: {lineage_id}")
        transition_ids = _ids(raw.get("transition_ids"), case_id=case_id)
        overlap = set(transition_ids) & seen_transitions
        if overlap:
            raise ValueError(
                f"transition IDs appear in multiple transfer cases: {sorted(overlap)}")
        seen_cases.add(case_id)
        seen_lineages.add(lineage_id)
        seen_transitions.update(transition_ids)
        cases.append({
            "case_id": case_id,
            "lineage_id": lineage_id,
            "transition_ids": transition_ids,
        })
    return cases


def _backup_database(source: sqlite3.Connection, destination: Path) -> None:
    """Create a new destination from an immutable source without shell copy."""
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(
            f"refusing to overwrite existing transfer ledger DB: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    target = db.connect(destination)
    try:
        source.backup(target)
        target.commit()
    finally:
        target.close()


def _case_result(case: dict, receipt: dict) -> dict:
    observed = set(str(value) for value in receipt.get("transfer_lineages") or ())
    declared = case["lineage_id"]
    return {
        "case_id": case["case_id"],
        "declared_lineage_id": declared,
        "transition_ids": list(case["transition_ids"]),
        "transfer_lineages": sorted(observed),
        # A manifest case is one independent lineage.  Accepting a case that
        # mixes lineages would let the aggregate count a cross-lineage bundle
        # as a single witness and weaken the quota semantics.
        "lineage_binding": observed == {declared},
        "eligible": bool(receipt.get("eligible") is True),
        "evidence_level": receipt.get("evidence_level"),
        "reason": receipt.get("reason"),
        "receipt": receipt,
    }


def evaluate_batch(
    database: Path | str,
    *,
    path_id: str,
    training_campaign_id: str,
    transfer_campaign_id: str | None,
    manifest: Path | str,
    output: Path | str,
    require_full_oracle: bool,
    min_transfer_lineages: int = 1,
    ledger_db: Path | str | None = None,
) -> dict:
    database = Path(database).resolve()
    manifest_path = Path(manifest).resolve()
    output_path = Path(output).resolve()
    if not database.is_file():
        raise FileNotFoundError(f"TEHM database not found: {database}")
    cases = validate_manifest(
        _load_manifest(manifest_path), min_transfer_lineages=min_transfer_lineages)
    transfer_campaign_id = str(transfer_campaign_id or training_campaign_id)
    if not transfer_campaign_id:
        raise ValueError("transfer_campaign_id is required")
    source_before = _sha(database)
    source_conn = db.connect_read_only(database)
    ledger_path = Path(ledger_db).resolve() if ledger_db is not None else None
    if ledger_path is not None and ledger_path == database:
        source_conn.close()
        raise ValueError("ledger_db must be distinct from the read-only source database")

    results: list[dict] = []
    ledger_receipts: list[dict] = []
    ledger_conn: sqlite3.Connection | None = None
    ledger_transaction = False
    try:
        # Resolve path existence and every transfer transition through the same
        # immutable source connection before optional ledger creation.
        for case in cases:
            pure = evaluate_transfer_supported_mechanism(
                source_conn, path_id, case["transition_ids"],
                training_campaign_id=training_campaign_id,
                transfer_campaign_id=transfer_campaign_id,
                require_full_oracle=require_full_oracle)
            results.append(_case_result(case, pure.to_dict()))

        if ledger_path is not None:
            _backup_database(source_conn, ledger_path)
            ledger_conn = db.connect(ledger_path)
            # One outer transaction makes the entire batch ledger atomic.  A
            # failed late receipt cannot leave a misleading partial cohort.
            ledger_conn.execute("BEGIN")
            ledger_transaction = True
            for case in cases:
                receipt = record_causal_transfer(
                    ledger_conn, path_id=path_id,
                    transfer_transition_ids=case["transition_ids"],
                    training_campaign_id=training_campaign_id,
                    transfer_campaign_id=transfer_campaign_id,
                    require_full_oracle=require_full_oracle, commit=False)
                checked = verify_causal_transfer(ledger_conn, receipt)
                if checked.get("verified") is not True:
                    raise RuntimeError(
                        f"ledger replay failed for {case['case_id']}: "
                        f"{checked.get('reasons')}")
                row = _case_result(case, receipt.transfer_receipt)
                row.update({
                    "transfer_receipt_id": receipt.transfer_receipt_id,
                    "ledger_verified": True,
                    "ledger_eligible": bool(checked.get("eligible") is True),
                })
                ledger_receipts.append(row)
            ledger_conn.commit()
            ledger_transaction = False
    except Exception:
        if ledger_conn is not None and ledger_transaction:
            ledger_conn.rollback()
        raise
    finally:
        if ledger_conn is not None:
            ledger_conn.close()
        source_conn.close()

    source_after = _sha(database)
    if source_before != source_after:
        raise RuntimeError("causal transfer batch changed its read-only source database")

    eligible = [row for row in results
                if row["eligible"] and row["lineage_binding"]]
    lineages = sorted({row["declared_lineage_id"] for row in eligible})
    all_eligible = len(eligible) == len(results)
    if not all_eligible:
        batch_status = "FAIL"
        reasons = ["one_or_more_transfer_cases_ineligible"]
    elif len(lineages) < min_transfer_lineages:
        batch_status = "NOT_ESTABLISHED"
        reasons = ["independent_transfer_lineage_quota_not_met"]
    else:
        batch_status = "PASS"
        reasons = []
    if ledger_path is not None:
        if len(ledger_receipts) != len(results):  # pragma: no cover - defensive
            raise RuntimeError("ledger receipt count does not match transfer cases")
        if not all(row.get("ledger_verified") is True for row in ledger_receipts):
            batch_status = "FAIL"
            reasons.append("one_or_more_ledger_receipts_unverified")

    report = {
        "version": BATCH_VERSION,
        "database": str(database),
        "database_sha256": source_before,
        "database_unchanged": True,
        "manifest": str(manifest_path),
        "path_id": path_id,
        "training_campaign_id": training_campaign_id,
        "transfer_campaign_id": transfer_campaign_id,
        "require_full_oracle": bool(require_full_oracle),
        "min_transfer_lineages": int(min_transfer_lineages),
        "cases": results,
        "transfer_lineages": lineages,
        "ledger_db": str(ledger_path) if ledger_path is not None else None,
        "ledger_receipts": ledger_receipts,
        "summary": {
            "case_count": len(results),
            "eligible_count": len(eligible),
            "failed_count": len(results) - len(eligible),
            "independent_lineage_count": len(lineages),
            "batch_status": batch_status,
            "reasons": sorted(set(reasons)),
        },
        "canonical_snapshots": canonical_snapshots(),
        "canonical_memory_mutation": "none",
        "promotion_eligible": False,
        "promotion_attempted": False,
        "read_only_source": True,
        "isolated_ledger_only": ledger_path is not None,
    }
    if ledger_path is not None:
        report["ledger_db_sha256"] = _sha(ledger_path)
        report["ledger_receipt_ids"] = [
            row["transfer_receipt_id"] for row in ledger_receipts]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--path-id", required=True)
    parser.add_argument("--training-campaign-id", required=True)
    parser.add_argument("--transfer-campaign-id")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ledger-db", type=Path, default=None,
                        help="new isolated DB for durable shadow receipts")
    parser.add_argument("--require-full-oracle", action="store_true")
    parser.add_argument("--min-transfer-lineages", type=int, default=1)
    args = parser.parse_args(argv)
    report = evaluate_batch(
        args.database, path_id=args.path_id,
        training_campaign_id=args.training_campaign_id,
        transfer_campaign_id=args.transfer_campaign_id,
        manifest=args.manifest, output=args.output,
        require_full_oracle=args.require_full_oracle,
        min_transfer_lineages=args.min_transfer_lineages,
        ledger_db=args.ledger_db)
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
