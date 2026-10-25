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


def _ineligible(asset, target_scope, reason):
    return AssetPromotionReceipt(
        asset_id=str(asset.get("asset_id") or ""), target_scope=target_scope,
        eligible=False, checks={name: False for name in ASSET_PROMOTION_GATES},
        missing=ASSET_PROMOTION_GATES,
        evidence={"reason": reason, "lineages": [], "design_ids_are_not_lineages": True})


def _retired_pending(asset, target_scope):
    """Retired R5 generations (skid v1-v9, I2C v2) are never eligible in the main tree."""
    from .retired import retired_generation, retired_reason
    generation = retired_generation(asset)
    return _ineligible(asset, target_scope, retired_reason(generation)) if generation else None


def _i2c_v3_pending(asset, target_scope):
    from .i2c_binding_v3 import is_i2c_v3_asset
    if not is_i2c_v3_asset(asset):
        return None
    return _ineligible(asset, target_scope, "i2c_v3_requires_strict_raw_train_authority")


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
    pending = _retired_pending(asset, target_scope) or _i2c_v3_pending(asset, target_scope)
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
    pending = _retired_pending(asset, target_scope)
    if pending is not None:
        return pending
    from .i2c_binding_v3 import is_i2c_v3_asset
    if is_i2c_v3_asset(asset):
        from .r5_train_evidence_i2c_v3 import evaluate as evaluate_i2c_v3
        return evaluate_i2c_v3(asset, validation_receipts=validation_receipts,
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
    lineages = {
        str(_bound_provenance(item).get("bound_design") or
            _bound_provenance(item).get("bound_project") or "")
        for item in bound_assets
        if _bound_provenance(item).get("bound_design") or
        _bound_provenance(item).get("bound_project")
    }
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
        "rollback_verified": bool((rollback_receipt or {}).get("verified") is True),
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
    verifiers = {CONTRACT: verify_structural_binding,
                 GUARD_CONTRACT: verify_guard_binding}
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
