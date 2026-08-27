"""Database-bound Asset Memory authority receipts.

The original asset gate helper is intentionally pure and remains useful for
shadow analysis.  This module adds the strict lifecycle seam: it binds that
analysis to the currently stored asset content digest and carries the exact
validation/binding/rollback evidence needed for an independent recheck.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
import sqlite3

from tehm import db as tehm_db
from tehm.ids import stable_dumps

from .lifecycle import evaluate_asset_authority
from .receipts import AssetAuthorityReceipt
from .registry import asset_content_digest, get_asset, get_asset_status, set_asset_status


AUTHORITY_VERSION = "asset-promotion-authority-v1"
ASSET_EVIDENCE_TYPES = {
    "validation": "asset_validation",
    "binding": "asset_binding",
    "rollback": "asset_rollback",
}
EVIDENCE_SPLITS = frozenset({"training", "calibration", "heldout", "ab"})


def _ensure_asset_authority_schema(conn: sqlite3.Connection) -> None:
    """Create the additive authority ledger used by v4 asset stores.

    Asset authority was introduced after the v4 schema was frozen.  Keeping
    this small extension idempotent lets old v4 databases opt into the ledger
    without rewriting the shipped migration chain; fresh stores also include
    the same objects in ``schema.sql``.
    """
    # Do not use ``executescript`` here: sqlite3 implicitly commits before an
    # executescript call, which could commit an unrelated outer transaction
    # while an authority receipt is only half-built.  Individual DDL
    # statements participate in the caller's transaction/savepoint.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tehm_asset_authority_evidence (
            asset_id       TEXT NOT NULL,
            target_scope   TEXT NOT NULL,
            evidence_type  TEXT NOT NULL,
            evidence_id    TEXT NOT NULL,
            split          TEXT NOT NULL CHECK (split IN
                           ('training', 'calibration', 'heldout', 'ab')),
            lineage_id     TEXT,
            verdict        TEXT NOT NULL,
            payload_json   TEXT NOT NULL,
            evidence_digest TEXT NOT NULL,
            PRIMARY KEY (asset_id, target_scope, evidence_type, evidence_id)
        )""")
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_asset_authority_evidence_scope
            ON tehm_asset_authority_evidence(asset_id, target_scope, split, verdict)""")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tehm_asset_authority_receipts (
            authority_receipt_id TEXT PRIMARY KEY,
            asset_id             TEXT NOT NULL,
            target_scope         TEXT NOT NULL,
            eligible             INTEGER NOT NULL CHECK (eligible IN (0, 1)),
            receipt_json         TEXT NOT NULL,
            receipt_digest       TEXT NOT NULL UNIQUE,
            created_at           TEXT NOT NULL
        )""")
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_asset_authority_receipts_scope
            ON tehm_asset_authority_receipts(asset_id, target_scope, eligible)""")


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _as_dict(value) -> dict:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        raise TypeError("asset authority receipt must be a mapping")
    return dict(value)


def _mappings(values) -> list[dict]:
    try:
        return [dict(value) for value in (values or ())
                if isinstance(value, Mapping)]
    except TypeError:
        return []


def _receipt_digest(payload: Mapping) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(dict(payload)).encode()).hexdigest()


def _receipt_id(receipt_digest: str) -> str:
    return "asset_authority_" + receipt_digest.split(":", 1)[1][:20]


def _evidence_id(*, asset_id: str, target_scope: str, evidence_type: str,
                 payload: Mapping, split: str, lineage_id, source_id: str,
                 ordinal: int) -> str:
    # Source/lineage makes repeated identical receipts from different
    # projects distinct; ordinal is only a fallback for legacy unlabelled
    # lists and therefore does not become a semantic claim.
    seed = {
        "asset_id": asset_id, "target_scope": target_scope,
        "evidence_type": evidence_type, "payload": dict(payload),
        "split": split, "lineage_id": lineage_id, "source_id": source_id,
        "ordinal": ordinal if not (source_id or lineage_id) else None,
    }
    return "asset_evidence_" + hashlib.sha256(
        stable_dumps(seed).encode()).hexdigest()[:24]


def _evidence_digest(*, asset_id: str, target_scope: str,
                     evidence_type: str, evidence_id: str, split: str,
                     lineage_id, verdict: str, payload: Mapping,
                     source_id: str = "") -> str:
    value = {
        "asset_id": asset_id, "target_scope": target_scope,
        "evidence_type": evidence_type, "evidence_id": evidence_id,
        "split": split, "lineage_id": lineage_id, "verdict": verdict,
        "source_id": source_id, "payload": dict(payload),
    }
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _normalise_entries(values, *, kind: str, default_split: str = "training"):
    """Return payload-bearing entries while preserving split/lineage metadata."""
    entries: list[dict] = []
    malformed = False
    if values is None:
        return entries, malformed
    if isinstance(values, (str, bytes)):
        return entries, True
    try:
        iterator = iter(values)
    except TypeError:
        return entries, True
    payload_key = "asset" if kind == "binding" else "receipt"
    for raw in iterator:
        if not isinstance(raw, Mapping):
            malformed = True
            continue
        wrapped = raw.get(payload_key)
        if isinstance(wrapped, Mapping):
            payload = dict(wrapped)
            split = str(raw.get("split") or default_split)
            lineage_id = raw.get("lineage_id")
            source_id = str(raw.get("source_id") or raw.get("project") or "")
        else:
            payload = dict(raw)
            split = default_split
            lineage_id = None
            source_id = ""
        if split not in EVIDENCE_SPLITS:
            malformed = True
            split = default_split if default_split in EVIDENCE_SPLITS else "ab"
        entries.append({
            "payload": payload, "split": split, "lineage_id": lineage_id,
            "source_id": source_id,
        })
    return entries, malformed


def _validation_verdict(payload: Mapping) -> str:
    return "PASS" if (
        payload.get("static_valid") is True and
        payload.get("independent_verifier") is True and
        payload.get("oracle_verdict") == "PASS" and
        payload.get("regression_verdict") == "PASS" and
        not payload.get("errors")
    ) else "FAIL"


def _binding_verdict(payload: Mapping, asset: Mapping) -> str:
    from .lifecycle import _binding_is_compatible
    return "PASS" if _binding_is_compatible(payload, asset) else "FAIL"


def _row_ref(*, asset_id: str, target_scope: str, kind: str,
             entry: Mapping, asset: Mapping, ordinal: int) -> dict:
    evidence_type = ASSET_EVIDENCE_TYPES[kind]
    payload = entry["payload"]
    if kind == "validation":
        verdict = _validation_verdict(payload)
    elif kind == "binding":
        verdict = _binding_verdict(payload, asset)
    else:
        verdict = "PASS" if payload.get("verified") is True else "FAIL"
    evidence_id = _evidence_id(
        asset_id=asset_id, target_scope=target_scope,
        evidence_type=evidence_type, payload=payload,
        split=entry["split"], lineage_id=entry.get("lineage_id"),
        source_id=str(entry.get("source_id") or ""), ordinal=ordinal)
    digest = _evidence_digest(
        asset_id=asset_id, target_scope=target_scope,
        evidence_type=evidence_type, evidence_id=evidence_id,
        split=entry["split"], lineage_id=entry.get("lineage_id"),
        verdict=verdict, payload=payload,
        source_id=str(entry.get("source_id") or ""))
    return {
        "evidence_type": evidence_type, "evidence_id": evidence_id,
        "split": entry["split"], "lineage_id": entry.get("lineage_id"),
        "verdict": verdict, "evidence_digest": digest,
        "source_id": str(entry.get("source_id") or ""),
        "payload": payload,
    }


def _insert_evidence_row(conn: sqlite3.Connection, *, asset_id: str,
                         target_scope: str, ref: Mapping) -> None:
    payload_json = stable_dumps(ref["payload"])
    existing = conn.execute(
        """SELECT split, lineage_id, verdict, payload_json, evidence_digest
             FROM tehm_asset_authority_evidence
            WHERE asset_id=? AND target_scope=? AND evidence_type=? AND evidence_id=?""",
        (asset_id, target_scope, ref["evidence_type"], ref["evidence_id"]),
    ).fetchone()
    values = (ref["split"], ref.get("lineage_id"), ref["verdict"],
              payload_json, ref["evidence_digest"])
    if existing is not None:
        if tuple(existing) != values:
            raise ValueError("asset authority evidence is immutable and conflicts")
        return
    conn.execute(
        """INSERT INTO tehm_asset_authority_evidence
           (asset_id, target_scope, evidence_type, evidence_id, split,
            lineage_id, verdict, payload_json, evidence_digest)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (asset_id, target_scope, ref["evidence_type"], ref["evidence_id"],
         *values))


def _receipt_payload(data: Mapping) -> dict:
    return {
        "authority_version": data.get("authority_version"),
        "asset_id": data.get("asset_id"),
        "target_scope": data.get("target_scope"),
        "asset_content_digest": data.get("asset_content_digest"),
        "eligible": data.get("eligible"),
        "checks": data.get("checks") or {},
        "missing": data.get("missing") or [],
        "evidence": data.get("evidence") or {},
    }


def _build_receipt(
    *, asset_id: str, target_scope: str, asset_digest: str,
    eligible: bool, checks: Mapping, missing, evidence: Mapping,
) -> AssetAuthorityReceipt:
    payload = {
        "authority_version": AUTHORITY_VERSION,
        "asset_id": asset_id,
        "target_scope": target_scope,
        "asset_content_digest": asset_digest,
        "eligible": bool(eligible),
        "checks": dict(checks),
        "missing": list(missing),
        "evidence": dict(evidence),
    }
    digest = _receipt_digest(payload)
    return AssetAuthorityReceipt(
        asset_id=asset_id, target_scope=target_scope,
        authority_version=AUTHORITY_VERSION,
        asset_content_digest=asset_digest, eligible=bool(eligible),
        checks=dict(checks), missing=tuple(missing), evidence=dict(evidence),
        authority_receipt_id=_receipt_id(digest), receipt_digest=digest)


def _persist_asset_authority_rows(
    conn: sqlite3.Connection,
    *,
    asset_id: str,
    target_scope: str,
    asset: Mapping,
    validation_entries: list[dict],
    binding_entries: list[dict],
    rollback_entries: list[dict],
    validation_malformed: bool,
    binding_malformed: bool,
    rollback_malformed: bool,
    evidence: dict,
    stored_digest: str,
    computed_digest: str | None,
    eligible: bool,
    checks: Mapping,
    missing: list[str],
) -> AssetAuthorityReceipt:
    """Persist one complete authority ledger unit.

    The caller owns a savepoint around this helper.  Keeping all evidence rows
    and the receipt row in one unit prevents a late immutable-row conflict from
    leaving a misleading partial authority record.
    """
    refs: dict[str, list[dict]] = {"validation": [], "binding": [],
                                   "rollback": []}
    for ordinal, entry in enumerate(validation_entries):
        ref = _row_ref(asset_id=asset_id, target_scope=target_scope,
                       kind="validation", entry=entry, asset=asset,
                       ordinal=ordinal)
        refs["validation"].append({key: value for key, value in ref.items()
                                    if key != "payload"})
        _insert_evidence_row(conn, asset_id=asset_id, target_scope=target_scope,
                             ref=ref)
    for ordinal, entry in enumerate(binding_entries):
        ref = _row_ref(asset_id=asset_id, target_scope=target_scope,
                       kind="binding", entry=entry, asset=asset,
                       ordinal=ordinal)
        refs["binding"].append({key: value for key, value in ref.items()
                                 if key != "payload"})
        _insert_evidence_row(conn, asset_id=asset_id, target_scope=target_scope,
                             ref=ref)
    for ordinal, entry in enumerate(rollback_entries):
        ref = _row_ref(asset_id=asset_id, target_scope=target_scope,
                       kind="rollback", entry=entry, asset=asset,
                       ordinal=ordinal)
        refs["rollback"].append({key: value for key, value in ref.items()
                                  if key != "payload"})
        _insert_evidence_row(conn, asset_id=asset_id, target_scope=target_scope,
                             ref=ref)
    evidence["evidence_refs"] = refs
    if validation_malformed or binding_malformed or rollback_malformed:
        evidence["evidence_errors"] = [
            name for name, value in (
                ("validation", validation_malformed),
                ("binding", binding_malformed),
                ("rollback", rollback_malformed),
            ) if value
        ]
    receipt = _build_receipt(
        asset_id=asset_id, target_scope=target_scope,
        asset_digest=stored_digest or (computed_digest or ""),
        eligible=eligible, checks=checks, missing=tuple(sorted(set(missing))),
        evidence=evidence)
    receipt_data = receipt.to_dict()
    receipt_payload = _receipt_payload(receipt_data)
    receipt_json = stable_dumps(receipt_payload)
    existing = conn.execute(
        """SELECT asset_id, target_scope, eligible, receipt_json, receipt_digest
             FROM tehm_asset_authority_receipts
            WHERE authority_receipt_id=?""",
        (receipt.authority_receipt_id,)).fetchone()
    receipt_values = (asset_id, target_scope, int(receipt.eligible), receipt_json,
                     receipt.receipt_digest)
    if existing is not None:
        if tuple(existing) != receipt_values:
            raise ValueError("asset authority receipt is immutable and conflicts")
    else:
        conn.execute(
            """INSERT INTO tehm_asset_authority_receipts
               (authority_receipt_id, asset_id, target_scope, eligible,
                receipt_json, receipt_digest, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (receipt.authority_receipt_id, *receipt_values,
             tehm_db.now_local()))
    return receipt


def record_asset_authority(
    conn: sqlite3.Connection,
    *,
    asset_id: str,
    target_scope: str,
    validation_receipts,
    bindings,
    rollback_receipt,
    min_lineages: int = 2,
) -> AssetAuthorityReceipt:
    """Create an authority receipt bound to the current DB asset content."""
    if min_lineages < 1:
        raise ValueError("min_lineages must be positive")
    asset = get_asset(conn, asset_id)
    if asset is None:
        raise ValueError(f"unknown asset: {asset_id}")
    _ensure_asset_authority_schema(conn)
    stored_digest = str(asset.get("content_digest") or "")
    computed_digest = asset_content_digest(asset)
    validation_entries, validation_malformed = _normalise_entries(
        validation_receipts, kind="validation")
    binding_entries, binding_malformed = _normalise_entries(
        bindings, kind="binding")
    rollback_entries, rollback_malformed = _normalise_entries(
        [rollback_receipt] if isinstance(rollback_receipt, Mapping) else
        rollback_receipt, kind="rollback", default_split="ab")
    validations = [entry["payload"] for entry in validation_entries]
    bound_assets = [entry["payload"] for entry in binding_entries]
    rollback_payload = (rollback_entries[0]["payload"]
                        if rollback_entries else {})
    evidence = {
        "validation_receipts": validations,
        "bindings": bound_assets,
        "rollback_receipt": rollback_payload,
        "min_lineages": int(min_lineages),
    }
    derived = evaluate_asset_authority(
        asset, validation_receipts=validations, bindings=bound_assets,
        rollback_receipt=rollback_payload, target_scope=target_scope,
        min_lineages=min_lineages)
    checks = dict(derived.checks)
    missing = list(derived.missing)
    eligible = bool(derived.eligible and computed_digest and
                    stored_digest == computed_digest)
    if (validation_malformed or binding_malformed or rollback_malformed or
            len(rollback_entries) != 1):
        missing.append("asset_authority_evidence_malformed")
        eligible = False
    if not computed_digest or stored_digest != computed_digest:
        missing.append("asset_content_digest")
        eligible = False
    evidence["derived_evidence"] = dict(derived.evidence)
    # Evidence rows and the receipt row must be committed together.  The
    # schema extension above uses individual DDL (never executescript), so it
    # cannot commit an unrelated outer transaction before this savepoint.
    had_outer_transaction = conn.in_transaction
    savepoint = "tehm_asset_authority_v1"
    conn.execute(f"SAVEPOINT {savepoint}")
    savepoint_active = True
    try:
        receipt = _persist_asset_authority_rows(
            conn, asset_id=asset_id, target_scope=target_scope, asset=asset,
            validation_entries=validation_entries,
            binding_entries=binding_entries, rollback_entries=rollback_entries,
            validation_malformed=validation_malformed,
            binding_malformed=binding_malformed,
            rollback_malformed=rollback_malformed, evidence=evidence,
            stored_digest=stored_digest, computed_digest=computed_digest,
            eligible=eligible, checks=checks, missing=missing)
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        savepoint_active = False
        if not had_outer_transaction:
            conn.commit()
        return receipt
    except Exception:
        if savepoint_active:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise


def verify_asset_authority(conn: sqlite3.Connection, authority_receipt) -> dict:
    """Verify receipt bytes, immutable evidence rows, and the current asset."""
    try:
        data = _as_dict(authority_receipt)
    except TypeError as exc:
        return {"eligible": False, "reasons": [str(exc)]}
    payload = _receipt_payload(data)
    reported_checks = (dict(payload["checks"])
                       if isinstance(payload["checks"], Mapping) else {})
    reasons: list[str] = []
    expected_digest = _receipt_digest(payload)
    if data.get("authority_version") != AUTHORITY_VERSION:
        reasons.append("authority_version_mismatch")
    if data.get("receipt_digest") != expected_digest:
        reasons.append("authority_receipt_digest_mismatch")
    if data.get("authority_receipt_id") != _receipt_id(expected_digest):
        reasons.append("authority_receipt_id_mismatch")
    asset_id = str(data.get("asset_id") or "")
    asset = get_asset(conn, asset_id) if asset_id else None
    if asset is None:
        reasons.append("asset_missing_or_malformed")
    else:
        stored_digest = str(asset.get("content_digest") or "")
        computed_digest = asset_content_digest(asset)
        if not computed_digest or stored_digest != computed_digest:
            reasons.append("asset_content_digest_mismatch")
        if payload["asset_content_digest"] != stored_digest:
            reasons.append("authority_asset_digest_mismatch")
        checks = payload["checks"]
        missing = payload["missing"]
        if not isinstance(checks, Mapping) or not isinstance(missing, list):
            reasons.append("authority_gate_payload_malformed")
            checks = {}
            missing = []
        evidence = payload["evidence"]
        if not isinstance(evidence, Mapping):
            reasons.append("authority_evidence_missing")
        else:
            # Every authority receipt must point at rows in the append-only
            # ledger.  The full payload is retained in the receipt for audit,
            # but verification derives the gates from these rows so a caller
            # cannot replace a validation/binding after recording it.
            refs = evidence.get("evidence_refs")
            loaded: dict[str, list[dict]] = {
                "validation": [], "binding": [], "rollback": []}
            if not _table_exists(conn, "tehm_asset_authority_evidence"):
                reasons.append("asset_evidence_ledger_missing")
                refs = {}
            if not isinstance(refs, Mapping):
                reasons.append("authority_evidence_refs_missing")
                refs = {}
            for kind, expected_type in ASSET_EVIDENCE_TYPES.items():
                raw_refs = refs.get(kind, [])
                if not isinstance(raw_refs, list):
                    reasons.append(f"authority_{kind}_refs_malformed")
                    continue
                for ref in raw_refs:
                    if not isinstance(ref, Mapping):
                        reasons.append(f"evidence:{kind}:ref_malformed")
                        continue
                    evidence_id = str(ref.get("evidence_id") or "")
                    split = str(ref.get("split") or "")
                    lineage_id = ref.get("lineage_id")
                    verdict = str(ref.get("verdict") or "")
                    if (not evidence_id or ref.get("evidence_type") != expected_type or
                            split not in EVIDENCE_SPLITS or not verdict):
                        reasons.append(f"evidence:{kind}:ref_malformed")
                        continue
                    row = conn.execute(
                        """SELECT split, lineage_id, verdict, payload_json,
                                  evidence_digest
                             FROM tehm_asset_authority_evidence
                            WHERE asset_id=? AND target_scope=?
                              AND evidence_type=? AND evidence_id=?""",
                        (asset_id, str(payload["target_scope"] or ""),
                         expected_type, evidence_id),
                    ).fetchone()
                    if row is None:
                        reasons.append(f"evidence:{kind}:row_missing")
                        continue
                    try:
                        row_payload = json.loads(row["payload_json"] or "{}")
                    except (TypeError, json.JSONDecodeError):
                        row_payload = None
                    if not isinstance(row_payload, Mapping):
                        reasons.append(f"evidence:{kind}:payload_malformed")
                        continue
                    if (row["split"], row["lineage_id"], row["verdict"]) != (
                            split, lineage_id, verdict):
                        reasons.append(f"evidence:{kind}:row_mismatch")
                    recomputed = _evidence_digest(
                        asset_id=asset_id,
                        target_scope=str(payload["target_scope"] or ""),
                        evidence_type=expected_type, evidence_id=evidence_id,
                        split=split, lineage_id=lineage_id, verdict=verdict,
                        payload=row_payload,
                        source_id=str(ref.get("source_id") or ""))
                    if (row["evidence_digest"] != recomputed or
                            ref.get("evidence_digest") != recomputed):
                        reasons.append(f"evidence:{kind}:digest_mismatch")
                    loaded[kind].append(dict(row_payload))
            if stable_dumps(evidence.get("validation_receipts") or []) != stable_dumps(
                    loaded["validation"]):
                reasons.append("authority_validation_payload_mismatch")
            if stable_dumps(evidence.get("bindings") or []) != stable_dumps(
                    loaded["binding"]):
                reasons.append("authority_binding_payload_mismatch")
            if stable_dumps(evidence.get("rollback_receipt") or {}) != stable_dumps(
                    loaded["rollback"][0] if loaded["rollback"] else {}):
                reasons.append("authority_rollback_payload_mismatch")
            try:
                min_lineages = int(evidence.get("min_lineages") or 1)
                derived = evaluate_asset_authority(
                    asset,
                    validation_receipts=loaded["validation"],
                    bindings=loaded["binding"],
                    rollback_receipt=(loaded["rollback"][0]
                                      if loaded["rollback"] else {}),
                    target_scope=str(payload["target_scope"] or ""),
                    min_lineages=min_lineages)
            except (TypeError, ValueError):
                derived = None
                reasons.append("authority_evidence_malformed")
            if derived is not None and dict(checks) != dict(derived.checks):
                reasons.append("authority_checks_mismatch")
            if derived is not None and tuple(sorted(missing)) != tuple(sorted(derived.missing)):
                # A content-digest failure is handled separately above; the
                # derived gate list itself must still be exact.
                if not ("asset_content_digest" in missing and
                        tuple(sorted(missing)) == tuple(sorted(
                            (*derived.missing, "asset_content_digest")))):
                    reasons.append("authority_missing_gates_mismatch")
            if payload["eligible"] is not True or derived is None or not derived.eligible:
                reasons.append("authority_receipt_not_eligible")
    if _table_exists(conn, "tehm_asset_authority_receipts"):
        receipt_row = conn.execute(
            """SELECT asset_id, target_scope, eligible, receipt_json,
                      receipt_digest
                 FROM tehm_asset_authority_receipts
                WHERE authority_receipt_id=?""",
            (data.get("authority_receipt_id"),),
        ).fetchone()
        if receipt_row is None:
            reasons.append("authority_receipt_row_missing")
        else:
            try:
                stored_payload = json.loads(receipt_row["receipt_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                stored_payload = None
            if not isinstance(stored_payload, Mapping):
                reasons.append("authority_receipt_row_malformed")
            elif stable_dumps(dict(stored_payload)) != stable_dumps(payload):
                reasons.append("authority_receipt_row_mismatch")
            if (receipt_row["asset_id"], receipt_row["target_scope"],
                    bool(receipt_row["eligible"]), receipt_row["receipt_digest"]) != (
                        asset_id, str(payload["target_scope"] or ""),
                        data.get("eligible") is True, expected_digest):
                reasons.append("authority_receipt_row_digest_mismatch")
    else:
        reasons.append("authority_receipt_ledger_missing")
    return {
        "eligible": not reasons,
        "reasons": sorted(set(reasons)),
        "checks": reported_checks,
        "asset_id": asset_id,
        "target_scope": str(payload["target_scope"] or ""),
        "evidence_verified": not any(reason.startswith("evidence:") or
                                      reason.startswith("authority_") and
                                      "evidence" in reason
                                      for reason in reasons),
    }


def promote_asset(conn: sqlite3.Connection, authority_receipt,
                  *, provenance: Mapping | None = None):
    """Promote only through a verified, content-bound authority receipt."""
    data = _as_dict(authority_receipt)
    check = verify_asset_authority(conn, data)
    if not check["eligible"]:
        raise ValueError(
            "asset authority receipt is not eligible: "
            f"{check['reasons']}")
    status = get_asset_status(
        conn, asset_id=check["asset_id"], target_scope=check["target_scope"])
    if status is None:
        raise ValueError("asset status scope is not registered")
    if status["status"] != "candidate":
        raise ValueError(f"asset status {status['status']!r} is not promotable")
    return set_asset_status(
        conn, asset_id=check["asset_id"], target_scope=check["target_scope"],
        status="promoted", gates=check["checks"],
        provenance={"authority_receipt": data, **dict(provenance or {})},
        authority_receipt=data, strict_asset_authority=True)


__all__ = [
    "AUTHORITY_VERSION", "AssetAuthorityReceipt", "promote_asset",
    "record_asset_authority", "verify_asset_authority",
]
