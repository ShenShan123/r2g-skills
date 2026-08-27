"""Source-bound semantic failure witnesses for ORFS transitions.

The physical ORFS oracle is intentionally strict: both arms must carry the
complete 14-check receipt.  That contract alone cannot describe a repair whose
before arm is semantically unacceptable while still producing a complete
physical run.  This module supplies a small, executable semantic contract that
is evaluated from the frozen project inputs, never from caller-provided
booleans or copied verdicts.

The first contract is a pre-registered numeric bound over ``config.mk``.  It
is useful for resource-budget experiments such as ``CORE_UTILIZATION <= 65``;
the physical reports remain independent evidence and are still required by
``assess_full_oracle``.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from pathlib import Path

from tehm.adapters.r2g_evidence import parse_config_mk
from tehm.ids import stable_dumps

SEMANTIC_ORACLE_VERSION = "orfs-semantic-oracle-v1"
_KINDS = frozenset({"config_numeric_bound"})
_OPERATORS = frozenset({"le", "lt", "ge", "gt", "eq"})


class SemanticOracleError(ValueError):
    """Raised when a semantic oracle contract is malformed."""


def normalize_spec(spec: Mapping) -> dict:
    """Validate and canonicalize one source-frozen semantic oracle spec."""
    if not isinstance(spec, Mapping):
        raise SemanticOracleError("semantic oracle spec must be a mapping")
    if str(spec.get("version") or "") != SEMANTIC_ORACLE_VERSION:
        raise SemanticOracleError("semantic oracle version mismatch")
    kind = str(spec.get("kind") or "")
    if kind not in _KINDS:
        raise SemanticOracleError(f"unsupported semantic oracle kind: {kind!r}")
    key = str(spec.get("config_key") or "").strip()
    if not key or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for ch in key):
        raise SemanticOracleError("config_key must be an uppercase config assignment")
    operator = str(spec.get("operator") or "")
    if operator not in _OPERATORS:
        raise SemanticOracleError(f"unsupported semantic oracle operator: {operator!r}")
    try:
        threshold = float(spec["threshold"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SemanticOracleError("semantic oracle threshold must be numeric") from exc
    if not math.isfinite(threshold):
        raise SemanticOracleError("semantic oracle threshold must be finite")
    # Only these fields are executable contract.  Free-form annotations are
    # deliberately excluded from the normalized digest so a label cannot
    # change the oracle semantics or transition identity.
    return {
        "version": SEMANTIC_ORACLE_VERSION,
        "kind": kind,
        "config_key": key,
        "operator": operator,
        "threshold": threshold,
    }


def load_spec(path: Path | str) -> dict:
    """Read and validate a JSON semantic oracle contract."""
    path = Path(path).resolve()
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SemanticOracleError(f"cannot read semantic oracle: {path}") from exc
    return normalize_spec(value)


def _config_sha256(project: Path) -> str | None:
    path = Path(project).resolve() / "constraints" / "config.mk"
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _compare(value: float, operator: str, threshold: float) -> bool:
    return {
        "le": value <= threshold,
        "lt": value < threshold,
        "ge": value >= threshold,
        "gt": value > threshold,
        "eq": value == threshold,
    }[operator]


def evaluate(project: Path | str, spec: Mapping) -> dict:
    """Evaluate a semantic contract directly against a materialized project."""
    normalized = normalize_spec(spec)
    project = Path(project).resolve()
    config_path = project / "constraints" / "config.mk"
    try:
        config = parse_config_mk(config_path.read_text(errors="replace"))
    except OSError:
        config = {}
    raw = config.get(normalized["config_key"])
    try:
        observed = float(raw)
    except (TypeError, ValueError):
        observed = None
    if observed is None or not math.isfinite(observed):
        verdict, reason = "UNKNOWN", "config_value_missing_or_non_numeric"
    else:
        verdict = ("PASS" if _compare(observed, normalized["operator"],
                                      normalized["threshold"]) else "FAIL")
        reason = ""
    payload = {
        "version": SEMANTIC_ORACLE_VERSION,
        "spec": normalized,
        "project": str(project),
        "config_key": normalized["config_key"],
        "observed": observed,
        "verdict": verdict,
        "reason": reason,
        "config_sha256": _config_sha256(project),
    }
    payload["receipt_sha256"] = hashlib.sha256(
        stable_dumps(payload).encode()).hexdigest()
    return payload


def evaluate_pair(before: Path | str, after: Path | str, spec: Mapping) -> dict:
    """Evaluate both arms and derive a fail→pass semantic witness."""
    normalized = normalize_spec(spec)
    before_receipt = evaluate(before, normalized)
    after_receipt = evaluate(after, normalized)
    before_verdict = before_receipt["verdict"]
    after_verdict = after_receipt["verdict"]
    original_failure = (
        "REMOVED" if before_verdict == "FAIL" and after_verdict == "PASS"
        else "PRESENT" if before_verdict == "FAIL" else "UNKNOWN")
    pair = {
        "version": SEMANTIC_ORACLE_VERSION,
        "spec": normalized,
        "before": before_receipt,
        "after": after_receipt,
        "original_failure": original_failure,
        "verdict": ("PASS" if after_verdict == "PASS" else
                     "FAIL" if after_verdict == "FAIL" else "UNKNOWN"),
    }
    pair["pair_sha256"] = hashlib.sha256(
        stable_dumps(pair).encode()).hexdigest()
    return pair


__all__ = [
    "SEMANTIC_ORACLE_VERSION", "SemanticOracleError", "evaluate",
    "evaluate_pair", "load_spec", "normalize_spec",
]
