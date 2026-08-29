#!/usr/bin/env python3
"""Replay one DB-bound rule-authority receipt without mutating its store.

The receipt is loaded from the immutable authority ledger, reconstructed from
the stored row, and passed through ``verify_rule_authority``.  This is an
audit/hand-off seam only: it does not change lifecycle status, import
canonical memory, or make a failed/incomplete receipt eligible.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm import db as tehm_db  # noqa: E402
from tehm.ids import stable_dumps  # noqa: E402
from tehm.lifecycle.rule_authority import (  # noqa: E402
    REQUIRED_GATES, verify_rule_authority,
)


def _sha(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


def _stored_bool(value, *, label: str) -> bool:
    """Accept only SQLite's strict integer projection of a boolean."""
    if type(value) is not int or value not in (0, 1):
        raise ValueError(f"{label}_malformed")
    return bool(value)


def _strict_text(value, *, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{label}_malformed")
    return value.strip()


def _strict_status_version(value) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value <= 0:
        raise ValueError("status_version_malformed")
    return value


def _load_row(conn: sqlite3.Connection, receipt_id: str) -> tuple[dict, dict]:
    """Load and type-check a receipt row before invoking semantic replay."""
    requested = _strict_text(receipt_id, label="authority_receipt_id")
    row = conn.execute(
        """SELECT authority_receipt_id, rule_id, target_scope,
                         status_version, eligible, receipt_json, receipt_digest
                    FROM tehm_rule_authority_receipts
                   WHERE authority_receipt_id=?""", (requested,)).fetchone()
    if row is None:
        raise ValueError("authority_receipt_row_missing")
    authority_id = _strict_text(
        row["authority_receipt_id"], label="authority_receipt_id")
    rule_id = _strict_text(row["rule_id"], label="rule_id")
    target_scope = _strict_text(row["target_scope"], label="target_scope")
    status_version = _strict_status_version(row["status_version"])
    eligible = _stored_bool(row["eligible"], label="eligible")
    digest = _strict_text(row["receipt_digest"], label="receipt_digest")
    raw_json = row["receipt_json"]
    if type(raw_json) is not str or not raw_json.strip():
        raise ValueError("authority_receipt_json_malformed")
    try:
        payload = json.loads(raw_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("authority_receipt_json_malformed") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("authority_receipt_payload_not_mapping")
    payload = dict(payload)
    if stable_dumps(payload) != raw_json:
        raise ValueError("authority_receipt_json_not_canonical")
    data = {
        **payload,
        "authority_receipt_id": authority_id,
        "receipt_digest": digest,
        "rule_id": rule_id,
        "target_scope": target_scope,
        "status_version": status_version,
        "eligible": eligible,
        "payload": payload,
    }
    metadata = {
        "authority_receipt_id": authority_id,
        "rule_id": rule_id,
        "target_scope": target_scope,
        "status_version": status_version,
        "eligible_stored": eligible,
        "receipt_digest": digest,
    }
    return data, metadata


def replay(database: Path, *, authority_receipt_id: str) -> dict:
    """Replay an authority receipt and prove the input DB was unchanged."""
    database = Path(database).resolve()
    before_sha = _sha(database)
    checked = None
    metadata = {"authority_receipt_id": authority_receipt_id}
    errors: list[str] = []
    try:
        conn = tehm_db.connect_read_only(database)
        try:
            data, metadata = _load_row(conn, authority_receipt_id)
            checked = verify_rule_authority(conn, data)
        finally:
            conn.close()
    except (FileNotFoundError, RuntimeError, sqlite3.Error, TypeError, ValueError) as exc:
        errors.append(str(exc))

    if checked is None:
        # A missing or malformed receipt is an authority-input failure; it is
        # not evidence that each individual gate was measured and failed.
        gate_status = {gate: "NOT_ESTABLISHED" for gate in REQUIRED_GATES}
        report = {
            "eligible": False,
            "gate_status": gate_status,
            "all_gates_established": False,
            "authority_replay_status": "FAIL",
            "reasons": sorted(set(errors)),
        }
    else:
        gate_report = checked.get("gate_report") or {}
        gate_status = dict(gate_report.get("gate_status") or {
            gate: "NOT_ESTABLISHED" for gate in REQUIRED_GATES
        })
        report = {
            "eligible": checked.get("eligible") is True,
            "gate_status": gate_status,
            "all_gates_established": all(
                gate_status.get(gate) == "PASS" for gate in REQUIRED_GATES),
            "authority_replay_status": (
                "PASS" if checked.get("eligible") is True else "FAIL"),
            "missing": list(gate_report.get("missing") or []),
            "failed": list(gate_report.get("failed") or []),
            "not_established": list(gate_report.get("not_established") or []),
            "reasons": sorted(set(checked.get("reasons") or [])),
            "checks": dict(checked.get("checks") or {}),
        }

    after_sha = _sha(database)
    unchanged = before_sha == after_sha
    if not unchanged:
        report["reasons"] = sorted(set(report["reasons"] +
                                       ["authority_database_changed_during_replay"]))
        report["eligible"] = False
    report.update({
        "version": "tehm-rule-authority-replay-v1",
        "authority_database": str(database),
        "authority_database_sha256_before": before_sha,
        "authority_database_sha256_after": after_sha,
        "database_unchanged": unchanged,
        "receipt": metadata,
        "decision": ("ALLOW_AUTHORITY_REVIEW" if report["eligible"]
                      else "DENY_CANONICAL_IMPORT"),
        "promotion_attempted": False,
        "canonical_memory_mutation": "none",
        "read_only": True,
    })
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authority-db", type=Path, required=True,
                        help="immutable/read-only TEHM authority database")
    parser.add_argument("--authority-receipt-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = replay(args.authority_db, authority_receipt_id=args.authority_receipt_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "eligible": report["eligible"],
        "gate_status": report["gate_status"],
        "decision": report["decision"],
        "database_unchanged": report["database_unchanged"],
        "promotion_attempted": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
