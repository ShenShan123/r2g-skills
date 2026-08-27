"""Content-addressed Asset Memory registry.

Assets are definitions plus contracts.  Their lifecycle is independent from
rule lifecycle and is always explicit; registration never grants runtime
authority.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping

from tehm import db as tehm_db
from tehm.ids import stable_dumps

from .receipts import AssetReceipt
from .schema import (
    ASSET_STATUS_TRANSITIONS, validate_asset_status, validate_asset_type,
    validate_contract, validate_independent_verifier,
)

ASSET_REGISTRY_VERSION = "asset-registry-v0.1"


def _content_digest(content: dict) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(content).encode()).hexdigest()


def asset_content_digest(asset: Mapping) -> str | None:
    """Recompute the immutable asset digest from a loaded registry object."""
    if not isinstance(asset, Mapping):
        return None
    keys = (
        "asset_type", "name", "version", "definition", "input_contract",
        "output_contract", "verifier_contract", "compatibility",
    )
    if any(key not in asset for key in keys):
        return None
    return _content_digest({key: asset[key] for key in keys})


def _default_scope(compatibility: dict) -> str:
    return str(compatibility.get("target_scope") or
               compatibility.get("compatibility_profile") or
               compatibility.get("profile") or "global")


def register_asset(
    conn: sqlite3.Connection,
    *,
    asset_type: str,
    name: str,
    version: str,
    definition: dict,
    input_contract: dict,
    output_contract: dict,
    verifier_contract: dict,
    compatibility: dict,
    provenance: dict | None = None,
    target_scope: str | None = None,
    commit: bool = True,
) -> AssetReceipt:
    asset_type = validate_asset_type(asset_type)
    if not name or not version:
        raise ValueError("asset name and version are required")
    definition = validate_contract("definition", definition)
    input_contract = validate_contract("input_contract", input_contract)
    output_contract = validate_contract("output_contract", output_contract)
    verifier_contract = validate_contract("verifier_contract", verifier_contract)
    compatibility = validate_contract("compatibility", compatibility)
    provenance = dict(provenance or {})
    validate_independent_verifier(verifier_contract, provenance)
    content = {
        "asset_type": asset_type, "name": name, "version": version,
        "definition": definition, "input_contract": input_contract,
        "output_contract": output_contract,
        "verifier_contract": verifier_contract,
        "compatibility": compatibility,
    }
    digest = _content_digest(content)
    asset_id = "asset_" + digest.split(":", 1)[1][:24]
    scope = str(target_scope or _default_scope(compatibility))
    now = tehm_db.now_local()
    had_outer_transaction = conn.in_transaction
    conn.execute(
        """INSERT OR IGNORE INTO tehm_assets
           (asset_id, asset_type, name, version, definition_json,
            input_contract_json, output_contract_json, verifier_contract_json,
            compatibility_json, provenance_json, content_digest, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (asset_id, asset_type, name, version, stable_dumps(definition),
         stable_dumps(input_contract), stable_dumps(output_contract),
         stable_dumps(verifier_contract), stable_dumps(compatibility),
         stable_dumps({**provenance, "registry_version": ASSET_REGISTRY_VERSION}),
         digest, now))
    stored = conn.execute(
        "SELECT * FROM tehm_assets WHERE asset_id=?", (asset_id,)).fetchone()
    if stored is None:
        raise ValueError("asset was not persisted")
    stored_asset = get_asset(conn, asset_id)
    if stored_asset is None:
        raise ValueError("asset registry content digest mismatch")
    expected_fields = {
        "asset_type": asset_type, "name": name, "version": version,
        "definition_json": stable_dumps(definition),
        "input_contract_json": stable_dumps(input_contract),
        "output_contract_json": stable_dumps(output_contract),
        "verifier_contract_json": stable_dumps(verifier_contract),
        "compatibility_json": stable_dumps(compatibility),
        "content_digest": digest,
    }
    if any(stored[field] != value for field, value in expected_fields.items()):
        raise ValueError("asset is immutable and conflicts")
    conn.execute(
        """INSERT OR IGNORE INTO tehm_asset_status
           (asset_id, target_scope, status, status_version, provenance_json,
            updated_at)
           VALUES (?, ?, 'draft', 1, ?, ?)""",
        (asset_id, scope, stable_dumps({"authority": "asset_registry"}), now))
    if commit and not had_outer_transaction:
        conn.commit()
    return AssetReceipt(asset_id, asset_type, name, version, digest,
                        scope, "draft", 1)


def get_asset(conn: sqlite3.Connection, asset_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM tehm_assets WHERE asset_id=?",
                       (asset_id,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    for key in ("definition_json", "input_contract_json", "output_contract_json",
                "verifier_contract_json", "compatibility_json", "provenance_json"):
        try:
            decoded = json.loads(result[key])
        except (TypeError, json.JSONDecodeError):
            # Registry corruption must not be loaded into an evaluator or
            # promotion authority.  Returning None makes callers fail closed
            # through the existing unknown-asset path.
            return None
        if not isinstance(decoded, dict):
            return None
        result[key[:-5] if key.endswith("_json") else key] = decoded
    computed_digest = asset_content_digest(result)
    expected_id = ("asset_" + computed_digest.split(":", 1)[1][:24]
                   if computed_digest else None)
    if (not computed_digest or result.get("content_digest") != computed_digest or
            result.get("asset_id") != expected_id):
        # A row with valid JSON but a mismatched content digest is still
        # untrusted: status/lifecycle and evaluators must not consume it.
        return None
    return result


def get_asset_status(conn: sqlite3.Connection, *, asset_id: str,
                     target_scope: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM tehm_asset_status WHERE asset_id=? AND target_scope=?",
        (asset_id, target_scope)).fetchone()
    return dict(row) if row else None


def set_asset_status(
    conn: sqlite3.Connection,
    *,
    asset_id: str,
    target_scope: str,
    status: str,
    provenance: dict | None = None,
    gates: dict | None = None,
    authority_receipt=None,
    strict_asset_authority: bool = False,
    commit: bool = True,
) -> AssetReceipt:
    status = validate_asset_status(status)
    asset = get_asset(conn, asset_id)
    if asset is None:
        raise KeyError(f"unknown asset: {asset_id}")
    current = get_asset_status(conn, asset_id=asset_id, target_scope=target_scope)
    if current is None:
        raise ValueError("asset status scope is not registered")
    old_status = current["status"]
    if status != old_status and status not in ASSET_STATUS_TRANSITIONS[old_status]:
        raise ValueError(f"invalid asset status transition {old_status}->{status}")
    if status == "promoted":
        if strict_asset_authority:
            if authority_receipt is None:
                raise ValueError(
                    "strict asset promotion requires an authority receipt")
            from .authority import verify_asset_authority

            authority_data = (authority_receipt.to_dict()
                              if hasattr(authority_receipt, "to_dict")
                              else authority_receipt)
            authority_check = verify_asset_authority(conn, authority_data)
            if not authority_check["eligible"]:
                raise ValueError(
                    "asset authority receipt is not eligible: "
                    f"{authority_check['reasons']}")
            if (authority_check.get("asset_id") != asset_id or
                    authority_check.get("target_scope") != target_scope):
                raise ValueError(
                    "asset authority receipt does not match asset status scope")
            gates = authority_check["checks"]
        else:
            from .lifecycle import evaluate_asset_promotion_gates

            gate_receipt = evaluate_asset_promotion_gates(
                asset, gates or {}, target_scope=target_scope)
            if not gate_receipt.eligible:
                raise ValueError(
                    f"asset promotion gates not satisfied: {gate_receipt.missing}")
    version = int(current["status_version"]) + (status != old_status)
    merged_provenance = dict(provenance or {})
    if gates is not None:
        merged_provenance["promotion_gates"] = dict(gates)
    had_outer_transaction = conn.in_transaction
    conn.execute(
        """UPDATE tehm_asset_status
              SET status=?, status_version=?, provenance_json=?, updated_at=?
            WHERE asset_id=? AND target_scope=?""",
        (status, version, stable_dumps(merged_provenance), tehm_db.now_local(),
         asset_id, target_scope))
    if commit and not had_outer_transaction:
        conn.commit()
    return AssetReceipt(
        asset_id=asset_id, asset_type=asset["asset_type"], name=asset["name"],
        version=asset["version"], content_digest=asset["content_digest"],
        target_scope=target_scope, status=status, status_version=version)


__all__ = ["ASSET_REGISTRY_VERSION", "asset_content_digest", "get_asset",
           "get_asset_status", "register_asset", "set_asset_status"]
