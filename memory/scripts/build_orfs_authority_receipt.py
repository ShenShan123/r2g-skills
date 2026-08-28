#!/usr/bin/env python3
"""Build a fail-closed authority receipt for an ORFS canonical import.

This command only reads the observation chain, staging database, and protected
canonical snapshot.  It never imports or mutates canonical memory.  A later
``promote_orfs_batch_observations.py`` invocation may consume the receipt only
when the decision is ``ALLOW_CANONICAL_IMPORT`` and all six gates are true.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

MEMORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MEMORY_ROOT))

from tehm.batch_lane import (  # noqa: E402
    CANONICAL_IMPORT_AUTHORITY_VERSION,
    PROMOTION_GATES,
    canonical_case_selection_digest,
    read_external_observations,
    sqlite_snapshot,
)
from tehm.lifecycle.promotion_gates import evaluate_promotion_gates  # noqa: E402


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def derive_gate_inputs(rows: list[dict], case_ids: list[str]) -> tuple[dict, dict]:
    """Derive available rule gates from preserved observation payloads.

    This helper intentionally returns only gates for which the external
    receipts contain the required measurement.  It never turns an absent
    rollback/registry/calibration record into ``False``.  A singleton eligible
    lineage is a measured cross-lineage failure (``0.0``), because it proves
    only that transfer evidence is insufficient rather than unobserved.
    """
    requested = {str(case_id) for case_id in case_ids}
    selected = [row for row in rows if str(row.get("case_id")) in requested]
    eligible = [row for row in selected
                if row.get("split") == "support" and
                row.get("classification") == "ELIGIBLE_POSITIVE" and
                row.get("learner_eligible") is True]
    details = {
        "selected_case_ids": sorted(str(row.get("case_id")) for row in selected),
        "support_case_ids": sorted(
            str(row.get("case_id")) for row in selected
            if row.get("split") == "support"),
        "eligible_positive_case_ids": sorted(
            str(row.get("case_id")) for row in eligible),
        "eligible_lineages": sorted({str(row.get("lineage_id")) for row in eligible
                                      if row.get("lineage_id") not in (None, "")}),
        "source": "external_observation_receipts",
    }
    derived: dict = {}
    obligation_values: list[float] = []
    utility_verdicts: list[str] = []
    rollback_values: list[bool] = []
    conformal_values: list[float] = []
    for row in eligible:
        record = row.get("record") or {}
        verification = record.get("verification") or {}
        coverage = verification.get("obligation_coverage")
        try:
            coverage = float(coverage)
        except (TypeError, ValueError):
            coverage = None
        if coverage is not None and math.isfinite(coverage):
            obligation_values.append(coverage)
        delta = record.get("observation_delta") or {}
        utility = str(delta.get("utility_verdict") or "").upper()
        # ``PARETO_SAFE`` is the canonical physical utility verdict emitted by
        # the ORFS adapter.  Keep the legacy SUPPORT spelling for older
        # fixtures, but treat both as measured non-harmful outcomes.
        if utility in {"HARMFUL", "REGRESSION", "PARETO_SAFE", "SUPPORT", "NEUTRAL"}:
            utility_verdicts.append(utility)
        rollback = record.get("rollback_receipt")
        if isinstance(rollback, dict) and isinstance(rollback.get("verified"), bool):
            rollback_values.append(rollback["verified"])
        conformal = record.get("conformal") or verification.get("conformal")
        if isinstance(conformal, dict):
            value = conformal.get("coverage")
            try:
                value = float(value)
            except (TypeError, ValueError):
                value = None
            if value is not None and math.isfinite(value):
                conformal_values.append(value)
    if eligible and len(obligation_values) == len(eligible) and obligation_values:
        derived["obligation_coverage"] = min(obligation_values)
    if eligible and len(utility_verdicts) == len(eligible) and utility_verdicts:
        derived["harmful_rate"] = sum(
            verdict in {"HARMFUL", "REGRESSION"} for verdict in utility_verdicts
        ) / len(utility_verdicts)
    if eligible:
        derived["cross_lineage_te"] = (
            1.0 if len(details["eligible_lineages"]) >= 2 else 0.0)
    if rollback_values and len(rollback_values) == len(eligible):
        derived["rollback_verified"] = all(rollback_values)
    if conformal_values:
        derived["conformal_coverage"] = sum(conformal_values) / len(conformal_values)
    details.update({
        "obligation_coverages": obligation_values,
        "utility_verdicts": utility_verdicts,
        "rollback_observations": len(rollback_values),
        "conformal_coverages": conformal_values,
    })
    return derived, details


def build_receipt(*, observations: Path, staging_db: Path, canonical_db: Path,
                  campaign_id: str, rule_id: str, target_scope: str,
                  case_ids: list[str], gate_inputs: dict | None = None,
                  status_version: int | None = None) -> dict:
    """Create an independently bound receipt without any database writes.

    When ``gate_inputs`` is omitted, measurements are derived from the selected
    external observation receipts.  The optional argument remains only for
    replaying older deterministic fixtures; production CLI calls never accept
    caller-supplied gate values.
    """
    rows = read_external_observations(observations)
    available = {str(row["case_id"]) for row in rows}
    missing_cases = sorted(set(case_ids) - available)
    if missing_cases:
        raise ValueError(f"authority case_ids absent from observation chain: {missing_cases}")
    if gate_inputs is None:
        gate_inputs, gate_derivation = derive_gate_inputs(rows, case_ids)
    else:
        gate_inputs = dict(gate_inputs)
        gate_derivation = {
            "source": "legacy_fixture_override",
            "warning": "caller-supplied gate inputs are not independent evidence",
        }
    gates = evaluate_promotion_gates(gate_inputs, strict=True)
    decision = "ALLOW_CANONICAL_IMPORT" if gates["eligible"] else "DENY_CANONICAL_IMPORT"
    return {
        "version": CANONICAL_IMPORT_AUTHORITY_VERSION,
        "authority_kind": "independent_orfs_promotion_authority",
        "campaign_id": campaign_id,
        "decision": decision,
        "promotion_attempted": False,
        "rule_id": rule_id,
        "target_scope": target_scope,
        "status_version": status_version,
        "case_ids": sorted(case_ids),
        "canonical_memory_mutation": "none",
        "promotion_gates": {**gate_inputs, **gates["checks"]},
        "gate_evaluation": gates,
        "gate_derivation": gate_derivation,
        "bindings": {
            "observations_sha256": _sha(observations),
            "staging_db_sha256": _sha(staging_db),
            "canonical_db_sha256_before": _sha(canonical_db),
            "case_selection_sha256": canonical_case_selection_digest(case_ids),
        },
        "snapshots": {
            "staging": sqlite_snapshot(staging_db),
            "canonical_before": sqlite_snapshot(canonical_db),
        },
        "observation_count": len(rows),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--observations", type=Path, required=True)
    ap.add_argument("--staging-db", type=Path, required=True)
    ap.add_argument("--canonical-db", type=Path, required=True)
    ap.add_argument("--campaign-id", required=True)
    ap.add_argument("--rule-id", required=True)
    ap.add_argument("--target-scope", default="route")
    ap.add_argument("--status-version", type=int, default=None)
    ap.add_argument("--case-id", dest="case_ids", action="append", required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args(argv)
    receipt = build_receipt(
        observations=args.observations.resolve(), staging_db=args.staging_db.resolve(),
        canonical_db=args.canonical_db.resolve(), campaign_id=args.campaign_id,
        rule_id=args.rule_id, target_scope=args.target_scope,
        case_ids=args.case_ids,
        status_version=args.status_version)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"decision": receipt["decision"],
                      "promotion_attempted": False,
                      "promotion_gates": receipt["gate_evaluation"]},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
