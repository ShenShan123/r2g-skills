"""Narrow P13 counterfactual completeness without strict-signoff authority.

The generic ``signoff_result`` remains the production-facing field.  This
receipt instead records whether a fixed-constraint P12 comparison observed all
of its preregistered physical checks.  It can make a reason-specific shadow
counterfactual complete, but can never certify strict signoff or production.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping

from tehm.ids import stable_dumps


COUNTERFACTUAL_ORACLE_VERSION = "p13-fixed-constraint-counterfactual-v1"
COUNTERFACTUAL_SCOPE = "fixed_constraint_counterfactual"
COUNTERFACTUAL_CHECKS = (
    "route", "drc", "lvs", "rcx", "timing", "manifest_binding",
)
_VERDICTS = frozenset({"PASS", "FAIL", "UNKNOWN"})


class CounterfactualOracleError(ValueError):
    """A scoped counterfactual oracle receipt is malformed."""


def fixed_constraint_check_verdicts(reports: Mapping) -> dict[str, str]:
    """Project R2G reports into the narrow fixed-constraint contract."""
    if not isinstance(reports, Mapping):
        raise CounterfactualOracleError("counterfactual reports must be an object")

    def status(name: str, accepted: set[str]) -> str:
        report = reports.get(name)
        if not isinstance(report, Mapping) or not report:
            return "UNKNOWN"
        return "PASS" if report.get("status") in accepted else "FAIL"

    route = status("route", {"clean", "complete", "pass"})
    # Full-deck DRC and executed LVS are required.  clean_beol/skipped remain
    # useful elsewhere but do not satisfy this counterfactual contract.
    drc = status("drc", {"clean"})
    lvs = status("lvs", {"clean"})
    rcx = status("rcx", {"complete", "pass"})
    timing_report = reports.get("timing")
    if not isinstance(timing_report, Mapping) or not timing_report:
        timing = "UNKNOWN"
    else:
        timing = "PASS" if (
            timing_report.get("tier") == "clean" or
            timing_report.get("status") == "clean") else "FAIL"

    manifest = reports.get("signoff_manifest")
    if not isinstance(manifest, Mapping) or not manifest:
        manifest_binding = "UNKNOWN"
    else:
        entries = manifest.get("reports")
        confirming = manifest.get("confirming_run")
        capability = manifest.get("platform_capability")
        constraint = manifest.get("constraint")
        missing = manifest.get("strict_missing")
        allowed_missing = {
            "constraint: fmax_search winner (reports/fmax_search.json status=ok)"
        }
        def sha256_text(value: object) -> bool:
            return (type(value) is str and len(value) == 64 and
                    all(char in "0123456789abcdefABCDEF" for char in value))

        strict_state_consistent = (
            (manifest.get("strict_clean") is True and missing == []) or
            (manifest.get("strict_clean") is False and
             isinstance(missing, list) and set(missing) == allowed_missing))
        bound = (
            isinstance(entries, Mapping) and
            all(isinstance(entries.get(name), Mapping) and
                entries[name].get("present") is True and
                sha256_text(entries[name].get("sha256"))
                for name in ("route.json", "drc.json", "lvs.json",
                             "rcx.json", "timing_check.json")) and
            entries["route.json"].get("status") == (reports.get("route") or {}).get("status") and
            entries["drc.json"].get("status") == (reports.get("drc") or {}).get("status") and
            entries["lvs.json"].get("status") == (reports.get("lvs") or {}).get("status") and
            entries["rcx.json"].get("status") == (reports.get("rcx") or {}).get("status") and
            entries["timing_check.json"].get("tier") == (reports.get("timing") or {}).get("tier") and
            isinstance(confirming, Mapping) and
            confirming.get("consensus") is True and
            isinstance(capability, Mapping) and
            capability.get("strict_signoff_ready") is True and
            isinstance(constraint, Mapping) and
            constraint.get("final_timing_tier") == "clean" and
            sha256_text(constraint.get("sdc_sha256")) and
            strict_state_consistent)
        manifest_binding = "PASS" if bound else "FAIL"
    return {
        "route": route, "drc": drc, "lvs": lvs, "rcx": rcx,
        "timing": timing, "manifest_binding": manifest_binding,
    }


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def build_counterfactual_oracle_receipt(
        checks: Mapping[str, str], *, evidence_digest: str) -> dict:
    """Build a content-addressed, shadow-only fixed-constraint receipt."""
    if not isinstance(checks, Mapping) or set(checks) != set(COUNTERFACTUAL_CHECKS):
        raise CounterfactualOracleError(
            "counterfactual oracle checks must cover the fixed contract")
    normalized = {name: checks[name] for name in COUNTERFACTUAL_CHECKS}
    if any(value not in _VERDICTS for value in normalized.values()):
        raise CounterfactualOracleError("counterfactual oracle verdict is invalid")
    if (type(evidence_digest) is not str or not evidence_digest.startswith("sha256:")
            or len(evidence_digest) <= len("sha256:")):
        raise CounterfactualOracleError(
            "counterfactual oracle evidence_digest must be a sha256 digest")
    complete = "UNKNOWN" not in set(normalized.values())
    payload = {
        "version": COUNTERFACTUAL_ORACLE_VERSION,
        "scope": COUNTERFACTUAL_SCOPE,
        "checks": normalized,
        "evidence_digest": evidence_digest,
        "complete": complete,
        "p13_counterfactual_admissible": complete,
        "evaluation_only": True,
        "strict_signoff_claim": False,
        "production_eligible": False,
        "canonical_memory_mutation": "none",
    }
    payload["receipt_digest"] = _digest(payload)
    return payload


def replay_counterfactual_oracle_receipt(value: object) -> dict:
    """Recompute every derived field and digest; reject authority expansion."""
    if not isinstance(value, Mapping):
        raise CounterfactualOracleError(
            "counterfactual oracle receipt must be an object")
    required = {
        "version", "scope", "checks", "evidence_digest", "complete",
        "p13_counterfactual_admissible", "evaluation_only",
        "strict_signoff_claim", "production_eligible",
        "canonical_memory_mutation", "receipt_digest",
    }
    if not required <= set(value):
        raise CounterfactualOracleError(
            "counterfactual oracle receipt is missing fields")
    if value.get("version") != COUNTERFACTUAL_ORACLE_VERSION:
        raise CounterfactualOracleError("counterfactual oracle version mismatch")
    if value.get("scope") != COUNTERFACTUAL_SCOPE:
        raise CounterfactualOracleError("counterfactual oracle scope mismatch")
    rebuilt = build_counterfactual_oracle_receipt(
        value.get("checks"), evidence_digest=value.get("evidence_digest"))
    if dict(value) != rebuilt:
        raise CounterfactualOracleError(
            "counterfactual oracle receipt replay mismatch")
    return rebuilt


def counterfactual_oracle_complete(receipt: object) -> bool:
    """Accept strict complete receipts or the narrow typed shadow contract."""
    if (getattr(receipt, "evaluation_only", None) is not True or
            getattr(receipt, "metadata", {}).get("oracle_available") is not True or
            getattr(receipt, "compile_result", "UNKNOWN") == "UNKNOWN" or
            getattr(receipt, "functional_result", "UNKNOWN") == "UNKNOWN" or
            getattr(receipt, "outcome", "UNKNOWN") == "UNKNOWN"):
        return False
    if getattr(receipt, "signoff_result", None) not in {None, "UNKNOWN"}:
        return True
    raw = (getattr(receipt, "metadata", {}).get("oracle_metadata") or {}).get(
        "counterfactual_oracle")
    try:
        replayed = replay_counterfactual_oracle_receipt(raw)
    except (CounterfactualOracleError, TypeError, ValueError):
        return False
    return (replayed["complete"] is True and
            replayed["p13_counterfactual_admissible"] is True)


__all__ = [
    "COUNTERFACTUAL_ORACLE_VERSION", "COUNTERFACTUAL_SCOPE",
    "COUNTERFACTUAL_CHECKS", "CounterfactualOracleError",
    "fixed_constraint_check_verdicts",
    "build_counterfactual_oracle_receipt",
    "replay_counterfactual_oracle_receipt",
    "counterfactual_oracle_complete",
]
