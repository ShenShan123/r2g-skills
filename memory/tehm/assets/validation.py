"""Static and externally-verifiable checks for asset proposals."""
from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import tempfile
from pathlib import Path

from tehm.rtl.rtl_actions import apply_rtl_action
from tehm.rtl.verilog_parse import parse_verilog

from .receipts import AssetValidationReceipt


def validate_asset_schema(asset: Mapping) -> tuple[bool, tuple[str, ...]]:
    if not isinstance(asset, Mapping):
        return False, ("asset_not_mapping",)
    required = (
        "asset_type", "name", "version", "definition", "input_contract",
        "output_contract", "verifier_contract", "compatibility", "provenance",
    )
    errors = [key for key in required if key not in asset]
    verifier = asset.get("verifier_contract")
    if not isinstance(verifier, Mapping) or verifier.get("independent") is not True:
        errors.append("verifier_contract.independent")
    provenance = asset.get("provenance") or {}
    if isinstance(provenance, Mapping) and provenance.get("generator_is_verifier") is True:
        errors.append("provenance.generator_is_verifier")
    definition = asset.get("definition") or {}
    action = definition.get("action") if isinstance(definition, Mapping) else None
    if not isinstance(action, Mapping) or not isinstance(action.get("payload"), Mapping):
        errors.append("definition.action.payload")
    return not errors, tuple(sorted(set(errors)))


def validate_rtl_rewrite_asset(
    asset: Mapping,
    source: str,
    *,
    verifier: Callable[[str, Mapping], Mapping] | None = None,
    regression_verifier: Callable[[str, Mapping], Mapping] | None = None,
) -> AssetValidationReceipt:
    schema_valid, schema_errors = validate_asset_schema(asset)
    if not schema_valid:
        return AssetValidationReceipt(
            asset_id=asset.get("asset_id"), status="SCHEMA_FAILED",
            schema_valid=False, static_valid=False,
            independent_verifier=False, oracle_verdict=None,
            regression_verdict=None, errors=schema_errors)
    errors: list[str] = []
    static_valid = False
    try:
        before = parse_verilog(source)
        if not before:
            raise ValueError("source has no parseable Verilog module")
        action = dict(asset["definition"]["action"])
        payload = dict(action.get("payload") or {})
        payload["domain"] = action.get("domain") or payload.get("domain")
        payload["compatibility_profile"] = (asset["compatibility"].get(
            "compatibility_profile") or payload.get("compatibility_profile"))
        new_source, edit = apply_rtl_action(source, payload)
        after = parse_verilog(new_source)
        if not after or new_source == source or not edit.get("rewritten"):
            raise ValueError("asset rewrite produced no parser-backed change")
        static_valid = True
    except (TypeError, ValueError, KeyError, NotImplementedError) as exc:
        errors.append(str(exc))
    verifier_contract = asset.get("verifier_contract") or {}
    independent = bool(verifier_contract.get("independent") is True and verifier)
    oracle_verdict = regression_verdict = None
    if static_valid and verifier is not None:
        try:
            result = dict(verifier(new_source, asset))
            oracle_verdict = result.get("verdict")
            if oracle_verdict != "PASS":
                errors.append(
                    f"independent verifier verdict is not PASS: {oracle_verdict!r}")
        except Exception as exc:  # registered oracle failures are receipts, not crashes
            errors.append(f"independent verifier failed: {exc}")
    if static_valid and regression_verifier is not None:
        try:
            result = dict(regression_verifier(new_source, asset))
            regression_verdict = result.get("verdict")
            if regression_verdict != "PASS":
                errors.append(
                    f"regression verifier verdict is not PASS: {regression_verdict!r}")
        except Exception as exc:
            errors.append(f"regression verifier failed: {exc}")
    status = "SHADOW_STATIC_PASS" if static_valid and not errors else "VALIDATION_FAILED"
    return AssetValidationReceipt(
        asset_id=asset.get("asset_id"), status=status,
        schema_valid=True, static_valid=static_valid,
        independent_verifier=independent,
        oracle_verdict=oracle_verdict, regression_verdict=regression_verdict,
        errors=tuple(errors))


def validate_rtl_asset_project(asset: Mapping, project: Path | str, *, oracle) -> AssetValidationReceipt:
    """Run a registered external RTL oracle against one asset proposal.

    The project manifest supplies target and frozen-regression testbenches.  A
    missing oracle or missing testbench is an explicit failed receipt; this
    helper never treats static parse success as promotion evidence.
    """
    project = Path(project)
    manifest_path = project / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text())
        rtl_files = sorted((project / "rtl").glob("*.v"))
        if not rtl_files:
            raise ValueError("project has no rtl/*.v")
        source = rtl_files[0].read_text()
        schema_valid, schema_errors = validate_asset_schema(asset)
        if not schema_valid:
            return AssetValidationReceipt(
                asset_id=asset.get("asset_id"), status="SCHEMA_FAILED",
                schema_valid=False, static_valid=False,
                independent_verifier=False, oracle_verdict=None,
                regression_verdict=None, errors=schema_errors)
        action = dict(asset["definition"]["action"])
        payload = dict(action.get("payload") or {})
        payload["domain"] = action.get("domain") or payload.get("domain")
        payload["compatibility_profile"] = asset["compatibility"].get(
            "compatibility_profile")
        fixed_source, edit = apply_rtl_action(source, payload)
        if fixed_source == source or not edit.get("rewritten"):
            raise ValueError("asset produced no parser-backed rewrite")
        if not parse_verilog(fixed_source):
            raise ValueError("asset output is not parseable")
        if oracle is None or not getattr(oracle, "available", False):
            return AssetValidationReceipt(
                asset_id=asset.get("asset_id"), status="ORACLE_UNAVAILABLE",
                schema_valid=True, static_valid=True,
                independent_verifier=False, oracle_verdict="UNKNOWN",
                regression_verdict="UNKNOWN", errors=("external oracle unavailable",))
        with tempfile.TemporaryDirectory(prefix="tehm_asset_") as temp:
            fixed_path = Path(temp) / rtl_files[0].name
            fixed_path.write_text(fixed_source)
            other = [path for path in rtl_files if path.name != rtl_files[0].name]
            verification = manifest.get("verification") or {}
            target = project / verification.get("target_test", "tb/tb_handshake.v")
            regression = project / verification.get("frozen_regression", "tb/tb_basic.v")
            result = oracle.verify(
                [fixed_path, *other],
                target_tb=target if target.exists() else None,
                regression_tb=regression if regression.exists() else None)
        return AssetValidationReceipt(
            asset_id=asset.get("asset_id"), status=(
                "SHADOW_ORACLE_PASS" if result.get("verdict") == "PASS"
                else "ORACLE_FAILED"),
            schema_valid=True, static_valid=True,
            independent_verifier=True,
            oracle_verdict=result.get("verdict"),
            regression_verdict=(result.get("regression") or {}).get("verdict"),
            errors=() if result.get("verdict") == "PASS" else (
                str(result.get("reason") or "oracle failed"),))
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        return AssetValidationReceipt(
            asset_id=asset.get("asset_id"), status="VALIDATION_FAILED",
            schema_valid=True, static_valid=False,
            independent_verifier=False, oracle_verdict=None,
            regression_verdict=None, errors=(str(exc),))


__all__ = ["validate_asset_schema", "validate_rtl_asset_project",
           "validate_rtl_rewrite_asset"]
