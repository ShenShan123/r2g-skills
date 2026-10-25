"""Typed asset contracts and lifecycle vocabulary."""
from __future__ import annotations

ASSET_TYPES = frozenset({
    "REPAIR_OPERATOR", "RTL_REWRITE_TEMPLATE", "FLOW_CONFIG_TRANSFORM",
    "DIAGNOSTIC_EXTRACTOR", "STRUCTURAL_PREDICATE",
    "VERIFICATION_OBLIGATION", "TOOL_ROUTING_POLICY", "LOCALIZATION_PROCEDURE",
    "EXECUTION_MACRO", "AGENT_ROLE_PROFILE",
})
ASSET_STATUSES = frozenset({
    "draft", "shadow", "candidate", "promoted", "demoted",
    "quarantined", "retired",
})
ASSET_STATUS_TRANSITIONS = {
    "draft": frozenset({"shadow", "quarantined", "retired"}),
    "shadow": frozenset({"candidate", "quarantined", "retired"}),
    "candidate": frozenset({"promoted", "demoted", "quarantined", "retired"}),
    "promoted": frozenset({"demoted", "quarantined", "retired"}),
    "demoted": frozenset({"shadow", "candidate", "quarantined", "retired"}),
    "quarantined": frozenset({"shadow", "retired"}),
    "retired": frozenset(),
}


def validate_asset_type(asset_type: str) -> str:
    value = str(asset_type)
    if value not in ASSET_TYPES:
        raise ValueError(f"unknown asset type: {asset_type!r}")
    return value


def validate_asset_status(status: str) -> str:
    value = str(status)
    if value not in ASSET_STATUSES:
        raise ValueError(f"unknown asset status: {status!r}")
    return value


def validate_contract(name: str, contract: object) -> dict:
    if not isinstance(contract, dict) or not contract:
        raise ValueError(f"{name} must be a non-empty object")
    return dict(contract)


def validate_independent_verifier(verifier_contract: dict,
                                  provenance: dict | None = None) -> None:
    verifier_contract = validate_contract("verifier_contract", verifier_contract)
    provenance = dict(provenance or {})
    if verifier_contract.get("independent") is not True:
        raise ValueError("verifier_contract.independent must be true")
    if provenance.get("generator_is_verifier") is True:
        raise ValueError("asset generator cannot be the sole verifier")


__all__ = ["ASSET_TYPES", "ASSET_STATUSES", "ASSET_STATUS_TRANSITIONS",
           "validate_asset_type", "validate_asset_status", "validate_contract",
           "validate_independent_verifier"]
