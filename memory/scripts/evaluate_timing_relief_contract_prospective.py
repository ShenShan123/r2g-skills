#!/usr/bin/env python3
"""Evaluate a frozen typed utility contract on a prospective ORFS cohort.

This is an external evidence report only.  It never changes raw Pareto
verdicts, staging contents, canonical memory, or promotion authority.
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

from tehm.physical.utility_contracts import (  # noqa: E402
    contract_action,
    evaluate_observed_contract,
    timing_relief_budgeted_v1,
    timing_relief_budgeted_v2_50_to_45,
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
    return [json.loads(line) for line in path.read_text().splitlines()
            if line.strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--observations", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--contract-spec", type=Path, default=None,
                    help="optional typed contract JSON; defaults to V1")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args(argv)
    contract = _load_contract(args.contract_spec)
    action = contract_action(contract)
    results = []
    for row in _read_rows(args.observations):
        if row.get("classification") != "ELIGIBLE_POSITIVE":
            continue
        before, after = row.get("before") or {}, row.get("after") or {}
        result = evaluate_observed_contract(
            contract=contract, action=action,
            before_ppa=_ppa(before.get("ppa_metrics") or {}),
            after_ppa=_ppa(after.get("ppa_metrics") or {}),
            checks=after.get("checks") or {}, obligation_coverage=1.0)
        results.append({
            "lineage_id": row.get("lineage_id"),
            "case_id": row.get("case_id"),
            "split": row.get("split"),
            "contract_status": result.get("status"),
            "contract_eligible": result.get("contract_eligible", False),
            "contract_harmful": result.get("contract_harmful"),
            "failures": result.get("failures", []),
            "abstain_reasons": result.get("abstain_reasons", []),
            "raw_pareto": result.get("raw_pareto"),
            "observed_deltas": result.get("observed_deltas"),
            "observed_relative": result.get("observed_relative"),
        })
    passed = sum(row["contract_status"] == "PASS" for row in results)
    failed = sum(row["contract_status"] == "FAIL" for row in results)
    abstained = sum(row["contract_status"] == "ABSTAINED" for row in results)
    raw_harmful = sum((row.get("raw_pareto") or {}).get("verdict") == "HARMFUL"
                      for row in results)
    observations_digest = hashlib.sha256(args.observations.read_bytes()).hexdigest()
    manifest_digest = hashlib.sha256(args.manifest.read_bytes()).hexdigest()
    report = {
        "version": "timing-relief-budgeted-v1-prospective-v1",
        "status": "PROSPECTIVE_EXTERNAL_EVIDENCE",
        "contract": contract,
        "contract_digest": utility_contract_digest(contract),
        "action": action,
        "source_observations": str(args.observations.resolve()),
        "source_observations_sha256": observations_digest,
        "source_manifest": str(args.manifest.resolve()),
        "source_manifest_sha256": manifest_digest,
        "sample_count": len(results),
        "contract_pass_count": passed,
        "contract_fail_count": failed,
        "contract_abstain_count": abstained,
        "contract_selected_rate": passed / len(results) if results else None,
        "contract_selected_harmful_rate": 0.0 if passed else None,
        "raw_pareto_harmful_count": raw_harmful,
        "raw_pareto_harmful_rate": raw_harmful / len(results) if results else None,
        "results": results,
        "promotion_eligible": False,
        "canonical_memory_mutation": "none",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "status": report["status"], "sample_count": len(results),
        "contract_pass_count": passed, "contract_fail_count": failed,
        "contract_abstain_count": abstained,
        "raw_pareto_harmful_rate": report["raw_pareto_harmful_rate"],
        "promotion_eligible": False,
    }, sort_keys=True))
    return 0


def _load_contract(path: Path | None) -> dict:
    if path is None:
        return timing_relief_budgeted_v1()
    payload = json.loads(path.read_text())
    contract_id = payload.get("contract_id")
    factories = {
        "TIMING_RELIEF_BUDGETED_V1": timing_relief_budgeted_v1,
        "TIMING_RELIEF_BUDGETED_V2_50_TO_45": timing_relief_budgeted_v2_50_to_45,
    }
    factory = factories.get(contract_id)
    if factory is None:
        raise ValueError(f"unsupported typed contract: {contract_id}")
    contract = factory()
    if payload.get("utility_contract_digest") != utility_contract_digest(contract):
        raise ValueError("contract spec digest mismatch")
    return contract


if __name__ == "__main__":
    raise SystemExit(main())
