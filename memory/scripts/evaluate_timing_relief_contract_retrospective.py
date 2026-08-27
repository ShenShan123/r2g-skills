#!/usr/bin/env python3
"""Score the V4 cohort under a typed utility contract, retrospectively only.

This report is design evidence for the next prospective split.  It never
rewrites the V4 observation chain, staging database, canonical memory, or raw
Pareto verdict.  Contract thresholds must be frozen before the prospective
cohort is executed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.parametric.shadow_campaign import digest  # noqa: E402
from tehm.physical.utility_contracts import (  # noqa: E402
    contract_action,
    evaluate_observed_contract,
    timing_relief_budgeted_v1,
    utility_contract_digest,
)


def _ppa(metrics: dict) -> dict:
    return {"summary": {
        "timing": {"setup_wns": metrics.get("wns_ns"),
                   "setup_tns": metrics.get("tns_ns")},
        "area": {"design_area_um2": metrics.get("area_um2")},
        "power": {"total_power_w": metrics.get("power_w")},
    }}


def _read_rows(path: Path) -> list[dict]:
    rows = []
    with path.open() as stream:
        for line in stream:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--observations", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args(argv)
    contract = timing_relief_budgeted_v1()
    action = contract_action(contract)
    results = []
    for row in _read_rows(args.observations):
        if row.get("classification") != "ELIGIBLE_POSITIVE":
            continue
        before = row.get("before") or {}
        after = row.get("after") or {}
        result = evaluate_observed_contract(
            contract=contract, action=action,
            before_ppa=_ppa(before.get("ppa_metrics") or {}),
            after_ppa=_ppa(after.get("ppa_metrics") or {}),
            checks=after.get("checks") or {}, obligation_coverage=1.0)
        results.append({
            "lineage_id": row.get("lineage_id"),
            "case_id": row.get("case_id"),
            "contract_status": result.get("status"),
            "contract_eligible": result.get("contract_eligible", False),
            "contract_harmful": result.get("contract_harmful"),
            "failures": result.get("failures", []),
            "abstain_reasons": result.get("abstain_reasons", []),
            "raw_pareto": result.get("raw_pareto"),
            "observed_deltas": result.get("observed_deltas"),
            "observed_relative": result.get("observed_relative"),
        })
    eligible = sum(row["contract_status"] == "PASS" for row in results)
    failed = sum(row["contract_status"] == "FAIL" for row in results)
    abstained = sum(row["contract_status"] == "ABSTAINED" for row in results)
    raw_harmful = sum((row.get("raw_pareto") or {}).get("verdict") == "HARMFUL"
                      for row in results)
    report = {
        "version": "timing-relief-budgeted-v1-retrospective-v1",
        "status": "RETROSPECTIVE_DESIGN_EVIDENCE",
        "contract": contract,
        "contract_digest": utility_contract_digest(contract),
        "action": action,
        "source_observations": str(args.observations.resolve()),
        "source_observations_sha256": hashlib.sha256(
            args.observations.read_bytes()).hexdigest(),
        "sample_count": len(results),
        "contract_pass_count": eligible,
        "contract_fail_count": failed,
        "contract_abstain_count": abstained,
        "contract_selected_rate": eligible / len(results) if results else None,
        "contract_selected_harmful_rate": 0.0 if eligible else None,
        "raw_pareto_harmful_count": raw_harmful,
        "raw_pareto_harmful_rate": raw_harmful / len(results) if results else None,
        "results": results,
        "prospective_validation_required": True,
        "promotion_eligible": False,
        "canonical_memory_mutation": "none",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "status": report["status"], "sample_count": len(results),
        "contract_pass_count": eligible, "contract_fail_count": failed,
        "raw_pareto_harmful_rate": report["raw_pareto_harmful_rate"],
        "promotion_eligible": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
