"""Independent Asset Memory promotion authority."""
from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Iterable

from .receipts import AssetPromotionReceipt
from .validation import validate_asset_schema

ASSET_PROMOTION_GATES = (
    "schema_valid", "static_valid", "independent_verifier",
    "compatibility_verified", "cross_lineage_verified",
    "regression_zero", "rollback_verified",
)


def _v8_pending(asset, target_scope):
    from .skid_binding_v8 import is_skid_v8_asset
    if not is_skid_v8_asset(asset):
        return None
    return AssetPromotionReceipt(
        asset_id=str(asset.get("asset_id") or ""), target_scope=target_scope,
        eligible=False, checks={name: False for name in ASSET_PROMOTION_GATES},
        missing=ASSET_PROMOTION_GATES,
        evidence={"reason": "v8_requires_strict_raw_train_authority",
                  "lineages": [], "design_ids_are_not_lineages": True})


def _v9_shadow_pending(asset, target_scope):
    """A software-only v9 primitive has no admitted raw TRAIN authority."""
    from tehm.rtl.skid_payload_action_v9 import DOMAIN, PROFILE
    definition = asset.get('definition')
    compatibility = asset.get('compatibility')
    action = definition.get('action') if isinstance(definition, Mapping) else None
    payload = action.get('payload') if isinstance(action, Mapping) else None
    if not ((isinstance(action, Mapping) and action.get('domain') == DOMAIN) or
            (isinstance(payload, Mapping) and payload.get('domain') == DOMAIN) or
            (isinstance(compatibility, Mapping) and
             compatibility.get('compatibility_profile') == PROFILE)):
        return None
    return AssetPromotionReceipt(
        asset_id=str(asset.get('asset_id') or ''), target_scope=target_scope,
        eligible=False, checks={name: False for name in ASSET_PROMOTION_GATES},
        missing=ASSET_PROMOTION_GATES,
        evidence={'reason': 'v9_raw_train_authority_not_implemented',
                  'lineages': [], 'design_ids_are_not_lineages': True})


def evaluate_asset_promotion_gates(
    asset: Mapping,
    gates: Mapping | None,
    *,
    target_scope: str,
) -> AssetPromotionReceipt:
    # Asset rows and receipts may have crossed a process boundary.  Treat
    # malformed input as an ineligible receipt instead of allowing a JSON
    # decoder/attribute error to become an implicit authority path.
    if not isinstance(asset, Mapping):
        return AssetPromotionReceipt(
            asset_id="", target_scope=target_scope, eligible=False,
            checks={name: False for name in ASSET_PROMOTION_GATES},
            missing=ASSET_PROMOTION_GATES,
            evidence={"reason": "asset_not_mapping"})
    pending = _v9_shadow_pending(asset, target_scope) or _v8_pending(asset, target_scope)
    if pending is not None:
        return pending
    source = dict(gates) if isinstance(gates, Mapping) else {}
    checks = {name: source.get(name) is True for name in ASSET_PROMOTION_GATES}
    # The registry contract is an independent hard gate.  A caller cannot
    # override it with a conveniently supplied boolean.
    verifier_contract = asset.get("verifier_contract")
    if isinstance(verifier_contract, str):
        try:
            verifier_contract = json.loads(verifier_contract)
        except (TypeError, json.JSONDecodeError):
            verifier_contract = None
    checks["independent_verifier"] = (
        checks["independent_verifier"] and
        isinstance(verifier_contract, dict) and
        verifier_contract.get("independent") is True)
    provenance = asset.get("provenance") or asset.get("provenance_json") or {}
    if isinstance(provenance, str):
        try:
            provenance = json.loads(provenance)
        except (TypeError, json.JSONDecodeError):
            provenance = None
    if not isinstance(provenance, dict):
        checks["independent_verifier"] = False
    elif provenance.get("generator_is_verifier") is True:
        checks["independent_verifier"] = False
    missing = tuple(name for name in ASSET_PROMOTION_GATES if not checks[name])
    return AssetPromotionReceipt(
        asset_id=str(asset.get("asset_id") or ""), target_scope=target_scope,
        eligible=not missing, checks=checks, missing=missing)


def evaluate_asset_authority(
    asset: Mapping,
    *,
    validation_receipts: Iterable[Mapping],
    bindings: Iterable[Mapping],
    rollback_receipt: Mapping | None,
    target_scope: str,
    min_lineages: int = 2,
) -> AssetPromotionReceipt:
    """Derive asset-promotion gates from independently recorded evidence.

    This is intentionally separate from :func:`evaluate_asset_promotion_gates`,
    which remains the final conjunction checker used by the registry.  The
    helper turns shadow execution receipts into that conjunction without
    accepting caller-supplied booleans.  It is still an audit operation: the
    registry status is not changed and no production runtime is touched.
    """
    if min_lineages < 1:
        raise ValueError("min_lineages must be positive")
    if not isinstance(asset, Mapping):
        return AssetPromotionReceipt(
            asset_id="", target_scope=target_scope, eligible=False,
            checks={name: False for name in ASSET_PROMOTION_GATES},
            missing=ASSET_PROMOTION_GATES,
            evidence={"reason": "asset_not_mapping"})
    from .skid_binding_v8 import is_skid_v8_asset
    pending = _v9_shadow_pending(asset, target_scope)
    if pending is not None:
        return pending
    if is_skid_v8_asset(asset):
        from .r5_train_evidence_v8 import evaluate as evaluate_v8
        return evaluate_v8(asset, validation_receipts=validation_receipts,
                           bindings=bindings, rollback_receipt=rollback_receipt,
                           target_scope=target_scope, min_lineages=min_lineages)
    schema_valid, schema_errors = validate_asset_schema(asset)
    try:
        validations = [dict(item) for item in (validation_receipts or ())
                       if isinstance(item, Mapping)]
    except TypeError:
        validations = []
    try:
        bound_assets = [dict(item) for item in (bindings or ())
                        if isinstance(item, Mapping)]
    except TypeError:
        bound_assets = []
    from .skid_binding_v3 import CONTRACT as SKID_V3_CONTRACT
    from tehm.rtl.skid_payload_action_v3 import (
        DOMAIN as SKID_V3_DOMAIN, PROFILE as SKID_V3_PROFILE)
    from .skid_binding_v4 import is_skid_v4_asset
    from .skid_binding_v5 import is_skid_v5_asset
    from .skid_binding_v6 import is_skid_v6_asset
    from .skid_binding_v7 import is_skid_v7_asset
    definition = asset.get("definition") or {}
    template = definition.get("binding_template") if isinstance(definition, Mapping) else None
    action = definition.get("action") if isinstance(definition, Mapping) else None
    skid_v3 = (
        (isinstance(template, Mapping) and template.get("contract") == SKID_V3_CONTRACT) or
        (isinstance(action, Mapping) and action.get("domain") == SKID_V3_DOMAIN))
    skid_v4 = is_skid_v4_asset(asset)
    design_ids = {
        str(_bound_provenance(item).get("bound_design") or
            _bound_provenance(item).get("bound_project") or "")
        for item in bound_assets
        if _bound_provenance(item).get("bound_design") or
        _bound_provenance(item).get("bound_project")
    }
    # For v3, neither design IDs nor caller-provided verified=True are proof.
    r5_proof = None
    r5_reason = "not_r5_skid_v3"
    if skid_v3:
        from .r5_train_evidence import ROLLBACK_VERSION, verify_train_asset_bundle
        r5_reason = ("audited_train_bundle_missing" if target_scope == SKID_V3_PROFILE
                     else "r5_target_scope_mismatch")
        if (target_scope == SKID_V3_PROFILE and isinstance(rollback_receipt, Mapping) and
                rollback_receipt.get("version") == ROLLBACK_VERSION):
            try:
                r5_proof = verify_train_asset_bundle(
                    asset, validations, bound_assets, rollback_receipt)
                r5_reason = ("audited_train_bundle_verified" if r5_proof.valid else
                             ",".join(r5_proof.reasons))
            except Exception as exc:  # missing or drifting raw evidence fails closed
                r5_reason = "audited_train_bundle_replay_failed:" + type(exc).__name__
    v4_proof = None
    v4_reason = "not_r5_skid_v4"
    if skid_v4:
        from .r5_train_evidence_v4 import ROLLBACK_VERSION as V4_ROLLBACK_VERSION
        from .r5_train_evidence_v4 import verify_train_asset_bundle as verify_v4_bundle
        from tehm.rtl.skid_payload_action_v4 import PROFILE as SKID_V4_PROFILE
        v4_reason = ("audited_v4_train_bundle_missing" if target_scope == SKID_V4_PROFILE
                     else "r5_v4_target_scope_mismatch")
        if (target_scope == SKID_V4_PROFILE and isinstance(rollback_receipt, Mapping) and
                rollback_receipt.get("version") == V4_ROLLBACK_VERSION):
            try:
                v4_proof = verify_v4_bundle(asset, validations, bound_assets, rollback_receipt)
                v4_reason = ("audited_v4_train_bundle_verified" if v4_proof.valid else
                             ",".join(v4_proof.reasons))
            except Exception as exc:
                v4_reason = "audited_v4_train_bundle_replay_failed:" + type(exc).__name__
    v5_proof = None
    v5_reason = "not_r5_skid_v5"
    if is_skid_v5_asset(asset):
        from .r5_train_evidence_v5 import ROLLBACK_VERSION as V5_ROLLBACK_VERSION
        from .r5_train_evidence_v5 import verify_train_asset_bundle as verify_v5_bundle
        from tehm.rtl.skid_payload_action_v5 import PROFILE as SKID_V5_PROFILE
        v5_reason = ("audited_v5_train_bundle_missing" if target_scope == SKID_V5_PROFILE
                     else "r5_v5_target_scope_mismatch")
        if (target_scope == SKID_V5_PROFILE and isinstance(rollback_receipt, Mapping) and
                rollback_receipt.get("version") == V5_ROLLBACK_VERSION):
            try:
                v5_proof = verify_v5_bundle(asset, validations, bound_assets, rollback_receipt)
                v5_reason = ("audited_v5_train_bundle_verified" if v5_proof.valid else
                             ",".join(v5_proof.reasons))
            except Exception as exc:
                v5_reason = "audited_v5_train_bundle_replay_failed:" + type(exc).__name__
    skid_v6 = is_skid_v6_asset(asset)
    v6_proof = None
    v6_reason = "not_r5_skid_v6"
    if skid_v6:
        from .r5_train_evidence_v6 import ROLLBACK_VERSION as V6_ROLLBACK_VERSION
        from .r5_train_evidence_v6 import verify_train_asset_bundle as verify_v6_bundle
        from tehm.rtl.skid_payload_action_v6 import PROFILE as SKID_V6_PROFILE
        v6_reason = ("audited_v6_train_bundle_missing" if target_scope == SKID_V6_PROFILE
                     else "r5_v6_target_scope_mismatch")
        if (target_scope == SKID_V6_PROFILE and isinstance(rollback_receipt, Mapping) and
                rollback_receipt.get("version") == V6_ROLLBACK_VERSION):
            try:
                v6_proof = verify_v6_bundle(asset, validations, bound_assets, rollback_receipt)
                v6_reason = ("audited_v6_train_bundle_verified" if v6_proof.valid else
                             ",".join(v6_proof.reasons))
            except Exception as exc:
                v6_reason = "audited_v6_train_bundle_replay_failed:" + type(exc).__name__
    skid_v7 = is_skid_v7_asset(asset)
    v7_proof = None
    v7_reason = "not_r5_skid_v7"
    if skid_v7:
        from .r5_train_evidence_v7 import ROLLBACK_VERSION as V7_ROLLBACK_VERSION
        from .r5_train_evidence_v7 import verify_train_asset_bundle as verify_v7_bundle
        from tehm.rtl.skid_payload_action_v7 import PROFILE as SKID_V7_PROFILE
        v7_reason = ("audited_v7_train_bundle_missing" if target_scope == SKID_V7_PROFILE
                     else "r5_v7_target_scope_mismatch")
        if (target_scope == SKID_V7_PROFILE and isinstance(rollback_receipt, Mapping) and
                rollback_receipt.get("version") == V7_ROLLBACK_VERSION):
            try:
                v7_proof = verify_v7_bundle(asset, validations, bound_assets, rollback_receipt)
                v7_reason = ("audited_v7_train_bundle_verified" if v7_proof.valid else
                             ",".join(v7_proof.reasons))
            except Exception as exc:
                v7_reason = "audited_v7_train_bundle_replay_failed:" + type(exc).__name__
    lineages = (set(v7_proof.lineages) if skid_v7 and v7_proof and v7_proof.valid
                else set() if skid_v7 else set(v6_proof.lineages) if v6_proof and v6_proof.valid
                else set() if skid_v6 else
                set(v5_proof.lineages) if v5_proof and v5_proof.valid
                else set() if is_skid_v5_asset(asset) else
                set(v4_proof.lineages) if skid_v4 and v4_proof and v4_proof.valid
                else set() if skid_v4 else
                set(r5_proof.lineages) if skid_v3 and r5_proof and r5_proof.valid
                else set() if skid_v3 else design_ids)
    checks = {
        "schema_valid": schema_valid and not schema_errors,
        "static_valid": bool(validations) and all(
            item.get("static_valid") is True for item in validations),
        "independent_verifier": bool(validations) and all(
            item.get("independent_verifier") is True and
            item.get("oracle_verdict") == "PASS"
            for item in validations),
        "compatibility_verified": bool(bound_assets) and all(
            _binding_is_compatible(item, asset) for item in bound_assets),
        "cross_lineage_verified": len(lineages) >= min_lineages,
        "regression_zero": bool(validations) and all(
            item.get("regression_verdict") == "PASS" and
            not item.get("errors") for item in validations),
        "rollback_verified": (bool(v7_proof and v7_proof.valid and v7_proof.rollback_verified)
                              if skid_v7 else
                              bool(v6_proof and v6_proof.valid and v6_proof.rollback_verified)
                              if skid_v6 else
                              bool(v5_proof and v5_proof.valid and v5_proof.rollback_verified)
                              if is_skid_v5_asset(asset) else
                              bool(v4_proof and v4_proof.valid and v4_proof.rollback_verified)
                              if skid_v4 else
                              bool(r5_proof and r5_proof.valid and r5_proof.rollback_verified)
                              if skid_v3 else
                              bool((rollback_receipt or {}).get("verified") is True)),
    }
    missing = tuple(name for name in ASSET_PROMOTION_GATES if not checks[name])
    evidence = {
        "validation_count": len(validations),
        "binding_count": len(bound_assets),
        "lineages": sorted(lineages),
        "rollback": dict(rollback_receipt)
        if isinstance(rollback_receipt, Mapping) else {},
        "schema_errors": list(schema_errors),
    }
    if skid_v3:
        evidence["design_ids_seen"] = sorted(design_ids)
        evidence["lineage_gate_reason"] = r5_reason
        if r5_proof is not None:
            evidence["r5_lineage_audit_digest"] = r5_proof.lineage_audit_digest
            evidence["r5_rollback_digest"] = r5_proof.rollback_digest
            evidence["r5_evidence_reasons"] = list(r5_proof.reasons)
    if skid_v4:
        evidence["design_ids_seen"] = sorted(design_ids)
        evidence["lineage_gate_reason"] = v4_reason
        if v4_proof is not None:
            evidence["r5_v4_lineage_audit_digest"] = v4_proof.lineage_audit_digest
            evidence["r5_v4_rollback_digest"] = v4_proof.rollback_digest
            evidence["r5_v4_contract_digest"] = v4_proof.shared_contract_digest
            evidence["r5_v4_evidence_reasons"] = list(v4_proof.reasons)
    if is_skid_v5_asset(asset):
        evidence["design_ids_seen"] = sorted(design_ids)
        evidence["lineage_gate_reason"] = v5_reason
        if v5_proof is not None:
            evidence["r5_v5_lineage_audit_digest"] = v5_proof.lineage_audit_digest
            evidence["r5_v5_rollback_digest"] = v5_proof.rollback_digest
            evidence["r5_v5_contract_digest"] = v5_proof.shared_contract_digest
            evidence["r5_v5_evidence_reasons"] = list(v5_proof.reasons)
    if skid_v6:
        evidence["design_ids_seen"] = sorted(design_ids)
        evidence["lineage_gate_reason"] = v6_reason
        if v6_proof is not None:
            evidence["r5_v6_lineage_audit_digest"] = v6_proof.lineage_audit_digest
            evidence["r5_v6_rollback_digest"] = v6_proof.rollback_digest
            evidence["r5_v6_contract_digest"] = v6_proof.shared_contract_digest
            evidence["r5_v6_evidence_reasons"] = list(v6_proof.reasons)
    if skid_v7:
        evidence["design_ids_seen"] = sorted(design_ids)
        evidence["lineage_gate_reason"] = v7_reason
        if v7_proof is not None:
            evidence["r5_v7_lineage_audit_digest"] = v7_proof.lineage_audit_digest
            evidence["r5_v7_rollback_digest"] = v7_proof.rollback_digest
            evidence["r5_v7_contract_digest"] = v7_proof.shared_contract_digest
            evidence["r5_v7_evidence_reasons"] = list(v7_proof.reasons)
    return AssetPromotionReceipt(
        asset_id=str(asset.get("asset_id") or ""), target_scope=target_scope,
        eligible=not missing, checks=checks, missing=missing,
        evidence=evidence)


def _binding_is_compatible(bound: Mapping, asset: Mapping) -> bool:
    if not isinstance(bound, Mapping) or not isinstance(asset, Mapping):
        return False
    provenance = bound.get("provenance") or {}
    if not isinstance(provenance, Mapping):
        return False
    # Fixture manifest.fix supplies the target answer, not transferable
    # localization. It remains executable for diagnostics, never authority.
    from .structural_binding import CONTRACT, verify_structural_binding
    from .guard_binding import CONTRACT as GUARD_CONTRACT, verify_guard_binding
    from .skid_binding_v3 import CONTRACT as SKID_V3_CONTRACT, verify_skid_binding_v3
    from .skid_binding_v7 import CONTRACT as SKID_V7_CONTRACT, verify_skid_binding_v7
    from .skid_binding_v4 import CONTRACT as SKID_V4_CONTRACT, verify_skid_binding_v4
    from .skid_binding_v5 import CONTRACT as SKID_V5_CONTRACT, verify_skid_binding_v5
    from .skid_binding_v6 import CONTRACT as SKID_V6_CONTRACT, verify_skid_binding_v6
    verifiers = {CONTRACT: verify_structural_binding,
                 GUARD_CONTRACT: verify_guard_binding,
                 SKID_V3_CONTRACT: verify_skid_binding_v3,
                 SKID_V4_CONTRACT: verify_skid_binding_v4,
                 SKID_V5_CONTRACT: verify_skid_binding_v5,
                 SKID_V6_CONTRACT: verify_skid_binding_v6,
                 SKID_V7_CONTRACT: verify_skid_binding_v7}
    contract = provenance.get("binding_contract")
    verifier = verifiers.get(contract) if isinstance(contract, str) else None
    if verifier is None or not verifier(bound, asset):
        return False
    compatibility = asset.get("compatibility") or {}
    if not isinstance(compatibility, Mapping):
        return False
    definition = bound.get("definition") or {}
    action = definition.get("action") if isinstance(definition, Mapping) else None
    payload = ((action or {}).get("payload") if isinstance(action, Mapping)
               else None) or {}
    if not isinstance(payload, Mapping):
        return False
    return (payload.get("compatibility_profile") ==
            compatibility.get("compatibility_profile"))


def _bound_provenance(bound: Mapping) -> Mapping:
    provenance = bound.get("provenance") if isinstance(bound, Mapping) else None
    return provenance if isinstance(provenance, Mapping) else {}


__all__ = ["ASSET_PROMOTION_GATES", "evaluate_asset_authority",
           "evaluate_asset_promotion_gates"]
