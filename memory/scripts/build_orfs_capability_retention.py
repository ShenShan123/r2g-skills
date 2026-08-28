#!/usr/bin/env python3
"""Audit frozen-policy capability retention on a later real ORFS lineage.

The candidate policy is loaded read-only from the attribution snapshot.  The
replay pair remains an external receipt (never learner support), and the
script never changes canonical memory or capability lifecycle status.
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

from tehm.adapters.orfs_pair import build_orfs_pair_record  # noqa: E402
from tehm.capability.retention import (  # noqa: E402
    evaluate_capability_retention, record_capability_retention,
    verify_capability_retention,
)
from tehm.capability.policy_snapshot import (  # noqa: E402
    validate_policy_load_row, validate_policy_snapshot_row,
)
from tehm.causal.mechanism import action_digest  # noqa: E402
from tehm import db  # noqa: E402


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, default=str).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _load_policy_binding(attribution_report: dict) -> tuple[Path, str, str, str]:
    db_path = Path(attribution_report["derived_db"]).resolve()
    policy = attribution_report.get("candidate_policy") or {}
    policy_id = str(policy.get("policy_snapshot_id") or "")
    policy_digest = str(policy.get("policy_digest") or "")
    capability_id = str((attribution_report.get("capability") or {}).get(
        "capability_id") or "")
    if not db_path.is_file() or not policy_id or not policy_digest or not capability_id:
        raise ValueError("attribution report lacks a complete candidate policy binding")
    return db_path, policy_id, policy_digest, capability_id


def _open_retention_ledger(source_db: Path, ledger_db: Path) -> sqlite3.Connection:
    """Open an isolated writable ledger cloned from the immutable source DB.

    The attribution snapshot is deliberately opened through the immutable
    read-only seam.  A ledger path is an explicit opt-in output and is never
    allowed to alias that source path.  Existing ledgers are reused so a
    rerun cannot overwrite an immutable receipt with a new payload.
    """
    source_db = source_db.resolve()
    ledger_db = ledger_db.resolve()
    if source_db == ledger_db:
        raise ValueError("retention ledger must be separate from attribution DB")
    ledger_db.parent.mkdir(parents=True, exist_ok=True)
    if not ledger_db.exists():
        source = db.connect_read_only(source_db)
        try:
            destination = sqlite3.connect(str(ledger_db))
            try:
                source.backup(destination)
            finally:
                destination.close()
        finally:
            source.close()
    conn = db.connect(ledger_db)
    db.ensure_schema(conn)
    return conn


def build_orfs_capability_retention(
    attribution_report: Path | str,
    *,
    output: Path | str,
    before_project: Path | str,
    after_project: Path | str,
    lineage_id: str,
    config_edits: dict,
    target_check: str = "route",
    retention_ledger_db: Path | str | None = None,
) -> dict:
    report_path = Path(attribution_report).resolve()
    report = json.loads(report_path.read_text())
    db_path, policy_id, policy_digest, capability_id = _load_policy_binding(report)
    training_lineages = set((report.get("firewall") or {}).get(
        "training_lineages") or ())
    heldout_lineages = set((report.get("firewall") or {}).get(
        "heldout_lineages") or ())
    if lineage_id in training_lineages or lineage_id in heldout_lineages:
        raise ValueError("retention lineage overlaps training/held-out firewall")

    # Read-only immutable open proves the snapshot exists and the runtime would
    # load the same digest used by the acquisition campaign.
    runtime_id = ""
    policy_load_receipt_id = ""
    conn = db.connect_read_only(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM tehm_policy_snapshots "
            "WHERE policy_snapshot_id=?", (policy_id,)).fetchone()
        load = conn.execute(
            "SELECT * FROM tehm_policy_load_receipts "
            "WHERE policy_snapshot_id=? ORDER BY created_at DESC, receipt_id DESC LIMIT 1",
            (policy_id,)).fetchone()
        if row is None:
            raise ValueError("candidate policy digest binding is stale or mismatched")
        try:
            snapshot = validate_policy_snapshot_row(row)
        except ValueError as exc:
            raise ValueError("candidate policy snapshot is malformed") from exc
        if snapshot["policy_digest"] != policy_digest:
            raise ValueError("candidate policy digest binding is stale or mismatched")
        if load is None:
            raise ValueError("candidate policy has no successful runtime load receipt")
        try:
            checked_load = validate_policy_load_row(load)
            load_payload = json.loads(checked_load["receipt_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("candidate policy runtime load receipt is malformed") from exc
        if checked_load["loaded"] != 1:
            raise ValueError("candidate policy has no successful runtime load receipt")
        if (not isinstance(load_payload, dict) or
                load_payload.get("policy_snapshot_id") != policy_id or
                load_payload.get("policy_digest") != policy_digest or
                load_payload.get("loaded") is not True):
            raise ValueError("candidate policy runtime load receipt is stale or mismatched")
        runtime_id = str(checked_load["runtime_id"])
        policy_load_receipt_id = str(checked_load["receipt_id"])
    finally:
        conn.close()

    record = build_orfs_pair_record(
        Path(before_project), Path(after_project), lineage_id=lineage_id,
        target_check=target_check, config_edits=config_edits,
        transformation_family="DENSITY_RELIEF")
    delta = record.observation_delta
    before_failed = delta.get("original_failure") == "REMOVED"
    candidate_pass = record.verification.get("verdict") == "PASS"
    no_regression = not list(delta.get("created_regressions") or [])
    evidence_id = _digest({
        "attribution_report_sha256": _sha(report_path),
        "candidate_policy_id": policy_id,
        "candidate_policy_digest": policy_digest,
        "lineage_id": lineage_id,
        "record_id": record.record_id,
        "action_digest": action_digest(record.action),
        "before_failed": before_failed,
        "candidate_pass": candidate_pass,
        "no_regression": no_regression,
    })
    replay = {
        "verdict": "PASS" if before_failed and candidate_pass else "FAIL",
        "disjoint_lineage": lineage_id not in training_lineages | heldout_lineages,
        "non_target_regression_zero": no_regression,
        "evidence_id": evidence_id,
        "split": "heldout",
        "lineage_id": lineage_id,
        "candidate_policy_digest": policy_digest,
        "baseline_failed": before_failed,
        "candidate_pass": candidate_pass,
        "target_check": target_check,
    }
    retention = evaluate_capability_retention(
        capability_id=capability_id,
        replay_id="retention_" + evidence_id.split(":", 1)[1][:20],
        replay=replay)
    ledger_result = None
    if retention_ledger_db is not None:
        ledger_path = Path(retention_ledger_db).resolve()
        ledger = _open_retention_ledger(db_path, ledger_path)
        try:
            ledger_receipt = record_capability_retention(
                ledger, capability_id=capability_id,
                replay_id=retention.replay_id, replay=replay,
                candidate_policy_snapshot_id=policy_id,
                runtime_id=runtime_id,
                policy_load_receipt_id=policy_load_receipt_id)
            ledger_verification = verify_capability_retention(
                ledger, capability_id, ledger_receipt)
            # A successful pure replay must also be consumable as authority
            # evidence.  Failed replays are intentionally recorded as
            # retained=0 audit rows and therefore verify as ineligible.
            if retention.retained and not ledger_verification["eligible"]:
                raise ValueError(
                    "retention ledger verification failed: "
                    + ";".join(ledger_verification["reasons"]))
            ledger_result = {
                "db": str(ledger_path),
                "receipt": ledger_receipt.to_dict(),
                "verification": ledger_verification,
                "authority_eligible": bool(ledger_verification["eligible"]),
            }
        finally:
            ledger.close()
    result = {
        "version": "orfs-capability-retention-v1",
        "attribution_report": str(report_path),
        "attribution_report_sha256": _sha(report_path),
        "candidate_policy": {
            "policy_snapshot_id": policy_id,
            "policy_digest": policy_digest,
            "derived_db": str(db_path),
            "read_only_verified": True,
        },
        "capability_id": capability_id,
        "lineage_id": lineage_id,
        "record": {
            "record_id": record.record_id,
            "before_project": str(Path(before_project).resolve()),
            "after_project": str(Path(after_project).resolve()),
            "before_failed": before_failed,
            "candidate_pass": candidate_pass,
            "created_regressions": list(delta.get("created_regressions") or []),
            "utility_verdict": delta.get("utility_verdict"),
            "evidence_refs": list(record.verification.get("evidence_refs") or []),
        },
        "firewall": {
            "training_lineages": sorted(training_lineages),
            "heldout_lineages": sorted(heldout_lineages),
            "retention_lineage": lineage_id,
            "disjoint": replay["disjoint_lineage"],
            "entered_learner_support": False,
        },
        "replay": replay,
        "retention": retention.to_dict(),
        "retention_ledger": ledger_result,
        "canonical_memory_mutation": "none",
        "promotion_attempted": False,
        "production_promotion_eligible": False,
        "authority_note": (
            "retention is an evaluation-only replay of a frozen candidate "
            "policy; no capability lifecycle or production policy mutation"),
    }
    out = Path(output).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attribution-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--before-project", type=Path, required=True)
    parser.add_argument("--after-project", type=Path, required=True)
    parser.add_argument("--lineage-id", required=True)
    parser.add_argument("--config-json", required=True)
    parser.add_argument("--target-check", default="route")
    parser.add_argument(
        "--retention-ledger-db", type=Path,
        help="optional isolated writable DB for authority-grade retention evidence")
    args = parser.parse_args(argv)
    result = build_orfs_capability_retention(
        args.attribution_report, output=args.output,
        before_project=args.before_project, after_project=args.after_project,
        lineage_id=args.lineage_id, config_edits=json.loads(args.config_json),
        target_check=args.target_check,
        retention_ledger_db=args.retention_ledger_db)
    print(json.dumps({
        "capability_id": result["capability_id"],
        "lineage_id": result["lineage_id"],
        "retained": result["retention"]["retained"],
        "retention_ledger_eligible": bool(
            (result.get("retention_ledger") or {}).get("authority_eligible", False)),
        "production_promotion_eligible": result["production_promotion_eligible"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
